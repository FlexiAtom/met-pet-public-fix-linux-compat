"""OneBot API 调用：echo 关联、发送通道抽象与 HTTP 发送端。

设计要点：

- 所有发送端实现 ``ApiSender`` 协议，接口是 ``send(action, params)``。
- ``SenderRegistry`` 按 ``source`` 优先选通道，找不到就退回任意可用通道，
  这样 NapCat 与 SnowLuma 同时在线时也能正确回发。
- HTTP 发送端同时兼容 ``POST /<action>``（NapCat 风格）与 ``POST /``
  （规范正文形式），前者 404/405 时自动退回后者。
"""

from __future__ import annotations

import itertools
import json
import threading
from typing import Any, Dict, Mapping, Optional, Protocol, Tuple

from meapet.onebot.types import ApiResponse

#: echo 计数器，进程内唯一即可，无需密码学强度
_echo_counter = itertools.count(1)
_echo_lock = threading.Lock()


def make_echo(prefix: str = "meapet") -> str:
    with _echo_lock:
        return f"{prefix}-{next(_echo_counter)}"


class ApiSender(Protocol):
    """一个能把 API 调用送到 QQ 侧实现端的通道。"""

    name: str

    @property
    def available(self) -> bool:  # pragma: no cover - 协议声明
        ...

    async def send(self, action: str, params: Mapping[str, Any]) -> ApiResponse:
        ...


class HttpApiSender:
    """通过 HTTP API 发送（发起端 / 接收端的发送侧）。"""

    mode = "http_api"

    def __init__(
        self,
        api_base: str,
        access_token: str = "",
        timeout_s: float = 10.0,
        name: str = "http_api",
    ) -> None:
        self.name = name
        self.api_base = (api_base or "").rstrip("/")
        self.access_token = access_token or ""
        self.timeout_s = float(timeout_s)
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.api_base)

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        return headers

    async def send(self, action: str, params: Mapping[str, Any]) -> ApiResponse:
        if not self.api_base:
            return ApiResponse.failure("未配置 http_api.api_base", echo=action)

        from meapet.http_async import get_client

        echo = make_echo()
        payload = {"action": action, "params": dict(params), "echo": echo}

        # 优先 /<action>（NapCat / SnowLuma 均支持），404/405 退回基址
        for url in (f"{self.api_base}/{action}", f"{self.api_base}/"):
            try:
                client = await get_client()
                response = await client.post(
                    url,
                    content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers=self._headers(),
                    timeout=self.timeout_s,
                )
            except Exception as exc:  # pragma: no cover - 网络异常路径
                self.last_error = f"{type(exc).__name__}"
                return ApiResponse.failure(f"HTTP 调用失败：{type(exc).__name__}", echo=echo)

            if response.status_code in (404, 405) and url.endswith(f"/{action}"):
                continue
            if response.status_code >= 400:
                self.last_error = f"HTTP {response.status_code}"
                return ApiResponse.failure(
                    f"HTTP {response.status_code}", echo=echo
                )
            try:
                body = response.json()
            except Exception:
                self.last_error = "响应不是 JSON"
                return ApiResponse.failure("响应不是 JSON", echo=echo)
            result = ApiResponse.from_payload(body, echo=echo)
            self.last_error = "" if result.ok else f"retcode={result.retcode}"
            return result

        return ApiResponse.failure("HTTP API 地址不可用", echo=echo)


class SenderRegistry:
    """管理多个发送端，并按来源优先选择。"""

    def __init__(self) -> None:
        self._senders: Dict[str, Any] = {}
        self._fallback: Optional[Any] = None

    def register(self, sender: Any, *, fallback: bool = False) -> None:
        self._senders[getattr(sender, "name", "")] = sender
        if fallback:
            self._fallback = sender

    def unregister(self, name: str) -> None:
        sender = self._senders.pop(name, None)
        if sender is not None and sender is self._fallback:
            self._fallback = None

    def get(self, name: str) -> Optional[Any]:
        return self._senders.get(name)

    def clear(self) -> None:
        self._senders.clear()
        self._fallback = None

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(self._senders)

    def pick(self, source: str = "") -> Optional[Any]:
        """按来源挑发送端；不可用则退回 fallback / 任意可用端。"""
        candidate = self._senders.get(source)
        if candidate is not None and getattr(candidate, "available", False):
            return candidate
        if self._fallback is not None and getattr(self._fallback, "available", False):
            return self._fallback
        for sender in self._senders.values():
            if getattr(sender, "available", False):
                return sender
        return None

    async def send(
        self,
        action: str,
        params: Mapping[str, Any],
        source: str = "",
    ) -> ApiResponse:
        sender = self.pick(source)
        if sender is None:
            return ApiResponse.failure("没有可用的 OneBot 发送通道")
        return await sender.send(action, params)


# --------------------------------------------------------------------------- #
# 常用动作的参数封装
# --------------------------------------------------------------------------- #

def send_private_params(user_id: int, message: Any, **extra: Any) -> Dict[str, Any]:
    params: Dict[str, Any] = {"user_id": int(user_id), "message": message}
    params.update(extra)
    return params


def send_group_params(group_id: int, message: Any, **extra: Any) -> Dict[str, Any]:
    params: Dict[str, Any] = {"group_id": int(group_id), "message": message}
    params.update(extra)
    return params


def send_target_params(
    kind: str,
    target_id: int,
    message: Any,
    **extra: Any,
) -> Tuple[str, Dict[str, Any]]:
    """按会话类型返回 ``(action, params)``。"""
    if kind == "group":
        return "send_group_msg", send_group_params(target_id, message, **extra)
    return "send_private_msg", send_private_params(target_id, message, **extra)
