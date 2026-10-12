"""OneBot v11 适配器与接收器。

MeaPet 通过本包同时扮演 OneBot 的两种角色：

- **发起端**：``forward_ws``（主动连接 NapCat / SnowLuma 的正向 WebSocket）、
  ``http_api``（调用对端的 HTTP API 发送消息）。
- **接收端**：``reverse_ws``（自己监听，等对端连进来）、``http_post``（接收对端
  的事件 HTTP POST），以及面向下游消费者的 ``hub`` 端点。

四种通信方式共用同一套消息段 / CQ 码 / 事件模型，具体实现见 :mod:`meapet.onebot.service`。
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_ONEBOT",
    "OneBotService",
    "normalize_onebot_config",
]


def __getattr__(name: str):  # pragma: no cover - 仅为延迟导入保持包轻量
    if name in ("DEFAULT_ONEBOT", "normalize_onebot_config"):
        from meapet.onebot import config as _config

        return getattr(_config, name)
    if name == "OneBotService":
        from meapet.onebot.service import OneBotService

        return OneBotService
    raise AttributeError(name)
