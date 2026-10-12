"""OneBot 传输层：四种通信方式的实现。"""

from __future__ import annotations

__all__ = ["ForwardWsClient", "OneBotServer", "WsSession"]


def __getattr__(name: str):  # pragma: no cover - 延迟导入
    if name == "WsSession":
        from meapet.onebot.transport.ws_session import WsSession

        return WsSession
    if name == "ForwardWsClient":
        from meapet.onebot.transport.forward_ws import ForwardWsClient

        return ForwardWsClient
    if name == "OneBotServer":
        from meapet.onebot.transport.server import OneBotServer

        return OneBotServer
    raise AttributeError(name)
