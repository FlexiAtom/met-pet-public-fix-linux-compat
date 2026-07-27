"""OpenClaw 官方 Gateway WebSocket v4 适配器。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import locale
import platform as runtime_platform
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping

from meapet import __version__
from meapet.agent.base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.openclaw_identity import (
    OpenClawDeviceIdentity,
    build_device_auth_payload_v3,
)
from meapet.agent.prompts import (
    MAX_REPAIR_INPUT_CHARS,
    REPAIR_INSTRUCTION,
    gateway_user_message,
)
from meapet.agent.ws_transport import (
    ConnectionDropped,
    DEFAULT_MAX_MESSAGE_BYTES,
    IncomingFrame,
    PersistentJsonWebSocket,
    WebSocketDisconnected,
    receive_json_frame,
    send_json_frame,
    validate_websocket_url,
)
from meapet.conversation.output_protocol import (
    MeaPetOutputStreamParser,
    ProtocolCompleted,
    SegmentCompleted,
    parse_reply_output,
)
from meapet.paths import get_data_dir


_PROTOCOL_VERSION = 4
_PRECONNECT_MAX_BYTES = 64 * 1024
_DEFAULT_MAX_PAYLOAD_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_BUFFERED_BYTES = 50 * 1024 * 1024
_SCOPES = ("operator.read", "operator.write")
_CONTROL_CHARS = frozenset({"\r", "\n", "\x00"})
_CLIENT_ID = "gateway-client"
_CLIENT_MODE = "backend"


def _safe_identifier(name: str, value: object, *, required: bool = False) -> str:
    result = str(value or "").strip()
    if required and not result:
        raise ValueError(f"{name} is required")
    if len(result) > 512 or any(char in result for char in _CONTROL_CHARS):
        raise ValueError(f"{name} is not a safe identifier")
    return result


def _json_frame_size(frame: Mapping[str, object]) -> int:
    return len(
        json.dumps(
            frame,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _policy_byte_limit(value: object, default: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    if limit <= 0:
        limit = default
    return min(limit, DEFAULT_MAX_MESSAGE_BYTES)


@dataclass(frozen=True)
class OpenClawConfig:
    base_url: str = "ws://127.0.0.1:18789"
    auth_token: str = ""
    session_key: str = ""
    session_id: str = ""
    timeout_seconds: float = 120.0
    verify_tls: bool = True
    ca_file: str = ""
    allow_insecure_ws: bool = False
    identity_path: str = ""

    def __post_init__(self) -> None:
        normalized, _parsed = validate_websocket_url(
            self.base_url,
            allow_insecure_ws=self.allow_insecure_ws,
        )
        object.__setattr__(self, "base_url", normalized)
        object.__setattr__(self, "auth_token", str(self.auth_token or "").strip())
        object.__setattr__(
            self,
            "session_key",
            _safe_identifier("session_key", self.session_key, required=True),
        )
        object.__setattr__(
            self,
            "session_id",
            _safe_identifier("session_id", self.session_id),
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
        object.__setattr__(
            self,
            "identity_path",
            str(self.identity_path or "").strip(),
        )


@dataclass(frozen=True)
class OpenClawCapabilities:
    platform: str
    protocol: int
    server_version: str
    chat_send: bool
    chat_abort: bool
    max_payload_bytes: int = _DEFAULT_MAX_PAYLOAD_BYTES
    max_buffered_bytes: int = _DEFAULT_MAX_BUFFERED_BYTES
    methods: tuple[str, ...] = ()
    events: tuple[str, ...] = ()


class _GatewayFailure(Exception):
    def __init__(
        self,
        category: str,
        safe_message: str,
        retryable: bool = False,
    ) -> None:
        super().__init__(safe_message)
        self.category = category
        self.safe_message = safe_message
        self.retryable = retryable

    def event(self, turn_id: str) -> TurnFailed:
        return TurnFailed(
            turn_id,
            self.category,
            self.safe_message,
            self.retryable,
        )


def _gateway_error(error: object) -> _GatewayFailure:
    payload = error if isinstance(error, Mapping) else {}
    code = str(payload.get("code") or "").strip().upper()
    retryable = bool(payload.get("retryable", False))
    if "PAIR" in code or code in {"DEVICE_REQUIRED", "DEVICE_NOT_PAIRED"}:
        return _GatewayFailure(
            "permission",
            "OpenClaw 需要先批准此设备配对。",
        )
    if code in {"UNAUTHORIZED", "AUTHENTICATION_FAILED", "INVALID_TOKEN"}:
        return _GatewayFailure(
            "authentication",
            "OpenClaw 认证失败，请检查访问令牌。",
        )
    if code in {"FORBIDDEN", "PERMISSION_DENIED", "INSUFFICIENT_SCOPE"}:
        return _GatewayFailure("permission", "OpenClaw 拒绝了当前请求。")
    if code in {"RATE_LIMITED", "RATE_LIMIT", "TOO_MANY_REQUESTS"}:
        return _GatewayFailure(
            "rate_limit",
            "OpenClaw 请求过于频繁，请稍后再试。",
            True,
        )
    if code in {"UNAVAILABLE", "SERVICE_UNAVAILABLE", "STARTING"}:
        return _GatewayFailure(
            "backend_unavailable",
            "OpenClaw Gateway 暂时不可用。",
            True,
        )
    return _GatewayFailure(
        "protocol",
        "OpenClaw Gateway 返回了无法处理的响应。",
        retryable,
    )


def _chat_error(payload: Mapping[str, object]) -> _GatewayFailure:
    kind = str(payload.get("errorKind") or "unknown").strip().lower()
    if kind == "rate_limit":
        return _GatewayFailure(
            "rate_limit",
            "OpenClaw 请求过于频繁，请稍后再试。",
            True,
        )
    if kind == "timeout":
        return _GatewayFailure(
            "timeout",
            "OpenClaw 响应超时，请稍后再试。",
            True,
        )
    if kind == "context_length":
        return _GatewayFailure("context_length", "OpenClaw 当前会话内容过长。")
    if kind == "refusal":
        return _GatewayFailure("permission", "OpenClaw 拒绝了当前请求。")
    return _GatewayFailure("backend", "OpenClaw 未能完成本轮回复。")


def _status_event(event_name: str, payload: Mapping[str, object]) -> ToolStatus:
    state_value = str(
        payload.get("state") or payload.get("status") or "running"
    ).strip().lower()
    if state_value in {"completed", "complete", "succeeded", "success", "done"}:
        state = "succeeded"
    elif state_value in {"failed", "error", "cancelled", "aborted"}:
        state = "failed"
    else:
        state = "started"
    if event_name == "session.tool":
        text = {
            "started": "Agent 正在执行工具",
            "succeeded": "Agent 工具执行完成",
            "failed": "Agent 工具执行失败",
        }[state]
    else:
        text = {
            "started": "Agent 正在处理",
            "succeeded": "Agent 处理完成",
            "failed": "Agent 处理失败",
        }[state]
    return ToolStatus(state, text)


def _message_text(message: object) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks = []
    for item in content:
        if isinstance(item, Mapping) and isinstance(item.get("text"), str):
            chunks.append(item["text"])
    return "".join(chunks)


def _assistant_text_signature(text: object) -> str:
    """把协议原文与桌面端保存的展示文本归一为同一比较形式。"""
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


def _last_assistant_text(messages: object) -> str:
    if not isinstance(messages, (list, tuple)):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping):
            continue
        if str(message.get("role") or "").strip().lower() != "assistant":
            continue
        text = _message_text(message)
        if text.strip():
            return text
    return ""


@dataclass
class _PendingRequest:
    future: asyncio.Future
    generation: int
    expect_final: bool = False
    turn_id: str = ""


@dataclass
class _TurnState:
    turn_id: str
    session_key: str
    generation: int
    queue: asyncio.Queue[object] = field(default_factory=asyncio.Queue)
    run_id: str = ""
    last_seq: int = -1


@dataclass(frozen=True)
class _TurnNetworkItem:
    generation: int
    value: object


@dataclass(frozen=True)
class _RequestFailed:
    failure: _GatewayFailure


@dataclass(frozen=True)
class _ResponseCompleted:
    payload: Mapping[str, object]


class OpenClawAdapter:
    """持久连接到 OpenClaw Gateway；一轮内支持取消与幂等重连。"""

    def __init__(
        self,
        config: OpenClawConfig,
        *,
        connector: Callable[..., object] | None = None,
        identity: OpenClawDeviceIdentity | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.config = config
        self._identity = identity
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._capabilities: OpenClawCapabilities | None = None
        self._pending: dict[str, _PendingRequest] = {}
        self._active: dict[str, _TurnState] = {}
        self._cancelled_turns: set[str] = set()
        self._dispatch_task: asyncio.Task | None = None
        self._transport = PersistentJsonWebSocket(
            self.config.base_url,
            timeout_seconds=self.config.timeout_seconds,
            verify_tls=self.config.verify_tls,
            ca_file=self.config.ca_file,
            connector=connector,
            handshake=self._handshake,
        )

    def _get_identity(self) -> OpenClawDeviceIdentity:
        if self._identity is None:
            path = self.config.identity_path or str(
                Path(get_data_dir()) / "openclaw_device_identity.json"
            )
            self._identity = OpenClawDeviceIdentity.load_or_create(path)
        return self._identity

    async def _handshake(self, websocket: Any) -> OpenClawCapabilities:
        if not self.config.auth_token:
            raise _GatewayFailure(
                "configuration",
                "OpenClaw 配置不完整，请填写访问令牌。",
            )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(15.0, self.config.timeout_seconds)
        challenge = await receive_json_frame(
            websocket,
            timeout_seconds=max(0.1, deadline - loop.time()),
            max_message_bytes=_PRECONNECT_MAX_BYTES,
        )
        challenge_payload = challenge.get("payload")
        if (
            challenge.get("type") != "event"
            or challenge.get("event") != "connect.challenge"
            or not isinstance(challenge_payload, Mapping)
        ):
            raise _GatewayFailure("protocol", "OpenClaw 未发送连接挑战。")
        try:
            nonce = _safe_identifier(
                "challenge nonce",
                challenge_payload.get("nonce"),
                required=True,
            )
        except ValueError:
            raise _GatewayFailure(
                "protocol",
                "OpenClaw 连接挑战无效。",
            ) from None
        identity = self._get_identity()
        signed_at = int(self._clock_ms())
        system_name = runtime_platform.system().strip().lower() or "unknown"
        device_family = "desktop"
        signature_payload = build_device_auth_payload_v3(
            device_id=identity.device_id,
            client_id=_CLIENT_ID,
            client_mode=_CLIENT_MODE,
            role="operator",
            scopes=_SCOPES,
            signed_at_ms=signed_at,
            token=self.config.auth_token,
            nonce=nonce,
            platform=system_name,
            device_family=device_family,
        )
        language = locale.getlocale()[0] or "zh-CN"
        connect_frame = {
            "type": "req",
            "id": "connect",
            "method": "connect",
            "params": {
                "minProtocol": _PROTOCOL_VERSION,
                "maxProtocol": _PROTOCOL_VERSION,
                "client": {
                    "id": _CLIENT_ID,
                    "displayName": "MeaPet",
                    "version": __version__,
                    "platform": system_name,
                    "deviceFamily": device_family,
                    "mode": _CLIENT_MODE,
                },
                "caps": ["tool-events"],
                "commands": [],
                "permissions": {},
                "role": "operator",
                "scopes": list(_SCOPES),
                "auth": {"token": self.config.auth_token},
                "locale": language,
                "userAgent": f"MeaPet/{__version__}",
                "device": {
                    "id": identity.device_id,
                    "publicKey": identity.public_key,
                    "signature": identity.sign(signature_payload),
                    "signedAt": signed_at,
                    "nonce": nonce,
                },
            },
        }
        if _json_frame_size(connect_frame) > _PRECONNECT_MAX_BYTES:
            raise _GatewayFailure(
                "configuration",
                "OpenClaw 连接凭据超过握手载荷上限。",
            )
        await send_json_frame(websocket, connect_frame)
        for _ in range(32):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            response = await receive_json_frame(
                websocket,
                timeout_seconds=remaining,
                max_message_bytes=_PRECONNECT_MAX_BYTES,
            )
            if response.get("type") != "res" or response.get("id") != "connect":
                continue
            if not response.get("ok"):
                raise _gateway_error(response.get("error"))
            hello = response.get("payload")
            if not isinstance(hello, Mapping) or hello.get("type") != "hello-ok":
                raise _GatewayFailure("protocol", "OpenClaw 握手响应无效。")
            try:
                protocol = int(hello.get("protocol") or 0)
            except (TypeError, ValueError):
                protocol = 0
            if protocol != _PROTOCOL_VERSION:
                raise _GatewayFailure("protocol", "OpenClaw 协议版本不兼容。")
            features = hello.get("features")
            features = features if isinstance(features, Mapping) else {}
            raw_methods = features.get("methods", ())
            if not isinstance(raw_methods, (list, tuple)):
                raw_methods = ()
            methods = tuple(
                str(value)
                for value in raw_methods
                if isinstance(value, str)
            )
            raw_events = features.get("events", ())
            if not isinstance(raw_events, (list, tuple)):
                raw_events = ()
            events = tuple(
                str(value)
                for value in raw_events
                if isinstance(value, str)
            )
            server = hello.get("server")
            server = server if isinstance(server, Mapping) else {}
            policy = hello.get("policy")
            policy = policy if isinstance(policy, Mapping) else {}
            capabilities = OpenClawCapabilities(
                platform="openclaw",
                protocol=protocol,
                server_version=str(server.get("version") or ""),
                chat_send="chat.send" in methods,
                chat_abort="chat.abort" in methods,
                max_payload_bytes=_policy_byte_limit(
                    policy.get("maxPayload"),
                    _DEFAULT_MAX_PAYLOAD_BYTES,
                ),
                max_buffered_bytes=_policy_byte_limit(
                    policy.get("maxBufferedBytes"),
                    _DEFAULT_MAX_BUFFERED_BYTES,
                ),
                methods=methods,
                events=events,
            )
            self._capabilities = capabilities
            return capabilities
        raise _GatewayFailure("protocol", "OpenClaw 未完成连接握手。")

    async def _ensure_connected(self) -> int:
        generation = await self._transport.ensure_connected()
        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.create_task(
                self._dispatch_frames(),
                name="meapet-openclaw-dispatch",
            )
        if self._capabilities is None or not self._capabilities.chat_send:
            raise _GatewayFailure(
                "protocol",
                "OpenClaw Gateway 未提供 chat.send。",
            )
        return generation

    async def _dispatch_frames(self) -> None:
        while True:
            incoming = await self._transport.frames.get()
            if isinstance(incoming, ConnectionDropped):
                failure = WebSocketDisconnected(
                    "OpenClaw Gateway connection dropped"
                )
                for request_id, pending in tuple(self._pending.items()):
                    if pending.generation != incoming.generation:
                        continue
                    self._pending.pop(request_id, None)
                    if not pending.future.done():
                        if pending.expect_final and pending.turn_id:
                            pending.future.cancel()
                        else:
                            pending.future.set_exception(failure)
                for state in tuple(self._active.values()):
                    if state.generation == incoming.generation:
                        await state.queue.put(incoming)
                continue
            frame = incoming.payload
            if frame.get("type") == "res":
                await self._dispatch_response(incoming.generation, frame)
            elif frame.get("type") == "event":
                await self._dispatch_event(incoming.generation, frame)

    async def _dispatch_response(
        self,
        generation: int,
        frame: Mapping[str, object],
    ) -> None:
        request_id = str(frame.get("id") or "")
        pending = self._pending.get(request_id)
        if pending is None or pending.generation != generation:
            return
        state = self._active.get(pending.turn_id) if pending.turn_id else None
        if not frame.get("ok"):
            self._pending.pop(request_id, None)
            failure = _gateway_error(frame.get("error"))
            if not pending.future.done():
                if pending.expect_final and pending.turn_id:
                    pending.future.cancel()
                else:
                    pending.future.set_exception(failure)
            if state is not None:
                await state.queue.put(
                    _TurnNetworkItem(
                        generation,
                        _RequestFailed(failure),
                    )
                )
            return
        payload = frame.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        try:
            run_id = _safe_identifier("run_id", payload.get("runId"))
        except ValueError:
            self._pending.pop(request_id, None)
            failure = _GatewayFailure(
                "protocol",
                "OpenClaw Gateway 返回了无效的运行标识。",
            )
            if not pending.future.done():
                if pending.expect_final and pending.turn_id:
                    pending.future.cancel()
                else:
                    pending.future.set_exception(failure)
            if state is not None:
                await state.queue.put(
                    _TurnNetworkItem(
                        generation,
                        _RequestFailed(failure),
                    )
                )
            return
        if state is not None and run_id:
            state.run_id = run_id
        status = str(payload.get("status") or "").strip().lower()
        waiting_status = {
            "accepted",
            "started",
            "streaming",
            "in_flight",
            "in-flight",
            "running",
        }
        if pending.expect_final and status in waiting_status:
            return
        self._pending.pop(request_id, None)
        if not pending.future.done():
            pending.future.set_result(payload)
        if state is not None and pending.expect_final:
            await state.queue.put(
                _TurnNetworkItem(
                    generation,
                    _ResponseCompleted(payload),
                )
            )

    async def _dispatch_event(
        self,
        generation: int,
        frame: Mapping[str, object],
    ) -> None:
        event_name = str(frame.get("event") or "")
        payload = frame.get("payload")
        if not isinstance(payload, Mapping):
            return
        event_session = str(payload.get("sessionKey") or "")
        try:
            run_id = _safe_identifier("run_id", payload.get("runId"))
        except ValueError:
            return
        for state in tuple(self._active.values()):
            if state.generation != generation:
                continue
            if event_session and event_session != state.session_key:
                continue
            if run_id and state.run_id and run_id != state.run_id:
                continue
            if run_id and not state.run_id:
                state.run_id = run_id
            if event_name in {"session.operation", "session.tool"}:
                value: object = _status_event(event_name, payload)
            elif event_name == "chat":
                value = payload
            else:
                continue
            await state.queue.put(_TurnNetworkItem(generation, value))

    async def _send_request(
        self,
        request_id: str,
        method: str,
        params: Mapping[str, object],
        *,
        generation: int,
        expect_final: bool = False,
        turn_id: str = "",
    ) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        old = self._pending.pop(request_id, None)
        if old is not None and not old.future.done():
            old.future.cancel()
        frame = {
            "type": "req",
            "id": request_id,
            "method": method,
            "params": dict(params),
        }
        capabilities = self._capabilities
        max_payload = (
            capabilities.max_payload_bytes
            if capabilities is not None
            else _DEFAULT_MAX_PAYLOAD_BYTES
        )
        if _json_frame_size(frame) > max_payload:
            raise _GatewayFailure(
                "payload_too_large",
                "发送内容超过 OpenClaw Gateway 的载荷上限。",
            )
        future = loop.create_future()
        self._pending[request_id] = _PendingRequest(
            future,
            generation,
            expect_final=expect_final,
            turn_id=turn_id,
        )
        try:
            await self._transport.send_json(
                frame,
                generation=generation,
            )
        except BaseException:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()
            raise
        return future

    async def _call_request(
        self,
        method: str,
        params: Mapping[str, object],
    ) -> Mapping[str, object]:
        """执行普通 Gateway RPC，并在超时后完整清理关联状态。"""
        generation = await self._ensure_connected()
        request_id = f"rpc:{method}:{uuid.uuid4().hex}"
        future = await self._send_request(
            request_id,
            method,
            params,
            generation=generation,
        )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self.config.timeout_seconds,
            )
            return result if isinstance(result, Mapping) else {}
        finally:
            pending = self._pending.pop(request_id, None)
            if pending is not None and not pending.future.done():
                pending.future.cancel()

    async def _recover_completed_reply(
        self,
        request: AgentTurnRequest,
        *,
        session_key: str,
    ) -> str:
        """恢复已完成、但终态事件在断线期间丢失的回复。"""
        capabilities = self._capabilities
        if capabilities is None or "chat.history" not in capabilities.methods:
            return ""
        payload = await self._call_request(
            "chat.history",
            {"sessionKey": session_key, "limit": 20},
        )
        recovered = _last_assistant_text(payload.get("messages"))
        if not recovered:
            return ""
        previous = _last_assistant_text(request.history)
        if previous and (
            _assistant_text_signature(previous)
            == _assistant_text_signature(recovered)
        ):
            return ""
        return recovered

    def _chat_params(
        self,
        request: AgentTurnRequest,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
        include_attachments: bool,
    ) -> dict[str, object]:
        params: dict[str, object] = {
            "sessionKey": session_key,
            "message": message,
            "deliver": False,
            "timeoutMs": max(0, int(self.config.timeout_seconds * 1000)),
            "idempotencyKey": idempotency_key,
        }
        if self.config.session_id and session_key == self.config.session_key:
            params["sessionId"] = self.config.session_id
        if include_attachments and request.attachments:
            params["attachments"] = [
                {
                    "type": "image",
                    "mimeType": attachment.media_type,
                    "fileName": attachment.file_name,
                    "content": attachment.data,
                }
                for attachment in request.attachments
            ]
        return params

    async def _start_chat(
        self,
        state: _TurnState,
        request: AgentTurnRequest,
        *,
        message: str,
        idempotency_key: str,
        include_attachments: bool,
        request_id: str,
    ) -> None:
        generation = await self._ensure_connected()
        state.generation = generation
        await self._send_request(
            request_id,
            "chat.send",
            self._chat_params(
                request,
                session_key=state.session_key,
                message=message,
                idempotency_key=idempotency_key,
                include_attachments=include_attachments,
            ),
            generation=generation,
            expect_final=True,
            turn_id=state.turn_id,
        )

    async def _reconnect_turn(
        self,
        state: _TurnState,
        request: AgentTurnRequest,
        *,
        message: str,
        idempotency_key: str,
        include_attachments: bool,
        request_id: str,
    ) -> bool:
        for delay in (0.0, 0.25, 0.75, 1.5):
            if request.turn_id in self._cancelled_turns:
                return False
            if delay:
                await asyncio.sleep(delay)
            try:
                generation = await self._transport.reconnect(
                    expected_generation=state.generation
                )
                if self._dispatch_task is None or self._dispatch_task.done():
                    self._dispatch_task = asyncio.create_task(
                        self._dispatch_frames(),
                        name="meapet-openclaw-dispatch",
                    )
                state.generation = generation
                await self._send_request(
                    request_id,
                    "chat.send",
                    self._chat_params(
                        request,
                        session_key=state.session_key,
                        message=message,
                        idempotency_key=idempotency_key,
                        include_attachments=include_attachments,
                    ),
                    generation=generation,
                    expect_final=True,
                    turn_id=state.turn_id,
                )
                return True
            except (
                _GatewayFailure,
                WebSocketDisconnected,
                OSError,
                TimeoutError,
            ):
                continue
        return False

    async def probe(self) -> OpenClawCapabilities:
        try:
            await self._ensure_connected()
        except _GatewayFailure as exc:
            raise ValueError(exc.safe_message) from None
        except (OSError, TimeoutError, WebSocketDisconnected) as exc:
            raise ValueError("无法连接 OpenClaw Gateway") from exc
        except Exception as exc:
            raise ValueError("OpenClaw Gateway 握手失败") from exc
        assert self._capabilities is not None
        return self._capabilities

    async def cancel_turn(self, turn_id: str) -> None:
        safe_turn_id = _safe_identifier("turn_id", turn_id, required=True)
        self._cancelled_turns.add(safe_turn_id)
        state = self._active.get(safe_turn_id)
        if state is None:
            return
        await state.queue.put(TurnCancelled(safe_turn_id))
        capabilities = self._capabilities
        if capabilities is not None and not capabilities.chat_abort:
            return
        params: dict[str, object] = {"sessionKey": state.session_key}
        if state.run_id:
            params["runId"] = state.run_id
        request_id = f"abort:{safe_turn_id}"
        try:
            generation = await self._ensure_connected()
            future = await self._send_request(
                request_id,
                "chat.abort",
                params,
                generation=generation,
            )
            await asyncio.wait_for(
                asyncio.shield(future),
                timeout=min(2.0, self.config.timeout_seconds),
            )
        except (
            asyncio.TimeoutError,
            _GatewayFailure,
            WebSocketDisconnected,
            OSError,
        ):
            pass
        finally:
            pending = self._pending.pop(request_id, None)
            if pending is not None and not pending.future.done():
                pending.future.cancel()

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

    def _repair_session_key(self, turn_id: str) -> str:
        scope = hashlib.sha256(
            f"{self.config.session_key}\x00{turn_id}".encode("utf-8")
        ).hexdigest()[:24]
        return f"agent:main:meapet:format-repair:{scope}"

    async def _repair_result(
        self,
        request: AgentTurnRequest,
        malformed_output: str,
    ):
        repair_turn_id = f"repair:{request.turn_id}"
        state = _TurnState(
            repair_turn_id,
            self._repair_session_key(request.turn_id),
            self._transport.generation,
        )
        self._active[repair_turn_id] = state
        parser = MeaPetOutputStreamParser()
        try:
            await self._start_chat(
                state,
                request,
                message=(
                    f"{REPAIR_INSTRUCTION}\n\n待转换原文：\n"
                    f"{malformed_output[:MAX_REPAIR_INPUT_CHARS]}"
                ),
                idempotency_key=f"{request.turn_id}-format-repair",
                include_attachments=False,
                request_id=repair_turn_id,
            )
            while True:
                item = await asyncio.wait_for(
                    state.queue.get(),
                    timeout=self.config.timeout_seconds,
                )
                if isinstance(item, _TurnNetworkItem):
                    if item.generation != state.generation:
                        continue
                    item = item.value
                if isinstance(item, Mapping):
                    event_state = str(item.get("state") or "").lower()
                    if event_state == "delta":
                        delta = item.get("deltaText")
                        if isinstance(delta, str):
                            parser.feed(delta)
                    elif event_state == "final":
                        final_text = _message_text(item.get("message"))
                        if final_text:
                            parser.feed(final_text)
                        result = parser.close(tts_enabled=request.tts_enabled)
                        if not result.requires_repair(
                            tts_enabled=request.tts_enabled
                        ):
                            return result
                        return None
                    elif event_state in {"aborted", "error"}:
                        return None
                elif isinstance(item, (ConnectionDropped, _RequestFailed)):
                    return None
        except (asyncio.TimeoutError, WebSocketDisconnected, _GatewayFailure):
            return None
        finally:
            self._active.pop(repair_turn_id, None)
            pending = self._pending.pop(repair_turn_id, None)
            if pending is not None and not pending.future.done():
                pending.future.cancel()

    async def stream_turn(self, request: AgentTurnRequest) -> AsyncIterator[object]:
        turn_id = request.turn_id
        if turn_id in self._cancelled_turns:
            self._cancelled_turns.discard(turn_id)
            yield TurnCancelled(turn_id)
            return

        state = _TurnState(
            turn_id,
            self.config.session_key,
            self._transport.generation,
        )
        self._active[turn_id] = state
        parser = MeaPetOutputStreamParser()
        raw_output = ""
        completed_indices: set[int] = set()
        protocol_completed_emitted = False
        suppress_stream = False
        message = gateway_user_message(request)
        request_id = f"send:{turn_id}"
        try:
            await self._start_chat(
                state,
                request,
                message=message,
                idempotency_key=turn_id,
                include_attachments=True,
                request_id=request_id,
            )
            while True:
                item = await asyncio.wait_for(
                    state.queue.get(),
                    timeout=self.config.timeout_seconds,
                )
                if isinstance(item, _TurnNetworkItem):
                    if item.generation != state.generation:
                        continue
                    item = item.value
                if isinstance(item, TurnCancelled):
                    self._cancelled_turns.discard(turn_id)
                    yield TurnCancelled(turn_id)
                    return
                if isinstance(item, ToolStatus):
                    yield item
                    continue
                if isinstance(item, ConnectionDropped):
                    recovered = await self._reconnect_turn(
                        state,
                        request,
                        message=message,
                        idempotency_key=turn_id,
                        include_attachments=True,
                        request_id=request_id,
                    )
                    if not recovered:
                        yield TurnFailed(
                            turn_id,
                            "connection",
                            "OpenClaw 连接中断，且未能恢复本轮会话。",
                            True,
                        )
                        return
                    continue
                if isinstance(item, _RequestFailed):
                    raise item.failure
                if isinstance(item, _ResponseCompleted):
                    status = str(
                        item.payload.get("status") or ""
                    ).strip().lower()
                    if status not in {"ok", "complete", "completed", "done"}:
                        continue
                    # 初次 chat.send 只回 started；重连后用同一幂等键重发，
                    # 已完成请求会直接回 ok，但错过的 chat 事件不会重播。
                    # 此时从持久化历史对账恢复，避免重复执行或空等超时。
                    if "<MEAPET_DONE" in raw_output.upper():
                        break
                    recovered = await self._recover_completed_reply(
                        request,
                        session_key=state.session_key,
                    )
                    if recovered:
                        raw_output = recovered
                        parser = MeaPetOutputStreamParser()
                        parser.feed(recovered)
                        completed_indices.clear()
                        protocol_completed_emitted = False
                        suppress_stream = True
                        break
                    continue
                if not isinstance(item, Mapping):
                    continue
                sequence = item.get("seq")
                if isinstance(sequence, int):
                    if sequence <= state.last_seq:
                        continue
                    state.last_seq = sequence
                event_state = str(item.get("state") or "").strip().lower()
                if event_state == "delta":
                    delta = item.get("deltaText")
                    if not isinstance(delta, str) or not delta:
                        continue
                    if bool(item.get("replace")):
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
                            "OpenClaw 输出超过长度上限，已中止本回合。",
                        )
                        return
                    continue
                if event_state == "final":
                    if not raw_output:
                        final_text = _message_text(item.get("message"))
                        if final_text:
                            raw_output = final_text
                            parser.feed(final_text)
                    break
                if event_state == "aborted":
                    yield TurnCancelled(turn_id)
                    return
                if event_state == "error":
                    raise _chat_error(item)

            result = parser.close(tts_enabled=request.tts_enabled)
            if result.requires_repair(tts_enabled=request.tts_enabled):
                yield FormatRepairRequired(result)
                repaired = await self._repair_result(request, raw_output)
                if turn_id in self._cancelled_turns:
                    self._cancelled_turns.discard(turn_id)
                    yield TurnCancelled(turn_id)
                    return
                if repaired is not None:
                    result = repaired
            if not any(
                segment.display_text.strip() for segment in result.segments
            ):
                yield TurnFailed(
                    turn_id,
                    "protocol",
                    "OpenClaw 没有返回可展示的回复。",
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
        except _GatewayFailure as exc:
            yield exc.event(turn_id)
        except (asyncio.TimeoutError, TimeoutError):
            yield TurnFailed(
                turn_id,
                "timeout",
                "OpenClaw 响应超时，请稍后再试。",
                True,
            )
        except (WebSocketDisconnected, OSError):
            yield TurnFailed(
                turn_id,
                "connection",
                "无法连接 OpenClaw Gateway，请检查地址和网络。",
                True,
            )
        except ValueError:
            yield TurnFailed(
                turn_id,
                "configuration",
                "OpenClaw 配置不完整，请检查地址、令牌和证书。",
            )
        except Exception:
            yield TurnFailed(
                turn_id,
                "internal_error",
                "OpenClaw 连接发生了未预期错误。",
                True,
            )
        finally:
            self._active.pop(turn_id, None)
            pending = self._pending.pop(request_id, None)
            if pending is not None and not pending.future.done():
                pending.future.cancel()
