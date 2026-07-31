"""Agent 适配器共用的持久 JSON WebSocket 传输层。"""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import SplitResult, urlsplit, urlunsplit

from websockets.exceptions import ConnectionClosed, WebSocketException


DEFAULT_MAX_MESSAGE_BYTES = 100 * 1024 * 1024
_LOCAL_HOSTS = frozenset({"localhost", "localhost.localdomain"})


def is_loopback_host(hostname: str) -> bool:
    value = str(hostname or "").strip().lower().rstrip(".")
    if value in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def validate_websocket_url(
    value: object,
    *,
    allow_insecure_ws: bool = False,
    allow_query: bool = False,
) -> tuple[str, SplitResult]:
    """校验 Agent WebSocket 地址并返回不含多余尾斜杠的规范形式。"""
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme.lower() not in {"ws", "wss"} or not parsed.netloc:
        raise ValueError("Agent endpoint must be a WebSocket ws(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError(
            "Agent WebSocket URL must not contain credentials or a fragment"
        )
    if parsed.query and not allow_query:
        raise ValueError(
            "Agent WebSocket URL must not contain a query; configure the token separately"
        )
    if (
        parsed.scheme.lower() == "ws"
        and not is_loopback_host(parsed.hostname or "")
        and not bool(allow_insecure_ws)
    ):
        raise ValueError(
            "remote plaintext WebSocket requires allow_insecure_ws=true"
        )
    path = parsed.path.rstrip("/")
    normalized = urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            path,
            parsed.query if allow_query else "",
            "",
        )
    )
    return normalized, urlsplit(normalized)


def websocket_connection_kwargs(
    url: str,
    *,
    timeout_seconds: float,
    verify_tls: bool,
    ca_file: str,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> dict[str, object]:
    """构造 websockets 13–15 均支持的连接参数。"""
    timeout = max(0.1, float(timeout_seconds))
    kwargs: dict[str, object] = {
        "open_timeout": min(timeout, 15.0),
        "close_timeout": 5.0,
        "max_size": max(1024, int(max_message_bytes)),
        "ping_interval": 20.0,
        "ping_timeout": 20.0,
    }
    if str(url).lower().startswith("wss://"):
        if ca_file:
            ca_path = Path(ca_file).expanduser()
            if not ca_path.is_file():
                raise ValueError("Agent CA certificate file does not exist")
            context = ssl.create_default_context(cafile=str(ca_path))
        else:
            context = ssl.create_default_context()
        if not verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        kwargs["ssl"] = context
    return kwargs


async def receive_json_frame(
    websocket: Any,
    *,
    timeout_seconds: float | None = None,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> dict:
    receive = websocket.recv()
    if timeout_seconds is not None:
        raw = await asyncio.wait_for(receive, timeout=max(0.1, timeout_seconds))
    else:
        raw = await receive
    if not isinstance(raw, str):
        raise WebSocketProtocolError("Agent returned a non-text WebSocket frame")
    if len(raw.encode("utf-8")) > max_message_bytes:
        raise WebSocketProtocolError("Agent WebSocket frame exceeds the safety limit")
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WebSocketProtocolError("Agent returned invalid JSON") from exc
    if not isinstance(frame, dict):
        raise WebSocketProtocolError("Agent returned an invalid JSON frame")
    return frame


async def send_json_frame(websocket: Any, frame: Mapping[str, object]) -> None:
    await websocket.send(
        json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
    )


class WebSocketProtocolError(RuntimeError):
    pass


class WebSocketDisconnected(ConnectionError):
    pass


@dataclass(frozen=True)
class IncomingFrame:
    generation: int
    payload: dict


@dataclass(frozen=True)
class ConnectionDropped:
    generation: int
    error: BaseException


Handshake = Callable[[Any], Awaitable[object]]


class PersistentJsonWebSocket:
    """一个 loop 内复用的 WebSocket；协议关联由上层适配器负责。

    连接本身只负责安全 URL/TLS、ping/pong、单写者、JSON 解码和连接代际。
    断线不会在后台无限重试；正在等待的上层回合收到 ``ConnectionDropped``
    后，按各自协议的幂等/恢复规则决定是否重连和重放。
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_seconds: float,
        verify_tls: bool = True,
        ca_file: str = "",
        connector: Callable[..., object] | None = None,
        handshake: Handshake | None = None,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        if connector is None:
            from websockets.asyncio.client import connect

            connector = connect
        self.url = str(url)
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.verify_tls = bool(verify_tls)
        self.ca_file = str(ca_file or "").strip()
        self.max_message_bytes = max(1024, int(max_message_bytes))
        self._connector = connector
        self._handshake = handshake
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._websocket: Any | None = None
        self._connection_context: Any | None = None
        self._reader_task: asyncio.Task | None = None
        self._generation = 0
        self._dropped_generation = 0
        self._closed = False
        self.frames: asyncio.Queue[IncomingFrame | ConnectionDropped] = (
            asyncio.Queue()
        )

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def connected(self) -> bool:
        return (
            not self._closed
            and self._websocket is not None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    async def ensure_connected(self) -> int:
        if self.connected:
            return self._generation
        async with self._connect_lock:
            if self.connected:
                return self._generation
            if self._closed:
                raise WebSocketDisconnected("Agent WebSocket has been closed")
            await self._dispose_connection()
            try:
                websocket, context = await self._open_connection()
            except asyncio.CancelledError:
                raise
            except (
                OSError,
                TimeoutError,
                WebSocketException,
            ) as exc:
                raise WebSocketDisconnected(
                    "Agent WebSocket connection failed"
                ) from exc
            try:
                if self._handshake is not None:
                    await self._handshake(websocket)
            except asyncio.CancelledError:
                await self._close_opened(websocket, context)
                raise
            except (
                OSError,
                TimeoutError,
                WebSocketException,
            ) as exc:
                await self._close_opened(websocket, context)
                raise WebSocketDisconnected(
                    "Agent WebSocket handshake failed"
                ) from exc
            except BaseException:
                await self._close_opened(websocket, context)
                raise
            self._generation += 1
            generation = self._generation
            self._websocket = websocket
            self._connection_context = context
            self._reader_task = asyncio.create_task(
                self._reader_loop(websocket, generation),
                name=f"meapet-agent-ws-{generation}",
            )
            return generation

    async def send_json(
        self,
        frame: Mapping[str, object],
        *,
        generation: int | None = None,
    ) -> int:
        current = await self.ensure_connected()
        if generation is not None and generation != current:
            raise WebSocketDisconnected("Agent WebSocket generation changed")
        async with self._send_lock:
            websocket = self._websocket
            if websocket is None or current != self._generation:
                raise WebSocketDisconnected("Agent WebSocket is disconnected")
            try:
                await send_json_frame(websocket, frame)
            except (ConnectionClosed, OSError, RuntimeError) as exc:
                await self._publish_drop(current, exc)
                raise WebSocketDisconnected("Agent WebSocket send failed") from exc
        return current

    async def reconnect(self, *, expected_generation: int | None = None) -> int:
        async with self._connect_lock:
            if (
                expected_generation is not None
                and self.connected
                and self._generation != expected_generation
            ):
                return self._generation
            await self._dispose_connection()
        return await self.ensure_connected()

    async def close(self) -> None:
        self._closed = True
        async with self._connect_lock:
            await self._dispose_connection()

    async def _open_connection(self) -> tuple[Any, Any | None]:
        kwargs = websocket_connection_kwargs(
            self.url,
            timeout_seconds=self.timeout_seconds,
            verify_tls=self.verify_tls,
            ca_file=self.ca_file,
            max_message_bytes=self.max_message_bytes,
        )
        # websockets 15 默认读取系统代理；本机 Agent 不应因为代理环境变量
        # 绕到 HTTP 代理。旧版 websockets 没有 proxy 参数，因此按签名兼容。
        if is_loopback_host(urlsplit(self.url).hostname or ""):
            try:
                supports_proxy = "proxy" in inspect.signature(
                    self._connector
                ).parameters
            except (TypeError, ValueError):
                supports_proxy = False
            if supports_proxy:
                kwargs["proxy"] = None
        connection = self._connector(self.url, **kwargs)
        if hasattr(connection, "__aenter__"):
            websocket = await connection.__aenter__()
            return websocket, connection
        if inspect.isawaitable(connection):
            websocket = await connection
            return websocket, None
        return connection, None

    async def _reader_loop(self, websocket: Any, generation: int) -> None:
        try:
            while (
                not self._closed
                and self._websocket is websocket
                and self._generation == generation
            ):
                frame = await receive_json_frame(
                    websocket,
                    max_message_bytes=self.max_message_bytes,
                )
                await self.frames.put(IncomingFrame(generation, frame))
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._publish_drop(generation, exc)

    async def _publish_drop(
        self,
        generation: int,
        error: BaseException,
    ) -> None:
        if (
            generation != self._generation
            or generation == self._dropped_generation
        ):
            return
        self._dropped_generation = generation
        self._websocket = None
        await self.frames.put(ConnectionDropped(generation, error))

    async def _dispose_connection(self) -> None:
        reader = self._reader_task
        self._reader_task = None
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        websocket = self._websocket
        context = self._connection_context
        self._websocket = None
        self._connection_context = None
        if websocket is not None or context is not None:
            await self._close_opened(websocket, context)

    @staticmethod
    async def _close_opened(websocket: Any, context: Any | None) -> None:
        if context is not None:
            try:
                await context.__aexit__(None, None, None)
                return
            except Exception:
                return
        if websocket is not None:
            try:
                await websocket.close()
            except Exception:
                pass
