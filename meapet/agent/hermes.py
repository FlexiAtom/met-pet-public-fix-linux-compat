"""Hermes TUI Gateway 原生 WebSocket JSON-RPC 适配器。"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Mapping, MutableMapping
from urllib.parse import urlencode, urlsplit, urlunsplit

from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.prompts import gateway_user_message
from meapet.agent.ws_transport import (
    ConnectionDropped,
    IncomingFrame,
    PersistentJsonWebSocket,
    WebSocketDisconnected,
    validate_websocket_url,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
    parse_reply_output,
)


_CONTROL_RE = re.compile(r"[\r\n\x00]")
_INTERACTIVE_REQUEST_EVENTS = frozenset(
    {
        "approval.request",
        "clarify.request",
        "secret.request",
        "sudo.request",
    }
)


def _safe_value(name: str, value: object, *, limit: int = 512) -> str:
    result = str(value or "").strip()
    if len(result) > limit or _CONTROL_RE.search(result):
        raise ValueError(f"{name} contains unsafe characters")
    return result


@dataclass(frozen=True)
class HermesConfig:
    base_url: str = "ws://127.0.0.1:9119/api/ws"
    auth_token: str = ""
    model: str = ""
    session_id: str = ""
    session_key: str = ""
    remote_session_id: str = ""
    history_turns: int = 5
    timeout_seconds: float = 120.0
    verify_tls: bool = True
    ca_file: str = ""
    allow_insecure_ws: bool = False

    def __post_init__(self) -> None:
        normalized, parsed = validate_websocket_url(
            self.base_url,
            allow_insecure_ws=self.allow_insecure_ws,
        )
        path = parsed.path.rstrip("/")
        if not path:
            path = "/api/ws"
        elif not path.endswith("/api/ws"):
            path = f"{path}/api/ws"
        normalized = urlunsplit(
            (parsed.scheme, parsed.netloc, path, "", "")
        )
        object.__setattr__(self, "base_url", normalized)
        object.__setattr__(self, "auth_token", str(self.auth_token or "").strip())
        object.__setattr__(self, "model", _safe_value("model", self.model))
        object.__setattr__(
            self,
            "session_id",
            _safe_value("session_id", self.session_id),
        )
        object.__setattr__(
            self,
            "session_key",
            _safe_value("session_key", self.session_key),
        )
        object.__setattr__(
            self,
            "remote_session_id",
            _safe_value("remote_session_id", self.remote_session_id),
        )
        try:
            history_turns = int(self.history_turns)
        except (TypeError, ValueError):
            history_turns = 5
        object.__setattr__(
            self,
            "history_turns",
            max(0, min(history_turns, 100)),
        )
        try:
            timeout = float(self.timeout_seconds)
        except (TypeError, ValueError):
            timeout = 120.0
        object.__setattr__(
            self,
            "timeout_seconds",
            timeout if timeout > 0 else 120.0,
        )
        object.__setattr__(self, "verify_tls", bool(self.verify_tls))
        object.__setattr__(self, "ca_file", str(self.ca_file or "").strip())
        object.__setattr__(
            self,
            "allow_insecure_ws",
            bool(self.allow_insecure_ws),
        )

    def authenticated_url(self) -> str:
        if not self.auth_token:
            return self.base_url
        parsed = urlsplit(self.base_url)
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode({"token": self.auth_token}),
                "",
            )
        )


@dataclass(frozen=True)
class HermesCapabilities:
    platform: str = "hermes"
    protocol: str = "json-rpc-2.0"
    streaming: bool = True
    session_resume: bool = True
    image_upload: bool = True


class _HermesRpcError(RuntimeError):
    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message)
        self.code = int(code)


def _safe_rpc_failure(turn_id: str, error: _HermesRpcError) -> TurnFailed:
    code = error.code
    if code in {401, 4010, 4011, 4401}:
        return TurnFailed(
            turn_id,
            "authentication",
            "Hermes 认证失败，请检查 WebSocket 访问令牌。",
        )
    if code in {403, 4030, 4403}:
        return TurnFailed(turn_id, "permission", "Hermes 拒绝了当前请求。")
    if code in {4090, 429, 4290}:
        return TurnFailed(
            turn_id,
            "rate_limit",
            "Hermes 当前繁忙，请稍后再试。",
            True,
        )
    if code >= 5000 or -32099 <= code <= -32000:
        return TurnFailed(
            turn_id,
            "backend_unavailable",
            "Hermes Gateway 暂时不可用。",
            True,
        )
    return TurnFailed(
        turn_id,
        "protocol",
        "Hermes Gateway 返回了无法处理的响应。",
    )


def _websocket_close_code(error: BaseException) -> int | None:
    code = getattr(error, "code", None)
    if code is None:
        received = getattr(error, "rcvd", None)
        code = getattr(received, "code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def _tool_status(event_type: str, payload: Mapping[str, object]) -> ToolStatus:
    del payload
    tail = event_type.rsplit(".", 1)[-1].lower()
    if tail in {"complete", "completed", "success", "succeeded"}:
        state = "succeeded"
    elif tail in {"error", "failed", "cancelled", "aborted"}:
        state = "failed"
    else:
        state = "started"
    return ToolStatus(
        state,
        {
            "started": "Agent 正在执行工具",
            "succeeded": "Agent 工具执行完成",
            "failed": "Agent 工具执行失败",
        }[state],
    )


def _history_messages(request: AgentTurnRequest, turns: int) -> list[dict]:
    history = []
    for item in request.history:
        role = str(item.get("role") or "").strip().lower()
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        text = content.strip()
        if text:
            history.append({"role": role, "content": text})
    if turns <= 0:
        return []
    return history[-turns * 2 :]


def _content_text(message: object) -> str:
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, Mapping) and part.get("type") in {"text", None}
    )


def _last_assistant(messages: object) -> str:
    if not isinstance(messages, (list, tuple)):
        return ""
    for message in reversed(messages):
        if (
            isinstance(message, Mapping)
            and str(message.get("role") or "").lower() == "assistant"
        ):
            text = _content_text(message)
            if text.strip():
                return text
    return ""


def _assistant_text_signature(text: object) -> str:
    """让远端协议原文可与桌面端保存的展示文本可靠比较。"""
    value = str(text or "").strip()
    if not value:
        return ""
    result = parse_reply_output(value)
    visible = "\n".join(
        segment.display_text.strip()
        for segment in result.segments
        if segment.display_text.strip()
    )
    return visible or value


@dataclass
class _PendingRequest:
    future: asyncio.Future
    generation: int


@dataclass
class _HermesTurn:
    turn_id: str
    runtime_session_id: str
    generation: int
    queue: asyncio.Queue[object] = field(default_factory=asyncio.Queue)


@dataclass(frozen=True)
class _HermesNetworkItem:
    generation: int
    value: object


@dataclass(frozen=True)
class _HermesRecovery:
    resumed: bool
    text: str = ""
    failure: TurnFailed | None = None


class HermesAdapter:
    """连接 ``hermes serve`` 的 `/api/ws`，不经过 HTTP API Server。"""

    def __init__(
        self,
        config: HermesConfig,
        *,
        connector: Callable[..., object] | None = None,
        config_sink: MutableMapping[str, object] | None = None,
    ) -> None:
        self.config = config
        self._config_sink = config_sink
        self._remote_session_id = config.remote_session_id
        self._runtime_session_id = ""
        self._session_generation = 0
        self._session_lock = asyncio.Lock()
        self._pending: dict[str, _PendingRequest] = {}
        self._active: dict[str, _HermesTurn] = {}
        self._cancelled_turns: set[str] = set()
        self._ready_events: dict[int, asyncio.Event] = {}
        self._dropped_events: dict[int, asyncio.Event] = {}
        self._drop_errors: dict[int, BaseException] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._transport = PersistentJsonWebSocket(
            self.config.authenticated_url(),
            timeout_seconds=self.config.timeout_seconds,
            verify_tls=self.config.verify_tls,
            ca_file=self.config.ca_file,
            connector=connector,
        )

    @property
    def remote_session_id(self) -> str:
        return self._remote_session_id

    def _remember_remote_session(self, value: object) -> None:
        remote = _safe_value("remote_session_id", value)
        if not remote:
            return
        self._remote_session_id = remote
        if self._config_sink is not None:
            self._config_sink["remote_session_id"] = remote

    async def _ensure_connected(self) -> int:
        if not self.config.auth_token:
            raise ValueError(
                "Hermes WebSocket token is required; start hermes serve with "
                "HERMES_DASHBOARD_SESSION_TOKEN"
            )
        generation = await self._transport.ensure_connected()
        ready = self._ready_events.setdefault(generation, asyncio.Event())
        dropped = self._dropped_events.setdefault(
            generation,
            asyncio.Event(),
        )
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(
                self._dispatch_frames(),
                name="meapet-hermes-dispatch",
            )
        ready_task = asyncio.create_task(ready.wait())
        dropped_task = asyncio.create_task(dropped.wait())
        try:
            done, _pending = await asyncio.wait(
                (ready_task, dropped_task),
                timeout=min(15.0, self.config.timeout_seconds),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError
            if not ready.is_set():
                error = self._drop_errors.get(
                    generation,
                    WebSocketDisconnected(
                        "Hermes Gateway closed before gateway.ready"
                    ),
                )
                close_code = _websocket_close_code(error)
                if close_code in {4401, 4403}:
                    raise _HermesRpcError(close_code)
                raise WebSocketDisconnected(
                    "Hermes Gateway closed before gateway.ready"
                ) from error
        finally:
            for task in (ready_task, dropped_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                ready_task,
                dropped_task,
                return_exceptions=True,
            )
        return generation

    async def _dispatch_frames(self) -> None:
        while True:
            incoming = await self._transport.frames.get()
            if isinstance(incoming, ConnectionDropped):
                self._drop_errors[incoming.generation] = incoming.error
                self._dropped_events.setdefault(
                    incoming.generation,
                    asyncio.Event(),
                ).set()
                failure = WebSocketDisconnected(
                    "Hermes Gateway connection dropped"
                )
                for request_id, pending in tuple(self._pending.items()):
                    if pending.generation != incoming.generation:
                        continue
                    self._pending.pop(request_id, None)
                    if not pending.future.done():
                        pending.future.set_exception(failure)
                for state in tuple(self._active.values()):
                    if state.generation == incoming.generation:
                        await state.queue.put(incoming)
                continue
            frame = incoming.payload
            if "id" in frame and (
                "result" in frame or "error" in frame
            ):
                self._dispatch_response(incoming.generation, frame)
                continue
            if frame.get("method") != "event":
                continue
            params = frame.get("params")
            if not isinstance(params, Mapping):
                continue
            event_type = str(params.get("type") or "")
            if event_type == "gateway.ready":
                self._ready_events.setdefault(
                    incoming.generation,
                    asyncio.Event(),
                ).set()
                continue
            session_id = str(params.get("session_id") or "")
            for state in tuple(self._active.values()):
                if state.generation != incoming.generation:
                    continue
                if session_id and session_id != state.runtime_session_id:
                    continue
                await state.queue.put(
                    _HermesNetworkItem(incoming.generation, params)
                )

    def _dispatch_response(
        self,
        generation: int,
        frame: Mapping[str, object],
    ) -> None:
        request_id = str(frame.get("id") or "")
        pending = self._pending.get(request_id)
        if pending is None or pending.generation != generation:
            return
        self._pending.pop(request_id, None)
        error = frame.get("error")
        if isinstance(error, Mapping):
            try:
                code = int(error.get("code") or -32000)
            except (TypeError, ValueError):
                code = -32000
            if not pending.future.done():
                pending.future.set_exception(
                    _HermesRpcError(code, str(error.get("message") or ""))
                )
            return
        result = frame.get("result")
        if not pending.future.done():
            pending.future.set_result(
                result if isinstance(result, Mapping) else {}
            )

    async def _rpc(
        self,
        method: str,
        params: Mapping[str, object],
        *,
        generation: int | None = None,
    ) -> Mapping[str, object]:
        if generation is None:
            generation = await self._ensure_connected()
        request_id = f"{method}:{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = _PendingRequest(future, generation)
        try:
            await self._transport.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": dict(params),
                },
                generation=generation,
            )
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.config.timeout_seconds,
            )
            return result
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _create_session(
        self,
        request: AgentTurnRequest,
        generation: int,
    ) -> Mapping[str, object]:
        params: dict[str, object] = {
            "messages": _history_messages(request, self.config.history_turns),
            "source": "meapet",
            "close_on_disconnect": False,
        }
        if self.config.model:
            params["model"] = self.config.model
        result = await self._rpc(
            "session.create",
            params,
            generation=generation,
        )
        self._remember_remote_session(
            result.get("stored_session_id") or result.get("session_key")
        )
        return result

    async def _ensure_session(
        self,
        request: AgentTurnRequest,
        *,
        create_if_missing: bool = True,
    ) -> tuple[str, int, Mapping[str, object]]:
        generation = await self._ensure_connected()
        if (
            self._runtime_session_id
            and self._session_generation == generation
        ):
            return self._runtime_session_id, generation, {}
        async with self._session_lock:
            generation = await self._ensure_connected()
            if (
                self._runtime_session_id
                and self._session_generation == generation
            ):
                return self._runtime_session_id, generation, {}
            result: Mapping[str, object]
            if self._remote_session_id:
                try:
                    result = await self._rpc(
                        "session.resume",
                        {
                            "session_id": self._remote_session_id,
                            "source": "meapet",
                            "close_on_disconnect": False,
                        },
                        generation=generation,
                    )
                except _HermesRpcError as exc:
                    if exc.code != 4007 or not create_if_missing:
                        raise
                    self._remote_session_id = ""
                    if self._config_sink is not None:
                        self._config_sink.pop("remote_session_id", None)
                    result = await self._create_session(request, generation)
            else:
                result = await self._create_session(request, generation)
            runtime = _safe_value("runtime session id", result.get("session_id"))
            if not runtime:
                raise _HermesRpcError(-32603, "missing runtime session id")
            self._runtime_session_id = runtime
            self._session_generation = generation
            self._remember_remote_session(
                result.get("stored_session_id")
                or result.get("session_key")
                or result.get("resumed")
                or self._remote_session_id
            )
            return runtime, generation, result

    async def probe(self) -> HermesCapabilities:
        try:
            await self._ensure_connected()
        except _HermesRpcError as exc:
            failure = _safe_rpc_failure("", exc)
            raise ValueError(failure.safe_message) from None
        except (OSError, TimeoutError, WebSocketDisconnected) as exc:
            raise ValueError(
                "无法连接 Hermes WebSocket Gateway；请确认 hermes serve 已启动"
            ) from exc
        except Exception as exc:
            raise ValueError("Hermes WebSocket Gateway 握手失败") from exc
        return HermesCapabilities()

    async def cancel_turn(self, turn_id: str) -> None:
        safe_turn_id = _safe_value("turn_id", turn_id)
        self._cancelled_turns.add(safe_turn_id)
        state = self._active.get(safe_turn_id)
        if state is None:
            return
        await state.queue.put(TurnCancelled(safe_turn_id))
        try:
            await asyncio.wait_for(
                self._rpc(
                    "session.interrupt",
                    {"session_id": state.runtime_session_id},
                    generation=state.generation,
                ),
                timeout=min(2.0, self.config.timeout_seconds),
            )
        except (
            asyncio.TimeoutError,
            _HermesRpcError,
            WebSocketDisconnected,
            OSError,
        ):
            pass

    cancel = cancel_turn

    async def close(self) -> None:
        for pending in tuple(self._pending.values()):
            if not pending.future.done():
                pending.future.cancel()
        self._pending.clear()
        self._active.clear()
        if self._dispatch_task is not None:
            self._dispatch_task.cancel()
            await asyncio.gather(self._dispatch_task, return_exceptions=True)
            self._dispatch_task = None
        await self._transport.close()

    async def _attach_images(
        self,
        request: AgentTurnRequest,
        *,
        runtime_session_id: str,
        generation: int,
    ) -> None:
        for attachment in request.attachments:
            await self._rpc(
                "image.attach_bytes",
                {
                    "session_id": runtime_session_id,
                    "content_base64": attachment.data,
                    "filename": attachment.file_name,
                },
                generation=generation,
            )

    async def _recover_turn(
        self,
        request: AgentTurnRequest,
        state: _HermesTurn,
        previous_assistant: str,
    ) -> _HermesRecovery:
        for delay in (0.0, 0.25, 0.75, 1.5):
            if request.turn_id in self._cancelled_turns:
                return _HermesRecovery(False)
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._transport.reconnect(
                    expected_generation=state.generation
                )
                self._runtime_session_id = ""
                runtime, generation, resume = await self._ensure_session(
                    request,
                    create_if_missing=False,
                )
                state.runtime_session_id = runtime
                state.generation = generation
                status = str(resume.get("status") or "").lower()
                inflight = resume.get("inflight")
                if isinstance(inflight, Mapping):
                    inflight_status = str(
                        inflight.get("status") or ""
                    ).strip().lower()
                    inflight_error = str(
                        inflight.get("error") or ""
                    ).strip()
                    if inflight_error or inflight_status in {
                        "error",
                        "failed",
                    }:
                        return _HermesRecovery(
                            True,
                            failure=TurnFailed(
                                request.turn_id,
                                "backend",
                                "Hermes 本轮执行失败；服务端保留了错误状态。",
                                bool(inflight.get("recoverable", True)),
                            ),
                        )
                    inflight_running = bool(
                        inflight.get("streaming", False)
                    )
                else:
                    inflight_running = False
                running = bool(resume.get("running")) or status == "streaming"
                auto_continue = resume.get("auto_continue")
                if (
                    running
                    or inflight_running
                    or isinstance(auto_continue, Mapping)
                ):
                    return _HermesRecovery(True)
                history = await self._rpc(
                    "session.history",
                    {"session_id": runtime},
                    generation=generation,
                )
                recovered = _last_assistant(history.get("messages"))
                if recovered and (
                    not previous_assistant
                    or _assistant_text_signature(recovered)
                    != _assistant_text_signature(previous_assistant)
                ):
                    return _HermesRecovery(True, recovered)
                return _HermesRecovery(False)
            except (
                asyncio.TimeoutError,
                _HermesRpcError,
                WebSocketDisconnected,
                OSError,
            ):
                continue
        return _HermesRecovery(False)

    async def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[object]:
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
        state: _HermesTurn | None = None
        try:
            runtime, generation, session_result = await self._ensure_session(
                request
            )
            state = _HermesTurn(turn_id, runtime, generation)
            self._active[turn_id] = state
            # Hermes prompt.submit 没有外部幂等键。提交前从远端取精确基线，
            # 断线恢复时只把基线之后的新 assistant 消息认作本轮结果。
            baseline = await self._rpc(
                "session.history",
                {"session_id": runtime},
                generation=generation,
            )
            previous_assistant = (
                _last_assistant(baseline.get("messages"))
                or _last_assistant(session_result.get("messages"))
                or _last_assistant(request.history)
            )
            await self._attach_images(
                request,
                runtime_session_id=runtime,
                generation=generation,
            )
            terminal_status = "complete"
            recovered_before_stream = ""
            try:
                await self._rpc(
                    "prompt.submit",
                    {
                        "session_id": runtime,
                        "text": gateway_user_message(request),
                    },
                    generation=generation,
                )
            except WebSocketDisconnected:
                recovery = await self._recover_turn(
                    request,
                    state,
                    previous_assistant,
                )
                if recovery.failure is not None:
                    yield recovery.failure
                    return
                if not recovery.resumed:
                    yield TurnFailed(
                        turn_id,
                        "connection",
                        "Hermes 连接中断；为避免重复执行工具，本轮未自动重发。",
                        True,
                    )
                    return
                recovered_before_stream = recovery.text
                # 丢弃触发上面恢复流程的旧代际断线通知；保留恢复后已经
                # 到达的新事件及其顺序。
                retained = []
                while not state.queue.empty():
                    queued = state.queue.get_nowait()
                    if not isinstance(queued, ConnectionDropped):
                        retained.append(queued)
                for queued in retained:
                    state.queue.put_nowait(queued)
            if recovered_before_stream:
                raw_output = recovered_before_stream
                parser.feed(recovered_before_stream)
                suppress_stream = True

            while not recovered_before_stream:
                item = await asyncio.wait_for(
                    state.queue.get(),
                    timeout=self.config.timeout_seconds,
                )
                if isinstance(item, _HermesNetworkItem):
                    if item.generation != state.generation:
                        continue
                    item = item.value
                if isinstance(item, TurnCancelled):
                    self._cancelled_turns.discard(turn_id)
                    yield TurnCancelled(turn_id)
                    return
                if isinstance(item, ConnectionDropped):
                    recovery = await self._recover_turn(
                        request,
                        state,
                        previous_assistant,
                    )
                    if recovery.failure is not None:
                        yield recovery.failure
                        return
                    if not recovery.resumed:
                        yield TurnFailed(
                            turn_id,
                            "connection",
                            "Hermes 连接中断；为避免重复执行工具，本轮未自动重发。",
                            True,
                        )
                        return
                    recovered_text = recovery.text
                    if recovered_text:
                        raw_output = recovered_text
                        parser = MeaPetOutputStreamParser()
                        parser.feed(recovered_text)
                        suppress_stream = True
                        break
                    continue
                if not isinstance(item, Mapping):
                    continue
                event_type = str(item.get("type") or "")
                payload = item.get("payload")
                payload = payload if isinstance(payload, Mapping) else {}
                if event_type in _INTERACTIVE_REQUEST_EVENTS:
                    try:
                        await asyncio.wait_for(
                            self._rpc(
                                "session.interrupt",
                                {"session_id": state.runtime_session_id},
                                generation=state.generation,
                            ),
                            timeout=min(2.0, self.config.timeout_seconds),
                        )
                    except (
                        asyncio.TimeoutError,
                        _HermesRpcError,
                        WebSocketDisconnected,
                        OSError,
                    ):
                        pass
                    yield TurnFailed(
                        turn_id,
                        "interaction_required",
                        "Hermes 正在等待交互式授权或补充信息；"
                        "MeaPet 不会自动同意敏感操作。",
                    )
                    return
                if event_type.startswith("tool."):
                    yield _tool_status(event_type, payload)
                    continue
                if event_type == "message.delta":
                    delta = payload.get("text")
                    if not isinstance(delta, str) or not delta:
                        continue
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
                            "Hermes 输出超过长度上限，已中止本回合。",
                        )
                        return
                    continue
                if event_type == "message.complete":
                    terminal_status = str(
                        payload.get("status") or "complete"
                    ).lower()
                    final_text = payload.get("text")
                    if isinstance(final_text, str) and final_text:
                        if not raw_output:
                            raw_output = final_text
                            parser.feed(final_text)
                        elif final_text != raw_output:
                            raw_output = final_text
                            parser = MeaPetOutputStreamParser()
                            parser.feed(final_text)
                            completed_indices.clear()
                            protocol_completed_emitted = False
                            suppress_stream = True
                    break
                if event_type == "error":
                    raise _HermesRpcError(-32000)

            if terminal_status in {"error", "failed"}:
                yield TurnFailed(
                    turn_id,
                    "backend",
                    "Hermes 未能完成本轮回复。",
                    True,
                )
                return
            if terminal_status in {"interrupted", "cancelled", "aborted"}:
                yield TurnCancelled(turn_id)
                return
            result = parser.close(tts_enabled=request.tts_enabled)
            if result.requires_repair(tts_enabled=request.tts_enabled):
                # Agent 模式禁止偷偷回落到 HTTP。先保留可展示解析结果，并把
                # 修复请求显式交给呈现层；后续可在独立 Hermes 会话中修复。
                yield FormatRepairRequired(result)
            if not any(
                segment.display_text.strip() for segment in result.segments
            ):
                yield TurnFailed(
                    turn_id,
                    "protocol",
                    "Hermes 没有返回可展示的回复。",
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
        except _HermesRpcError as exc:
            yield _safe_rpc_failure(turn_id, exc)
        except (asyncio.TimeoutError, TimeoutError):
            yield TurnFailed(
                turn_id,
                "timeout",
                "Hermes 响应超时，请稍后再试。",
                True,
            )
        except (WebSocketDisconnected, OSError):
            yield TurnFailed(
                turn_id,
                "connection",
                "无法连接 Hermes WebSocket Gateway。",
                True,
            )
        except ValueError:
            yield TurnFailed(
                turn_id,
                "configuration",
                "Hermes 配置不完整，请检查 WebSocket 地址、令牌和证书。",
            )
        except Exception:
            yield TurnFailed(
                turn_id,
                "internal_error",
                "Hermes 连接发生了未预期错误。",
                True,
            )
        finally:
            self._active.pop(turn_id, None)
