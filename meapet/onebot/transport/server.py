"""接收端：反向 WebSocket、事件 HTTP POST，以及 hub 下游端点。

这三种「对端连我们」的通信方式默认共用同一套 ``host:port``，因此合并到一个
ASGI 应用里，用不同 path 区分；只有配置里写了不同端口时才会起多个 uvicorn。

鉴权按 OneBot 规范：``Authorization: Bearer <token>`` 优先，其次
``?access_token=``；token 为空表示不鉴权。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
from typing import Any, Awaitable, Callable, Mapping, Optional

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from meapet.log import get_color_logger
from meapet.onebot.transport.ws_session import WsSession
from meapet.onebot.types import ApiResponse

_log = get_color_logger("onebot")  # 与 service.py 同一个 logger，日志统一进 logs/onebot.log

MAX_BODY_BYTES = 8 * 1024 * 1024

#: 非阻塞停止时给 uvicorn 自行收尾的宽限窗口（秒）
GRACEFUL_STOP_SECONDS = 2.0

OnEvent = Callable[[Mapping[str, Any], str], None]
OnHubCall = Callable[[str, Mapping[str, Any]], Awaitable[ApiResponse]]
OnSession = Callable[[WsSession], None]


def _failed_payload(retcode: int = 1, echo: Any = None) -> dict:
    return {"status": "failed", "retcode": retcode, "data": None, "echo": echo}


def _authorized(headers: Mapping[str, str], query: Mapping[str, str], token: str) -> bool:
    if not token:
        return True
    raw = ""
    for key, value in (headers or {}).items():
        if str(key).lower() == "authorization":
            raw = str(value or "")
            break
    if raw.lower().startswith("bearer "):
        supplied = raw[7:].strip()
        if supplied and secrets.compare_digest(supplied, token):
            return True
    supplied = str((query or {}).get("access_token") or "")
    return bool(supplied) and secrets.compare_digest(supplied, token)


class OneBotServer:
    """一组「对端连我们」的端点，跑在 MeaPet 共用 asyncio 守护线程上。"""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        on_upstream_event: OnEvent,
        on_hub_call: OnHubCall,
        name: str = "onebot",
        reverse_ws_path: Optional[str] = None,
        reverse_ws_token: str = "",
        http_post_path: Optional[str] = None,
        http_post_token: str = "",
        hub_ws_path: Optional[str] = None,
        hub_http_path: Optional[str] = None,
        hub_token: str = "",
        on_upstream_open: Optional[OnSession] = None,
        on_upstream_closed: Optional[OnSession] = None,
        on_hub_open: Optional[OnSession] = None,
        on_hub_closed: Optional[OnSession] = None,
    ) -> None:
        self.name = name
        self.host = host
        self.port = port
        self.reverse_ws_path = reverse_ws_path
        self.reverse_ws_token = reverse_ws_token or ""
        self.http_post_path = http_post_path
        self.http_post_token = http_post_token or ""
        self.hub_ws_path = hub_ws_path
        self.hub_http_path = hub_http_path
        self.hub_token = hub_token or ""
        self._on_upstream_event = on_upstream_event
        self._on_hub_call = on_hub_call
        self._on_upstream_open = on_upstream_open
        self._on_upstream_closed = on_upstream_closed
        self._on_hub_open = on_hub_open
        self._on_hub_closed = on_hub_closed

        self.last_error = ""
        self._future = None
        self._server = None
        self.app = self._build_app()

    # -- 判定 ------------------------------------------------------------- #

    @property
    def running(self) -> bool:
        return self._future is not None and not self._future.done()

    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    # -- ASGI 应用 -------------------------------------------------------- #

    def _build_app(self) -> Starlette:
        routes: list = []
        if self.reverse_ws_path:
            routes.append(WebSocketRoute(self.reverse_ws_path, self._upstream_ws))
        if self.hub_ws_path:
            routes.append(WebSocketRoute(self.hub_ws_path, self._hub_ws))
        if self.http_post_path:
            routes.append(Route(self.http_post_path, self._upstream_http, methods=["POST"]))
        if self.hub_http_path:
            routes.append(Route(self.hub_http_path, self._hub_http, methods=["POST"]))
        return Starlette(routes=routes)

    # -- 上游（NapCat / SnowLuma）：对端连进来 ----------------------------- #

    async def _upstream_ws(self, websocket: WebSocket) -> None:
        if not _authorized(websocket.headers, websocket.query_params, self.reverse_ws_token):
            _log.warning("[onebot] 反向 WS 鉴权失败，已拒绝连接")
            await websocket.close(code=1008)
            return
        await websocket.accept()
        session = WsSession(
            f"{self.name}:upstream:{id(websocket)}", websocket.send_text
        )
        _log.info("[onebot] 反向 WS 已接入：%s", session.name)
        if self._on_upstream_open is not None:
            self._on_upstream_open(session)
        try:
            while True:
                raw = await websocket.receive_text()
                session.feed(raw, self._on_upstream_event)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover - 连接异常
            self.last_error = type(exc).__name__
        finally:
            session.close("上游断开")
            if self._on_upstream_closed is not None:
                self._on_upstream_closed(session)

    async def _upstream_http(self, request) -> JSONResponse:
        if not _authorized(request.headers, request.query_params, self.http_post_token):
            return JSONResponse(_failed_payload(104), status_code=401)
        raw = await self._read_body(request)
        if raw is None:
            return JSONResponse(_failed_payload(102), status_code=413)
        try:
            payload = json.loads(raw)
        except Exception:
            return JSONResponse(_failed_payload(102), status_code=400)
        if not isinstance(payload, Mapping):
            # 事件必然是对象；这里如果返回 ok，对端会以为投递成功而其实被丢掉了
            return JSONResponse(_failed_payload(102), status_code=400)
        self._on_upstream_event(payload, "http_post")
        return JSONResponse({"status": "ok", "retcode": 0, "data": None})

    # -- hub（dsh 插件等下游消费者） --------------------------------------- #

    async def _hub_ws(self, websocket: WebSocket) -> None:
        if not _authorized(websocket.headers, websocket.query_params, self.hub_token):
            _log.warning("[onebot] hub WS 鉴权失败，已拒绝连接")
            await websocket.close(code=1008)
            return
        await websocket.accept()
        session = WsSession(f"{self.name}:hub:{id(websocket)}", websocket.send_text)
        _log.info("[onebot] hub WS 已接入：%s", session.name)
        if self._on_hub_open is not None:
            self._on_hub_open(session)
        try:
            while True:
                raw = await websocket.receive_text()
                await self._serve_hub_frame(session, raw)
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # pragma: no cover
            self.last_error = type(exc).__name__
        finally:
            session.close("hub 断开")
            if self._on_hub_closed is not None:
                self._on_hub_closed(session)

    async def _serve_hub_frame(self, session: WsSession, raw: str) -> None:
        """hub 连接上的入站帧是下游发来的 API 调用请求。"""
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, Mapping):
            return
        action = str(payload.get("action") or "")
        params = payload.get("params")
        echo = payload.get("echo")
        if not action:
            await session.push(_failed_payload(100, echo))
            return
        result = await self._on_hub_call(action, params if isinstance(params, Mapping) else {})
        await session.push(
            {
                "status": result.status,
                "retcode": result.retcode,
                "data": result.data,
                "echo": echo,
            }
        )

    async def _hub_http(self, request) -> JSONResponse:
        if not _authorized(request.headers, request.query_params, self.hub_token):
            return JSONResponse(_failed_payload(104), status_code=401)
        raw = await self._read_body(request)
        if raw is None:
            return JSONResponse(_failed_payload(102), status_code=413)
        try:
            payload = json.loads(raw)
        except Exception:
            return JSONResponse(_failed_payload(102), status_code=400)
        if not isinstance(payload, Mapping):
            return JSONResponse(_failed_payload(102), status_code=400)
        action = str(payload.get("action") or "")
        echo = payload.get("echo")
        if not action:
            return JSONResponse(_failed_payload(100, echo))
        params = payload.get("params")
        result = await self._on_hub_call(action, params if isinstance(params, Mapping) else {})
        return JSONResponse(
            {
                "status": result.status,
                "retcode": result.retcode,
                "data": result.data,
                "echo": echo,
            }
        )

    @staticmethod
    async def _read_body(request) -> Optional[bytes]:
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > MAX_BODY_BYTES:
                    return None
            except ValueError:
                return None
        # 不能只看 Content-Length：分块传输根本不带这个头，而 request.body() 会把
        # 整个请求无上限地读进内存。这里自己累加、随时封顶，内存上界是
        # MAX_BODY_BYTES + 单个分块。
        chunks: list = []
        total = 0
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    # -- 生命周期 --------------------------------------------------------- #

    def start(self) -> None:
        if self.running:
            return
        from meapet.async_runtime import submit

        self._future = submit(self._serve())

    async def _serve(self) -> None:
        try:
            import uvicorn
        except ImportError as exc:  # pragma: no cover - 可选依赖
            raise RuntimeError("OneBot 接收端需要安装 uvicorn>=0.30") from exc
        config = uvicorn.Config(
            self.app,
            host=self.host,
            port=self.port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        try:
            await self._server.serve()
        except Exception as exc:  # pragma: no cover - 端口占用等
            self.last_error = type(exc).__name__
            _log.warning("[onebot] 接收端 %s 启动失败：%s", self.endpoint, type(exc).__name__)

    def stop(self, timeout_seconds: float = 0.0) -> None:
        """请求关闭。

        默认非阻塞（GUI 的退出 / 配置重载路径调用），但**不是**立刻 cancel：
        先让 uvicorn 自己收尾，只有在一个宽限窗口后仍未退出才 cancel。直接 cancel
        会把 uvicorn 的 lifespan / accept 任务变成悬挂任务，在事件循环关闭时抛
        ``Event loop is closed``。

        这里刻意**不**设 ``force_exit``：那会跳过 uvicorn 的 lifespan 收尾，
        同样留下悬挂任务。宽限窗口足够它正常退出。
        """
        if self._server is not None:
            self._server.should_exit = True
        future, self._future = self._future, None
        self._server = None
        if future is None:
            return
        try:
            timeout = max(0.0, float(timeout_seconds))
        except (TypeError, ValueError):
            timeout = 0.0
        if timeout > 0.0:
            try:
                future.result(timeout=timeout)
            except Exception:
                pass
            return
        _schedule_cancel(future, grace_seconds=GRACEFUL_STOP_SECONDS)


def _schedule_cancel(future, grace_seconds: float) -> None:
    """宽限窗口后取消；窗口内由被调用方自行收尾。"""

    def _cancel() -> None:
        if future.done():
            return
        try:
            future.cancel()
        except Exception:
            pass

    if future.done():
        return
    timer = threading.Timer(max(0.1, float(grace_seconds)), _cancel)
    timer.daemon = True
    timer.start()


def group_listeners(config: Mapping[str, Any]) -> list:
    """把启用的接收端按 ``(host, port)`` 归并。

    返回 ``[{"host":..., "port":..., "reverse_ws":..., "http_post":..., "hub":...}]``，
    其中各值为对应配置段或 ``None``。
    """
    groups: dict = {}
    for key, section_key in (
        ("reverse_ws", "reverse_ws"),
        ("http_post", "http_post"),
        ("hub", "hub"),
    ):
        section = config.get(section_key) or {}
        if not section.get("enabled"):
            continue
        host = str(section.get("host") or "127.0.0.1")
        try:
            port = int(section.get("port"))
        except (TypeError, ValueError):
            continue
        bucket = groups.setdefault((host, port), {"host": host, "port": port,
                                                 "reverse_ws": None, "http_post": None,
                                                 "hub": None})
        bucket[key] = section
    return list(groups.values())
