"""发起端：正向 WebSocket 客户端。

主动连接对端（NapCat 的正向 WS 端口、SnowLuma 的 ``ws://host:3001/``，或 dsh
插件提供的 OneBot 服务端），在同一条连接上接收事件并发送 API 调用。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from meapet.log import get_color_logger
from meapet.onebot.transport.ws_session import WsSession

_log = get_color_logger("onebot")  # 与 service.py 同一个 logger，日志统一进 logs/onebot.log

try:  # websockets>=13 的新版 asyncio 客户端
    from websockets.asyncio.client import connect as _ws_connect
except Exception:  # pragma: no cover - 旧版回退
    from websockets import connect as _ws_connect  # type: ignore

OnEvent = Callable[[Mapping[str, Any], str], None]
OnStatus = Callable[[bool, str], None]


def _with_token(url: str, token: str) -> str:
    """把 access_token 附成 query 参数（部分实现端只认这种鉴权方式）。"""
    if not token:
        return url
    parts = urlsplit(url)
    query = parts.query
    extra = urlencode({"access_token": token})
    query = f"{query}&{extra}" if query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


async def _connect(url: str, headers: Mapping[str, str], timeout_s: float):
    """兼容 websockets 13/14/15 的 kwargs 差异。"""
    kwargs: dict = {"max_size": None, "open_timeout": timeout_s}
    try:
        return await _ws_connect(url, additional_headers=dict(headers), **kwargs)
    except TypeError:  # pragma: no cover - 老版本参数名
        return await _ws_connect(url, extra_headers=dict(headers), **kwargs)


async def _close_quietly(connection) -> None:
    """关闭连接；关闭过程中的异常不影响重连循环。"""
    close = getattr(connection, "close", None)
    if not callable(close):
        return
    try:
        await close()
    except Exception:
        pass


class ForwardWsClient:
    """带指数退避重连的正向 WS 客户端。"""

    mode = "forward_ws"
    name = "forward_ws"

    def __init__(
        self,
        config: Mapping[str, Any],
        on_event: OnEvent,
        on_status: Optional[OnStatus] = None,
        name: str = "forward_ws",
    ) -> None:
        self.name = name
        self.url = str(config.get("url") or "")
        self.access_token = str(config.get("access_token") or "")
        self.reconnect = bool(config.get("reconnect", True))
        self.initial_ms = int(config.get("reconnect_initial_ms", 1000))
        self.max_ms = int(config.get("reconnect_max_ms", 30000))
        self._on_event = on_event
        self._on_status = on_status
        self._session: Optional[WsSession] = None
        self._task: Optional[asyncio.Task] = None
        self.connected = False
        self.last_error = ""

    # -- 发送端接口 ------------------------------------------------------- #

    @property
    def available(self) -> bool:
        session = self._session
        return session is not None and session.available

    async def send(self, action: str, params: Mapping[str, Any]):
        session = self._session
        if session is None or not session.available:
            from meapet.onebot.types import ApiResponse

            return ApiResponse.failure("正向 WS 未连接")
        return await session.send(action, params)

    # -- 生命周期 --------------------------------------------------------- #

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.ensure_future(self.run())

    def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        session, self._session = self._session, None
        if session is not None:
            session.close("客户端停止")
        self.connected = False

    def _notify(self, connected: bool, detail: str) -> None:
        self.connected = connected
        if detail:
            self.last_error = "" if connected else detail
        if self._on_status is not None:
            try:
                self._on_status(connected, detail)
            except Exception:  # pragma: no cover - 状态回调不应影响连接
                pass

    async def run(self) -> None:
        delay_ms = max(100, self.initial_ms)
        while True:
            session: Optional[WsSession] = None
            try:
                headers = {}
                if self.access_token:
                    headers["Authorization"] = f"Bearer {self.access_token}"
                connection = await _connect(
                    _with_token(self.url, self.access_token), headers, 10.0
                )
                session = WsSession(self.name, connection.send)
                self._session = session
                delay_ms = max(100, self.initial_ms)
                _log.info("[onebot] 正向 WS 已连接：%s", self.url)
                self._notify(True, "")
                try:
                    async for raw in connection:
                        session.feed(raw, self._on_event)
                finally:
                    session.close("连接关闭")
                    if self._session is session:
                        self._session = None
                    # 取消/断开时显式关连接，否则对端要等超时才发现我们走了
                    await _close_quietly(connection)
            except asyncio.CancelledError:
                if session is not None:
                    session.close("已取消")
                raise
            except Exception as exc:
                self._notify(False, type(exc).__name__)
                _log.warning("[onebot] 正向 WS 连接失败：%s", type(exc).__name__)
                if session is not None:
                    session.close(type(exc).__name__)
                if self._session is session:
                    self._session = None
            else:
                self._notify(False, "连接关闭")

            if not self.reconnect:
                return
            try:
                await asyncio.sleep(delay_ms / 1000.0)
            except asyncio.CancelledError:
                raise
            delay_ms = min(self.max_ms, delay_ms * 2)
