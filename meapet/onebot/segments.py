"""消息段数组 / CQ 码字符串 / 纯文本之间的转换。

入站消息可能是三种形态（规范 5.1）：CQ 码字符串、消息段数组、单个消息段对象。
其中**字符串形态是转义过的**，数组/对象形态不做转义 —— 这里严格按此区分，
避免把数组里的 ``&#91;`` 二次解码。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence, Tuple

from meapet.onebot import cq
from meapet.onebot.types import MessageSegment

#: 非文本消息段在纯文本里的占位符（喂给模型时保留语义线索）
SEGMENT_PLACEHOLDERS = {
    "image": "[图片]",
    "face": "[表情]",
    "record": "[语音]",
    "video": "[视频]",
    "rps": "[猜拳]",
    "dice": "[骰子]",
    "shake": "[窗口抖动]",
    "poke": "[戳一戳]",
    "anonymous": "[匿名消息]",
    "share": "[链接分享]",
    "contact": "[推荐联系人]",
    "location": "[位置]",
    "music": "[音乐分享]",
    "forward": "[合并转发]",
    "node": "[转发节点]",
    "xml": "[XML消息]",
    "json": "[JSON消息]",
    "reply": "",
}


def text_segment(text: str) -> MessageSegment:
    return MessageSegment("text", {"text": text})


def at_segment(qq: Any) -> MessageSegment:
    return MessageSegment("at", {"qq": str(qq)})


def _from_mapping(item: Mapping[str, Any]) -> MessageSegment | None:
    seg_type = str(item.get("type") or "").strip()
    if not seg_type:
        return None
    raw_data = item.get("data")
    data = dict(raw_data) if isinstance(raw_data, Mapping) else {}
    return MessageSegment(seg_type, data)


def normalize_message(value: Any) -> Tuple[MessageSegment, ...]:
    """把任意形态的 ``message`` 字段规范化为消息段元组。"""
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(
            MessageSegment(seg_type, dict(data)) for seg_type, data in cq.parse_cq(value)
        )
    if isinstance(value, Mapping):
        # 单个消息段对象；若没有 type 字段则当作空
        segment = _from_mapping(value)
        return (segment,) if segment is not None else ()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        out = []
        for item in value:
            if isinstance(item, Mapping):
                segment = _from_mapping(item)
                if segment is not None:
                    out.append(segment)
            elif isinstance(item, str):
                out.extend(normalize_message(item))
        return tuple(out)
    return ()


def segments_to_cq(segments: Iterable[MessageSegment]) -> str:
    """消息段 → CQ 码字符串（用于需要字符串 message 的接口）。"""
    return cq.build_cq([(seg.type, dict(seg.data)) for seg in segments])


def segments_to_text(segments: Iterable[MessageSegment]) -> str:
    """消息段 → 纯文本，供模型阅读；非文本段转成占位符。"""
    parts = []
    for seg in segments:
        if seg.type == "text":
            parts.append(str(seg.get("text", "")))
        elif seg.type == "at":
            qq = str(seg.get("qq", ""))
            parts.append("@全体成员" if qq == "all" else f"@{qq}")
        else:
            parts.append(SEGMENT_PLACEHOLDERS.get(seg.type, f"[{seg.type}]"))
    return "".join(parts).strip()


def is_at_self(segments: Iterable[MessageSegment], self_id: int) -> bool:
    """消息里是否 @ 了本机机器人（``@全体成员`` 也算）。"""
    wanted = str(int(self_id)) if self_id else ""
    for seg in segments:
        if seg.type != "at":
            continue
        qq = str(seg.get("qq", ""))
        if qq == "all" or (wanted and qq == wanted):
            return True
    return False


def build_message_payload(target_kind: str, message: Any, **extra: Any) -> dict:
    """组装 ``send_private_msg`` / ``send_group_msg`` 的参数。"""
    params = dict(extra)
    params["message"] = message
    params["auto_escape"] = False
    return params
