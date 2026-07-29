"""通用 Agent Link v1 WebSocket 适配器。

第三方 Agent 负责实现同一字段契约并把 MeaPet 上报的 Tool 加入自身
Agent Loop；MeaPet 不再为每个 Agent 品牌转换聊天或工具协议。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, AsyncIterator, Mapping

from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.link_protocol import (
    AgentLinkFrame,
    AgentLinkProtocolError,
    make_agent_link_frame,
    normalize_agent_link_version,
)
from meapet.agent.prompts import gateway_user_message
from meapet.agent.ws_transport import (
    ConnectionDropped,
    IncomingFrame,
    PersistentJsonWebSocket,
    WebSocketDisconnected,
    receive_json_frame,
    send_json_frame,
    validate_websocket_url,
)
from meapet.config.defaults import (
    DEFAULT_AGENT_LINK_WS_URL,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
)
from meapet.control.capabilities import (
    CapabilityArgumentsError,
    CapabilityError,
    CapabilityNotFoundError,
    CapabilityRegistry,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
)
from meapet.log import get_color_logger


log = get_color_logger("agent_link")


_SAFE_CATEGORIES = frozenset(
    {
        "authentication",
        "permission",
        "rate_limit",
        "backend_unavailable",
        "connection",
        "timeout",
        "protocol",
        "cancelled",
        "internal_error",
    }
)
_MAX_TERMINAL_TOOL_RESULTS = 32


def _package_version() -> str:
    try:
        return metadata.version("mea-pet")
    except metadata.PackageNotFoundError:
        return "1.0.0"


def _safe_text(value: object, *, limit: int = 500) -> str:
    result = " ".join(str(value or "").split())[:limit]
    if any(char in result for char in "\x00"):
        return ""
    return result


def _reply_message_id(prefix: str, request_id: str) -> str:
    digest = hashlib.sha256(
        str(request_id or "").encode("utf-8")
    ).hexdigest()[:24]
    return f"{prefix}-{digest}"


@dataclass(frozen=True)
class AgentLinkConfig:
    base_url: str = DEFAULT_AGENT_LINK_WS_URL
    auth_token: str = ""
    device_id: str = ""
    session_id: str = ""
    timeout_seconds: float = DEFAULT_AGENT_TIMEOUT_SECONDS
    verify_tls: bool = True
    ca_file: str = ""
    allow_insecure_ws: bool = False
    extensions: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized, _parsed = validate_websocket_url(
            self.base_url,
            allow_insecure_ws=self.allow_insecure_ws,
        )
        object.__setattr__(self, "base_url", normalized)
        object.__setattr__(
            self,
            "auth_token",
            str(self.auth_token or "").strip(),
        )
        for name in ("device_id", "session_id"):
            value = str(getattr(self, name) or "").strip()
            if (
                not value
                or len(value) > 256
                or any(char in value for char in "\r\n\x00")
            ):
                raise ValueError(f"Agent Link {name} 配置无效")
            object.__setattr__(self, name, value)
        try:
            timeout = float(self.timeout_seconds)
        except (TypeError, ValueError):
            timeout = DEFAULT_AGENT_TIMEOUT_SECONDS
        object.__setattr__(
            self,
            "timeout_seconds",
            timeout if timeout > 0 else DEFAULT_AGENT_TIMEOUT_SECONDS,
        )
        object.__setattr__(self, "verify_tls", bool(self.verify_tls))
        object.__setattr__(self, "ca_file", str(self.ca_file or "").strip())
        object.__setattr__(
            self,
            "allow_insecure_ws",
            bool(self.allow_insecure_ws),
        )
        # 复用信封校验，确保配置中的扩展也是有界、带命名空间的 JSON。
        normalized_frame = make_agent_link_frame(
            "control.validate",
            {},
            extensions=self.extensions,
        )
        object.__setattr__(
            self,
            "extensions",
            normalized_frame["extensions"],
        )


@dataclass(frozen=True)
class AgentLinkCapabilities:
    protocol_version: str
    agent_name: str
    server_version: str
    streaming: bool
    chat_cancel: bool
    dynamic_tools: bool
    tool_cancel: bool


@dataclass
class _AgentLinkTurn:
    request: AgentTurnRequest
    submit_frame: dict[str, Any]
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    last_seq: int = -1


class _AgentLinkFailure(RuntimeError):
    def __init__(
        self,
        category: str,
        safe_message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.category = (
            category if category in _SAFE_CATEGORIES else "backend_unavailable"
        )
        self.safe_message = (
            _safe_text(safe_message)
            or "Agent Link 后端返回了错误。"
        )
        self.retryable = bool(retryable)

    def event(self, turn_id: str) -> TurnFailed:
        return TurnFailed(
            turn_id,
            self.category,
            self.safe_message,
            self.retryable,
        )


class AgentLinkAdapter:
    """一条长连接同时承载聊天、主动消息和 MeaPet Tool 调用。"""

    def __init__(
        self,
        config: AgentLinkConfig,
        *,
        connector=None,
    ) -> None:
        self.config = config
        self._capabilities: AgentLinkCapabilities | None = None
        self._remote_session_id = config.session_id
        self._registry = CapabilityRegistry()
        self._registry_unsubscribe = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._closed = False
        self._fatal_error: BaseException | None = None
        self._active: dict[str, _AgentLinkTurn] = {}
        self._cancelled_turns: set[str] = set()
        self._tool_tasks: dict[str, asyncio.Task] = {}
        self._tool_terminal: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._registry_sync_tasks: set[asyncio.Task] = set()
        self._transport = PersistentJsonWebSocket(
            config.base_url,
            timeout_seconds=config.timeout_seconds,
            verify_tls=config.verify_tls,
            ca_file=config.ca_file,
            connector=connector,
            handshake=self._handshake,
        )

    def bind_capability_registry(self, registry: CapabilityRegistry) -> None:
        if not isinstance(registry, CapabilityRegistry):
            raise TypeError("registry must be a CapabilityRegistry")
        if self._registry_unsubscribe is not None:
            self._registry_unsubscribe()
        self._registry = registry
        self._registry_unsubscribe = registry.subscribe(
            self._on_registry_changed
        )
        self._on_registry_changed(registry.revision)

    def _on_registry_changed(self, _revision: int) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        def schedule() -> None:
            if self._ready.is_set() and not self._closed:
                task = asyncio.create_task(
                    self._send_tool_snapshot(),
                    name="meapet-agent-link-tools-changed",
                )
                self._registry_sync_tasks.add(task)
                task.add_done_callback(self._registry_sync_finished)

        try:
            loop.call_soon_threadsafe(schedule)
        except RuntimeError:
            pass

    def _registry_sync_finished(self, task: asyncio.Task) -> None:
        self._registry_sync_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None and not self._closed:
            log.warning(
                "[agent-link] 工具快照同步失败: "
                f"{type(error).__name__}"
            )

    def _hello_payload(self) -> dict[str, Any]:
        return {
            "client": {
                "name": "MeaPet",
                "version": _package_version(),
            },
            "device": {
                "id": self.config.device_id,
            },
            "auth": {
                "scheme": "bearer",
                "token": self.config.auth_token,
            },
            "resume": {
                "session_id": self._remote_session_id,
            },
            "capabilities": {
                "chat": {
                    "submit": True,
                    "streaming": True,
                    "cancel": True,
                },
                "tools": {
                    "dynamic": True,
                    "call": True,
                    "cancel": True,
                    "list_changed": True,
                },
                "assets": {
                    "inline": True,
                    "max_inline_bytes": 5 * 1024 * 1024,
                    "media_types": [
                        "image/jpeg",
                        "image/png",
                        "image/webp",
                    ],
                },
            },
            "required_extensions": [],
        }

    async def _handshake(self, websocket: Any) -> AgentLinkCapabilities:
        hello = make_agent_link_frame(
            "control.hello",
            self._hello_payload(),
            session_id=self._remote_session_id,
            extensions=self.config.extensions,
        )
        await send_json_frame(websocket, hello)
        raw = await receive_json_frame(
            websocket,
            timeout_seconds=min(15.0, self.config.timeout_seconds),
        )
        frame = AgentLinkFrame.parse(raw)
        if frame.type == "control.error":
            raise _AgentLinkFailure(
                str(frame.payload.get("category") or "authentication"),
                str(
                    frame.payload.get("safe_message")
                    or "Agent Link 握手被拒绝。"
                ),
                bool(frame.payload.get("retryable", False)),
            )
        if frame.type != "control.ready" or frame.reply_to != hello["id"]:
            raise AgentLinkProtocolError(
                "INVALID_HANDSHAKE",
                "Agent Link 服务端没有返回匹配的 control.ready",
            )
        if frame.payload.get("authenticated") is not True:
            raise _AgentLinkFailure(
                "authentication",
                "Agent Link 认证失败，请检查访问令牌。",
            )
        raw_selected_version = frame.payload.get("version")
        if raw_selected_version is None or raw_selected_version == "":
            raw_selected_version = frame.version
        selected_version = normalize_agent_link_version(raw_selected_version)
        required_extensions = frame.payload.get("required_extensions") or []
        if not isinstance(required_extensions, list):
            raise AgentLinkProtocolError(
                "INVALID_HANDSHAKE",
                "Agent Link required_extensions 必须是数组",
            )
        if required_extensions:
            raise AgentLinkProtocolError(
                "UNSUPPORTED_EXTENSION",
                "Agent Link 服务端要求了 MeaPet 不支持的必需扩展",
            )

        raw_capabilities = frame.payload.get("capabilities")
        capabilities = (
            raw_capabilities if isinstance(raw_capabilities, Mapping) else {}
        )
        chat = (
            capabilities.get("chat")
            if isinstance(capabilities.get("chat"), Mapping)
            else {}
        )
        tools = (
            capabilities.get("tools")
            if isinstance(capabilities.get("tools"), Mapping)
            else {}
        )
        if not bool(chat.get("submit", False)):
            raise AgentLinkProtocolError(
                "CHAT_UNAVAILABLE",
                "Agent Link 后端没有声明聊天请求能力",
            )
        if not bool(tools.get("call", False)) or not bool(
            tools.get("dynamic", False)
        ):
            raise AgentLinkProtocolError(
                "TOOLS_UNAVAILABLE",
                "Agent Link 后端没有声明动态前端工具能力",
            )

        selected_session_id = (
            frame.session_id
            or str(frame.payload.get("session_id") or "").strip()
            or self.config.session_id
        )
        if selected_session_id != self.config.session_id:
            raise AgentLinkProtocolError(
                "SESSION_MISMATCH",
                "Agent Link 服务端返回了不匹配的会话 ID",
            )
        self._remote_session_id = self.config.session_id
        result = AgentLinkCapabilities(
            protocol_version=selected_version,
            agent_name=_safe_text(
                frame.payload.get("agent_name") or "自定义 Agent",
                limit=120,
            ),
            server_version=_safe_text(
                frame.payload.get("server_version"),
                limit=120,
            ),
            streaming=bool(chat.get("streaming", True)),
            chat_cancel=bool(chat.get("cancel", True)),
            dynamic_tools=True,
            tool_cancel=bool(tools.get("cancel", True)),
        )
        self._capabilities = result
        return result

    async def start(self) -> None:
        """启动后台长连接；离线时按上限退避重连。"""
        if self._closed:
            raise RuntimeError("Agent Link 适配器已关闭")
        self._loop = asyncio.get_running_loop()
        if self._supervisor_task is None or self._supervisor_task.done():
            self._supervisor_task = asyncio.create_task(
                self._supervise(),
                name="meapet-agent-link-supervisor",
            )

    async def _wait_ready(self, timeout: float) -> None:
        await self.start()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.1, float(timeout))
        while not self._ready.is_set():
            if self._closed:
                raise WebSocketDisconnected("Agent Link 适配器已关闭")
            if self._fatal_error is not None:
                raise self._fatal_error
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                await asyncio.wait_for(
                    self._ready.wait(),
                    timeout=min(0.25, remaining),
                )
            except asyncio.TimeoutError:
                continue

    async def _supervise(self) -> None:
        delay = 0.25
        try:
            while not self._closed:
                try:
                    generation = await self._transport.ensure_connected()
                    await self._send_tool_snapshot(generation=generation)
                    await self._resend_active_turns(generation)
                    self._fatal_error = None
                    self._ready.set()
                    delay = 0.25
                    while not self._closed:
                        item = await self._transport.frames.get()
                        if isinstance(item, IncomingFrame):
                            if item.generation != generation:
                                continue
                            await self._dispatch_frame(item.payload)
                            continue
                        if (
                            isinstance(item, ConnectionDropped)
                            and item.generation == generation
                        ):
                            break
                except asyncio.CancelledError:
                    raise
                except (_AgentLinkFailure, AgentLinkProtocolError) as exc:
                    self._fatal_error = exc
                    self._fail_active_turns(exc)
                    return
                except (
                    OSError,
                    TimeoutError,
                    WebSocketDisconnected,
                ):
                    pass
                finally:
                    self._ready.clear()
                if self._closed:
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5.0)
        finally:
            self._ready.clear()

    def _fail_active_turns(self, error: BaseException) -> None:
        if isinstance(error, _AgentLinkFailure):
            failure = error
        elif isinstance(error, AgentLinkProtocolError):
            failure = _AgentLinkFailure(
                "protocol",
                error.safe_message,
            )
        else:
            failure = _AgentLinkFailure(
                "connection",
                "Agent Link 连接已断开。",
                True,
            )
        for turn_id, state in tuple(self._active.items()):
            state.queue.put_nowait(failure.event(turn_id))

    async def _send_protocol(
        self,
        frame: Mapping[str, object],
        *,
        timeout: float | None = None,
    ) -> None:
        limit = (
            self.config.timeout_seconds
            if timeout is None
            else max(0.1, float(timeout))
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + limit
        in_supervisor = asyncio.current_task() is self._supervisor_task
        while not self._closed:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            if in_supervisor and not self._ready.is_set():
                raise WebSocketDisconnected(
                    "Agent Link 连接在处理入站消息时断开"
                )
            await self._wait_ready(remaining)
            try:
                await self._transport.send_json(frame)
                return
            except WebSocketDisconnected:
                self._ready.clear()
                # 入站分发本身运行在 supervisor 中；它不能等待自己重连。
                if in_supervisor:
                    raise
        raise WebSocketDisconnected("Agent Link 适配器已关闭")

    async def _send_tool_snapshot(
        self,
        *,
        generation: int | None = None,
    ) -> None:
        snapshot = self._registry.protocol_snapshot()
        frame = make_agent_link_frame(
            "tools.snapshot",
            snapshot,
            message_id=f"tools-{snapshot['revision']}",
            session_id=self._remote_session_id,
            extensions=self.config.extensions,
        )
        if generation is None:
            await self._send_protocol(frame)
        else:
            await self._transport.send_json(frame, generation=generation)

    async def _resend_active_turns(self, generation: int) -> None:
        for state in tuple(self._active.values()):
            await self._transport.send_json(
                state.submit_frame,
                generation=generation,
            )

    def _frame_target(self, frame: AgentLinkFrame) -> str:
        return (
            frame.reply_to
            or str(frame.payload.get("request_id") or "").strip()
        )

    async def _dispatch_frame(self, raw: object) -> None:
        frame = AgentLinkFrame.parse(raw)
        if frame.session_id != self._remote_session_id:
            raise AgentLinkProtocolError(
                "SESSION_MISMATCH",
                "Agent Link 消息的会话 ID 与当前连接不匹配",
            )
        required_extensions = frame.payload.get("required_extensions") or []
        if not isinstance(required_extensions, list):
            raise AgentLinkProtocolError(
                "INVALID_EXTENSIONS",
                "Agent Link required_extensions 必须是数组",
            )
        if required_extensions:
            raise AgentLinkProtocolError(
                "UNSUPPORTED_EXTENSION",
                "Agent Link 消息要求了 MeaPet 不支持的必需扩展",
            )

        if frame.type == "control.ping":
            await self._send_protocol(
                make_agent_link_frame(
                    "control.pong",
                    {},
                    session_id=self._remote_session_id,
                    reply_to=frame.id,
                    extensions=self.config.extensions,
                ),
                timeout=5.0,
            )
            return
        if frame.type == "tools.refresh":
            await self._send_tool_snapshot()
            return
        if frame.type == "tool.call":
            await self._accept_tool_call(frame)
            return
        if frame.type == "tool.cancel":
            self._cancel_tool_call(frame)
            return
        if frame.type.startswith("chat."):
            target = self._frame_target(frame)
            state = self._active.get(target)
            if state is not None:
                await state.queue.put(frame)
            return
        if frame.type == "control.error":
            target = self._frame_target(frame)
            state = self._active.get(target)
            failure = self._failure_from_payload(frame.payload)
            if state is not None:
                await state.queue.put(failure.event(target))
                return
            raise failure
        if bool(frame.payload.get("required", False)):
            await self._send_protocol(
                make_agent_link_frame(
                    "control.error",
                    {
                        "code": "UNSUPPORTED_MESSAGE",
                        "safe_message": "MeaPet 不支持该必需消息类型。",
                        "retryable": False,
                    },
                    session_id=self._remote_session_id,
                    reply_to=frame.id,
                    extensions=self.config.extensions,
                ),
                timeout=5.0,
            )

    def _failure_from_payload(
        self,
        payload: Mapping[str, object],
    ) -> _AgentLinkFailure:
        category = str(
            payload.get("category") or "backend_unavailable"
        ).strip().lower()
        return _AgentLinkFailure(
            category,
            str(
                payload.get("safe_message")
                or "Agent Link 后端执行失败。"
            ),
            bool(payload.get("retryable", False)),
        )

    async def _accept_tool_call(self, frame: AgentLinkFrame) -> None:
        request_id = frame.id
        terminal = self._tool_terminal.get(request_id)
        if terminal is not None:
            self._tool_terminal.move_to_end(request_id)
            await self._send_protocol(terminal)
            return
        if request_id in self._tool_tasks:
            await self._send_tool_accepted(request_id, duplicate=True)
            return

        name = str(frame.payload.get("name") or "").strip()
        arguments = frame.payload.get("arguments", {})
        await self._send_tool_accepted(request_id, duplicate=False)
        if not isinstance(arguments, Mapping):
            terminal = self._tool_error_frame(
                request_id,
                "INVALID_ARGUMENTS",
                "工具参数不符合 MeaPet 公布的 Schema。",
                retryable=False,
            )
            self._cache_tool_terminal(request_id, terminal)
            await self._send_protocol(terminal)
            return
        task = asyncio.create_task(
            self._execute_tool(request_id, name, dict(arguments)),
            name=f"meapet-agent-link-tool-{request_id[:32]}",
        )
        self._tool_tasks[request_id] = task

    async def _send_tool_accepted(
        self,
        request_id: str,
        *,
        duplicate: bool,
    ) -> None:
        await self._send_protocol(
            make_agent_link_frame(
                "tool.accepted",
                {
                    "status": "accepted",
                    "duplicate": duplicate,
                },
                message_id=_reply_message_id("accepted", request_id),
                session_id=self._remote_session_id,
                reply_to=request_id,
                extensions=self.config.extensions,
            )
        )

    async def _execute_tool(
        self,
        request_id: str,
        name: str,
        arguments: Mapping[str, object],
    ) -> None:
        terminal: dict[str, Any]
        try:
            result = await self._registry.call(
                name,
                arguments,
                request_id=request_id,
            )
            terminal = make_agent_link_frame(
                "tool.result",
                {
                    "status": "succeeded",
                    "result": result,
                },
                message_id=_reply_message_id("result", request_id),
                session_id=self._remote_session_id,
                reply_to=request_id,
                extensions=self.config.extensions,
            )
        except asyncio.CancelledError:
            terminal = make_agent_link_frame(
                "tool.error",
                {
                    "status": "cancelled",
                    "code": "CANCELLED",
                    "safe_message": "MeaPet 已取消该工具调用。",
                    "retryable": False,
                },
                message_id=_reply_message_id("cancelled", request_id),
                session_id=self._remote_session_id,
                reply_to=request_id,
                extensions=self.config.extensions,
            )
        except CapabilityNotFoundError:
            terminal = self._tool_error_frame(
                request_id,
                "TOOL_UNAVAILABLE",
                "当前 MeaPet 没有提供该工具。",
                retryable=False,
            )
        except CapabilityArgumentsError:
            terminal = self._tool_error_frame(
                request_id,
                "INVALID_ARGUMENTS",
                "工具参数不符合 MeaPet 公布的 Schema。",
                retryable=False,
            )
        except CapabilityError:
            terminal = self._tool_error_frame(
                request_id,
                "INVALID_RESULT",
                "MeaPet 工具返回了无效结果。",
                retryable=False,
            )
        except Exception:
            terminal = self._tool_error_frame(
                request_id,
                "EXECUTION_FAILED",
                "MeaPet 工具执行失败。",
                retryable=True,
            )
        finally:
            self._tool_tasks.pop(request_id, None)

        self._cache_tool_terminal(request_id, terminal)
        if not self._closed:
            try:
                await self._send_protocol(terminal)
            except (
                asyncio.TimeoutError,
                WebSocketDisconnected,
            ):
                # 终态保留在有界缓存中；Agent 使用同一 request_id 重试时
                # 直接返回，不重复执行本机操作。
                pass

    def _cache_tool_terminal(
        self,
        request_id: str,
        terminal: dict[str, Any],
    ) -> None:
        self._tool_terminal[request_id] = terminal
        self._tool_terminal.move_to_end(request_id)
        while len(self._tool_terminal) > _MAX_TERMINAL_TOOL_RESULTS:
            self._tool_terminal.popitem(last=False)

    def _tool_error_frame(
        self,
        request_id: str,
        code: str,
        safe_message: str,
        *,
        retryable: bool,
    ) -> dict[str, Any]:
        return make_agent_link_frame(
            "tool.error",
            {
                "status": "failed",
                "code": code,
                "safe_message": safe_message,
                "retryable": bool(retryable),
            },
            message_id=_reply_message_id("error", request_id),
            session_id=self._remote_session_id,
            reply_to=request_id,
            extensions=self.config.extensions,
        )

    def _cancel_tool_call(self, frame: AgentLinkFrame) -> None:
        request_id = self._frame_target(frame)
        task = self._tool_tasks.get(request_id)
        if task is not None and not task.done():
            task.cancel()

    def _chat_submit_frame(self, request: AgentTurnRequest) -> dict[str, Any]:
        attachments = [
            {
                "type": "image",
                "media_type": attachment.media_type,
                "file_name": attachment.file_name,
                "data": attachment.data,
            }
            for attachment in request.attachments
        ]
        return make_agent_link_frame(
            "chat.submit",
            {
                "content": gateway_user_message(request),
                "user_text": request.user_text,
                "history": [dict(item) for item in request.history],
                "frontend_context": dict(request.frontend_context),
                "attachments": attachments,
                "response_format": "meapet-segments-v1",
                "idempotent": True,
            },
            message_id=request.turn_id,
            session_id=self._remote_session_id,
            extensions=self.config.extensions,
        )

    async def probe(self) -> AgentLinkCapabilities:
        await self.start()
        try:
            await self._wait_ready(
                min(15.0, self.config.timeout_seconds)
            )
        except _AgentLinkFailure as exc:
            raise ValueError(exc.safe_message) from None
        except AgentLinkProtocolError as exc:
            raise ValueError(exc.safe_message) from None
        except (asyncio.TimeoutError, WebSocketDisconnected, OSError) as exc:
            raise ValueError(
                "无法连接 Agent Link WebSocket，请检查地址、令牌和服务状态"
            ) from exc
        if self._capabilities is None:
            raise ValueError("Agent Link 握手未返回能力")
        return self._capabilities

    async def cancel_turn(self, turn_id: str) -> None:
        safe_turn_id = str(turn_id or "").strip()
        self._cancelled_turns.add(safe_turn_id)
        state = self._active.get(safe_turn_id)
        if state is not None:
            await state.queue.put(TurnCancelled(safe_turn_id))
        capabilities = self._capabilities
        if capabilities is not None and not capabilities.chat_cancel:
            return
        try:
            await self._send_protocol(
                make_agent_link_frame(
                    "chat.cancel",
                    {"request_id": safe_turn_id},
                    session_id=self._remote_session_id,
                    reply_to=safe_turn_id,
                    extensions=self.config.extensions,
                ),
                timeout=2.0,
            )
        except (
            asyncio.TimeoutError,
            WebSocketDisconnected,
            OSError,
        ):
            pass

    cancel = cancel_turn

    async def close(self) -> None:
        self._closed = True
        self._ready.clear()
        for turn_id, state in tuple(self._active.items()):
            state.queue.put_nowait(TurnCancelled(turn_id))
        if self._registry_unsubscribe is not None:
            self._registry_unsubscribe()
            self._registry_unsubscribe = None
        for task in tuple(self._registry_sync_tasks):
            task.cancel()
        if self._registry_sync_tasks:
            await asyncio.gather(
                *tuple(self._registry_sync_tasks),
                return_exceptions=True,
            )
        self._registry_sync_tasks.clear()
        for task in tuple(self._tool_tasks.values()):
            task.cancel()
        if self._tool_tasks:
            await asyncio.gather(
                *tuple(self._tool_tasks.values()),
                return_exceptions=True,
            )
        self._tool_tasks.clear()
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            await asyncio.gather(
                self._supervisor_task,
                return_exceptions=True,
            )
            self._supervisor_task = None
        await self._transport.close()
        self._active.clear()

    async def stream_turn(
        self,
        request: AgentTurnRequest,
    ) -> AsyncIterator[object]:
        turn_id = request.turn_id
        if turn_id in self._cancelled_turns:
            self._cancelled_turns.discard(turn_id)
            yield TurnCancelled(turn_id)
            return

        parser = MeaPetOutputStreamParser()
        raw_output = ""
        completed_indices: set[int] = set()
        protocol_completed_emitted = False
        suppress_stream = False
        try:
            await self._wait_ready(
                min(15.0, self.config.timeout_seconds)
            )
            submit_frame = self._chat_submit_frame(request)
            state = _AgentLinkTurn(request, submit_frame)
            self._active[turn_id] = state
            await self._send_protocol(submit_frame)

            while True:
                item = await asyncio.wait_for(
                    state.queue.get(),
                    timeout=self.config.timeout_seconds,
                )
                if isinstance(item, TurnCancelled):
                    self._cancelled_turns.discard(turn_id)
                    yield item
                    return
                if isinstance(item, TurnFailed):
                    yield item
                    return
                if not isinstance(item, AgentLinkFrame):
                    continue
                if item.type == "chat.accepted":
                    continue
                if item.type == "chat.tool_status":
                    state_name = _safe_text(
                        item.payload.get("state"),
                        limit=64,
                    )
                    safe_status = _safe_text(
                        item.payload.get("safe_text"),
                        limit=300,
                    )
                    if state_name and safe_status:
                        yield ToolStatus(state_name, safe_status)
                    continue
                if item.type == "chat.delta":
                    sequence = item.payload.get("seq")
                    if isinstance(sequence, int):
                        if sequence <= state.last_seq:
                            continue
                        state.last_seq = sequence
                    delta = item.payload.get("text")
                    if not isinstance(delta, str) or not delta:
                        continue
                    if bool(item.payload.get("replace", False)):
                        raw_output = delta
                        parser = MeaPetOutputStreamParser()
                        completed_indices.clear()
                        protocol_completed_emitted = False
                        suppress_stream = True
                    else:
                        raw_output += delta
                    for event in parser.feed(delta):
                        if isinstance(event, SegmentCompleted):
                            if event.segment.missing_required_fields:
                                continue
                            completed_indices.add(event.segment.index)
                        elif isinstance(event, ProtocolCompleted):
                            protocol_completed_emitted = True
                        if not suppress_stream:
                            yield event
                    if parser.overflowed:
                        yield TurnFailed(
                            turn_id,
                            "protocol",
                            "Agent Link 输出超过长度上限，已中止本回合。",
                        )
                        return
                    continue
                if item.type == "chat.final":
                    final_text = item.payload.get("text")
                    if isinstance(final_text, str) and final_text:
                        if bool(item.payload.get("replace", False)):
                            raw_output = final_text
                            parser = MeaPetOutputStreamParser()
                            parser.feed(final_text)
                            completed_indices.clear()
                            protocol_completed_emitted = False
                            suppress_stream = True
                        elif not raw_output:
                            raw_output = final_text
                            parser.feed(final_text)
                    break
                if item.type == "chat.cancelled":
                    yield TurnCancelled(turn_id)
                    return
                if item.type == "chat.error":
                    raise self._failure_from_payload(item.payload)

            result = parser.close(tts_enabled=request.tts_enabled)
            if result.requires_repair(tts_enabled=request.tts_enabled):
                yield FormatRepairRequired(result)
            if not any(
                segment.display_text.strip() for segment in result.segments
            ):
                yield TurnFailed(
                    turn_id,
                    "protocol",
                    "Agent Link 没有返回可展示的回复。",
                )
                return
            for segment in result.segments:
                if segment.index not in completed_indices:
                    yield SegmentCompleted(segment)
                    completed_indices.add(segment.index)
            if result.done and not protocol_completed_emitted:
                yield ProtocolCompleted()
            yield TurnCompleted(turn_id, result)
        except asyncio.CancelledError:
            raise
        except _AgentLinkFailure as exc:
            yield exc.event(turn_id)
        except AgentLinkProtocolError as exc:
            yield TurnFailed(
                turn_id,
                "protocol",
                exc.safe_message,
            )
        except (asyncio.TimeoutError, TimeoutError):
            await self.cancel_turn(turn_id)
            yield TurnFailed(
                turn_id,
                "timeout",
                "Agent Link 响应超时，请稍后再试。",
                True,
            )
        except (WebSocketDisconnected, OSError):
            yield TurnFailed(
                turn_id,
                "connection",
                "无法连接 Agent Link 后端，请检查地址和网络。",
                True,
            )
        except ValueError:
            yield TurnFailed(
                turn_id,
                "configuration",
                "Agent Link 配置不完整，请检查地址、令牌和证书。",
            )
        except Exception:
            yield TurnFailed(
                turn_id,
                "internal_error",
                "Agent Link 连接发生了未预期错误。",
                True,
            )
        finally:
            self._active.pop(turn_id, None)
