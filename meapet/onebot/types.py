"""OneBot v11 规范数据模型。

这里只放**不依赖任何 I/O 与 Qt**的纯数据结构，供编解码、事件解析、路由与
各通信方式共用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple

PRIVATE = "private"
GROUP = "group"

#: 规范中事件/接口用到的消息类型标记
MESSAGE_TYPES = (PRIVATE, GROUP)


@dataclass(frozen=True)
class MessageSegment:
    """一个消息段：``{"type": ..., "data": {...}}``。"""

    type: str
    data: Mapping[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "data": dict(self.data)}


@dataclass(frozen=True)
class ConversationTarget:
    """一条会话的寻址信息：私聊是 user_id，群聊是 group_id。

    ``source`` 记录事件来自哪条上游连接，用于选择回发通道；为空表示由调用方
    自行挑选可用通道。
    """

    kind: str
    target_id: int
    source: str = ""

    def __post_init__(self) -> None:
        if self.kind not in MESSAGE_TYPES:
            raise ValueError(f"unsupported conversation kind: {self.kind!r}")

    @property
    def key(self) -> str:
        return f"{self.kind}:{int(self.target_id)}"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "target_id": int(self.target_id), "source": self.source}


@dataclass(frozen=True)
class IncomingMessage:
    """规范化后的入站消息事件（只保留 MeaPet 用得到的字段）。"""

    self_id: int
    message_type: str
    sub_type: str
    message_id: int
    user_id: int
    group_id: int
    segments: Tuple[MessageSegment, ...]
    raw_message: str
    text: str
    nickname: str = ""
    card: str = ""
    role: str = ""
    time: int = 0
    is_at_self: bool = False
    #: 事件来自哪条上游连接（forward_ws / reverse_ws:… / http_post），
    #: 用于把回复发回同一个对端
    source: str = ""

    @property
    def is_group(self) -> bool:
        return self.message_type == GROUP

    @property
    def is_private(self) -> bool:
        return self.message_type == PRIVATE

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or str(self.user_id)

    @property
    def target(self) -> ConversationTarget:
        if self.is_group:
            return ConversationTarget(GROUP, self.group_id, self.source)
        return ConversationTarget(PRIVATE, self.user_id, self.source)

    @property
    def conversation_key(self) -> str:
        return self.target.key


@dataclass(frozen=True)
class IncomingEvent:
    """一条已解析的上报事件。

    ``kind`` 取 ``message`` / ``notice`` / ``request`` / ``meta_event``；
    ``message`` 非空时表示这是一条可路由的消息事件。
    """

    kind: str
    self_id: int
    time: int
    raw: Mapping[str, Any]
    message: Optional[IncomingMessage] = None
    notice_type: str = ""
    request_type: str = ""
    meta_event_type: str = ""
    sub_type: str = ""

    @property
    def is_message(self) -> bool:
        return self.message is not None


@dataclass(frozen=True)
class ApiRequest:
    """一次 OneBot API 调用。"""

    action: str
    params: Mapping[str, Any]
    echo: str


@dataclass(frozen=True)
class ApiResponse:
    """OneBot API 调用的响应。"""

    ok: bool
    status: str = "failed"
    retcode: int = 1
    data: Any = None
    echo: str = ""
    error: str = ""

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], echo: str = "") -> "ApiResponse":
        data = payload.get("data") if isinstance(payload, Mapping) else None
        try:
            retcode = int(payload.get("retcode", 1))
        except (TypeError, ValueError):
            retcode = 1
        status = str(payload.get("status") or ("ok" if retcode == 0 else "failed"))
        return cls(
            ok=status == "ok" and retcode == 0,
            status=status,
            retcode=retcode,
            data=data,
            echo=str(payload.get("echo") or echo),
        )

    @classmethod
    def failure(cls, error: str, echo: str = "") -> "ApiResponse":
        return cls(ok=False, status="failed", retcode=1, data=None, echo=echo, error=error)


@dataclass
class ConnectionStatus:
    """一条连接的可观测状态，用于界面展示与排障。"""

    name: str
    mode: str
    listening: bool = False
    connected: bool = False
    endpoint: str = ""
    detail: str = ""
    last_error: str = ""
