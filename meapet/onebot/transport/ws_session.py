"""双向 WebSocket 会话：事件接收与 echo 关联的 API 调用。

正向 WS（我们连对端）与反向 WS（对端连我们）在 OneBot 里是同一条连接上的
双向复用，因此两者共用这一个会话对象 —— 差别只在连接的建立方式。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from meapet.onebot.api import make_echo
from meapet.onebot.events import is_api_response
from meapet.onebot.types import ApiResponse

SendText = Callable[[str], Awaitable[None]]


class WsSession:
    """包装一条已建立的 WebSocket 连接。"""

    def __init__(
        self,
        name: str,
        send_text: SendText,
        call_timeout_s: float = 15.0,
    ) -> None:
        self.name = name
        self._send_text = send_text
        self._call_timeout_s = float(call_timeout_s)
        self._pending: Dict[str, asyncio.Future] = {}
        self._closed = False
        self.connected_at = time.time()

    @property
    def available(self) -> bool:
        return not self._closed

    async def send(self, action: str, params: Mapping[str, Any]) -> ApiResponse:
        """发送 API 调用并等待同 echo 的响应。"""
        if self._closed:
            return ApiResponse.failure("连接已断开")
        echo = make_echo()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[echo] = future
        payload: Dict[str, Any] = {"action": action, "params": dict(params), "echo": echo}
        try:
            await self._send_text(json.dumps(payload, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout=self._call_timeout_s)
        except asyncio.TimeoutError:
            return ApiResponse.failure("调用超时", echo=echo)
        except Exception as exc:
            return ApiResponse.failure(f"发送失败：{type(exc).__name__}", echo=echo)
        finally:
            self._pending.pop(echo, None)

    async def push(self, payload: Mapping[str, Any]) -> bool:
        """单向推送（hub 事件发布用），不等响应。"""
        if self._closed:
            return False
        try:
            await self._send_text(json.dumps(dict(payload), ensure_ascii=False))
            return True
        except Exception:
            return False

    def feed(self, raw: str, on_event: Callable[[Mapping[str, Any], str], None]) -> None:
        """处理一条收到的文本帧：API 响应 → 唤醒等待者，其余 → 事件回调。"""
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, Mapping):
            return
        if is_api_response(payload):
            echo = str(payload.get("echo") or "")
            future = self._pending.get(echo) if echo else None
            if future is None and not echo and len(self._pending) == 1:
                # 规范要求响应带 echo，但个别实现端（例如 SnowLuma 文档里的应答信封
                # 只有 status/retcode/data/message/wording）在 WS 上也可能不带。
                # 此时如果**只有一个**在途调用，按唯一性归位是安全的；超过一个就宁可不猜。
                echo, future = next(iter(self._pending.items()))
            if future is not None and not future.done():
                future.set_result(ApiResponse.from_payload(payload, echo=echo))
            return
        on_event(payload, self.name)

    def close(self, reason: str = "") -> None:
        """标记断开并让所有等待中的调用立即失败。"""
        if self._closed:
            return
        self._closed = True
        pending, self._pending = self._pending, {}
        for echo, future in pending.items():
            if not future.done():
                future.set_result(
                    ApiResponse.failure(reason or "连接已断开", echo=echo)
                )


class NullSession:
    """占位发送端：始终不可用，用于配置缺失时保持注册表完整。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.available = False

    async def send(self, action: str, params: Mapping[str, Any]) -> ApiResponse:
        return ApiResponse.failure("通道不可用")
