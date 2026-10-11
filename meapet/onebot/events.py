"""OneBot v11 上报事件的解析。

只做**防御式**解析：任何字段缺失或类型异常都不抛异常，最坏情况退化为一条
不可路由的事件（``message is None``），保证上游的实现端差异不会打挂桌宠。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from meapet.onebot.segments import is_at_self, normalize_message, segments_to_text
from meapet.onebot.types import (
    GROUP,
    MESSAGE_TYPES,
    PRIVATE,
    IncomingEvent,
    IncomingMessage,
)

MESSAGE = "message"
NOTICE = "notice"
REQUEST = "request"
META_EVENT = "meta_event"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def parse_message(payload: Mapping[str, Any], self_id: int = 0) -> IncomingMessage:
    """解析 ``post_type=message`` 的事件。"""
    group_id = _as_int(payload.get("group_id"), 0)
    message_type = str(payload.get("message_type") or "").strip().lower()
    if message_type not in MESSAGE_TYPES:
        message_type = GROUP if group_id else PRIVATE

    segments = normalize_message(payload.get("message"))
    raw_message = str(payload.get("raw_message") or "")
    if not segments and raw_message:
        segments = normalize_message(raw_message)

    sender = _as_mapping(payload.get("sender"))
    resolved_self = self_id or _as_int(payload.get("self_id"), 0)

    return IncomingMessage(
        self_id=resolved_self,
        message_type=message_type,
        sub_type=str(payload.get("sub_type") or ""),
        message_id=_as_int(payload.get("message_id"), 0),
        user_id=_as_int(payload.get("user_id"), 0),
        group_id=group_id,
        segments=segments,
        raw_message=raw_message,
        text=segments_to_text(segments),
        nickname=str(sender.get("nickname") or ""),
        card=str(sender.get("card") or ""),
        role=str(sender.get("role") or ""),
        time=_as_int(payload.get("time"), 0),
        is_at_self=is_at_self(segments, resolved_self),
    )


def parse_event(payload: Any, self_id: int = 0) -> Optional[IncomingEvent]:
    """解析任意上报事件；无法识别时返回 ``None``。"""
    if not isinstance(payload, Mapping):
        return None
    post_type = str(payload.get("post_type") or "").strip()
    resolved_self = self_id or _as_int(payload.get("self_id"), 0)
    when = _as_int(payload.get("time"), 0)
    raw = dict(payload)

    if not post_type:
        return None

    if post_type == MESSAGE:
        message = parse_message(payload, resolved_self)
        return IncomingEvent(
            kind=MESSAGE,
            self_id=resolved_self,
            time=when,
            raw=raw,
            message=message,
            sub_type=message.sub_type,
        )

    field = {
        NOTICE: ("notice_type", "notice_type"),
        REQUEST: ("request_type", "request_type"),
        META_EVENT: ("meta_event_type", "meta_event_type"),
    }.get(post_type)

    if field is None:
        # 未知 post_type：仍交给 hub 转发，但不参与消息路由
        return IncomingEvent(kind=post_type, self_id=resolved_self, time=when, raw=raw)

    key, attr = field
    return IncomingEvent(
        kind=post_type,
        self_id=resolved_self,
        time=when,
        raw=raw,
        sub_type=str(payload.get("sub_type") or ""),
        **{attr: str(payload.get(key) or "")},
    )


def is_api_response(payload: Any) -> bool:
    """区分「API 响应」与「事件上报」：带 echo/retcode 且没有 post_type。"""
    if not isinstance(payload, Mapping):
        return False
    if payload.get("post_type"):
        return False
    return "retcode" in payload or "status" in payload or "echo" in payload


def summarize(event: IncomingEvent) -> str:
    """一行摘要，用于日志（不回显消息正文，避免隐私落到日志里）。"""
    if event.message is not None:
        message = event.message
        where = f"group:{message.group_id}" if message.is_group else f"private:{message.user_id}"
        return f"message {where} len={len(message.text)} id={message.message_id}"
    if event.kind == NOTICE:
        return f"notice {event.notice_type}"
    if event.kind == REQUEST:
        return f"request {event.request_type}"
    if event.kind == META_EVENT:
        return f"meta_event {event.meta_event_type}"
    return f"event {event.kind}"
