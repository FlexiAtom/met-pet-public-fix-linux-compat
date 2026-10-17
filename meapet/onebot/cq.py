"""CQ 码编解码（OneBot v11 字符串消息格式）。

CQ 码形如 ``[CQ:type,key=value]``。规范要求参数值里的 ``&`` ``[`` ``]`` ``,``
做转义，因此**先**还原 ``&#xx;`` 实体、**后**还原 ``&amp;``，否则 ``&amp;#91;``
会被错误地二次解码成 ``[``。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

CQ_PREFIX = "[CQ:"

_TEXT_ESCAPES: Tuple[Tuple[str, str], ...] = (
    ("&", "&amp;"),
    ("[", "&#91;"),
    ("]", "&#93;"),
)

_VALUE_ESCAPES: Tuple[Tuple[str, str], ...] = _TEXT_ESCAPES + ((",", "&#44;"),)


def escape_text(value: str) -> str:
    """转义消息正文（不处理逗号）。"""
    out = value or ""
    for raw, entity in _TEXT_ESCAPES:
        out = out.replace(raw, entity)
    return out


def escape_value(value: str) -> str:
    """转义 CQ 参数值（逗号也要转义）。"""
    out = value or ""
    for raw, entity in _VALUE_ESCAPES:
        out = out.replace(raw, entity)
    return out


def unescape(value: str) -> str:
    """还原 CQ 转义。顺序敏感：实体在前，``&amp;`` 在后。"""
    out = value or ""
    out = out.replace("&#91;", "[").replace("&#93;", "]").replace("&#44;", ",")
    out = out.replace("&amp;", "&")
    return out


def _parse_body(body: str) -> Tuple[str, Dict[str, str]]:
    parts = body.split(",")
    seg_type = parts[0].strip()
    if not seg_type:
        return "", {}
    data: Dict[str, str] = {}
    for item in parts[1:]:
        if not item:
            continue
        key, sep, value = item.partition("=")
        key = key.strip()
        if not key:
            continue
        data[key] = unescape(value) if sep else ""
    return seg_type, data


def parse_cq(text: str) -> List[Tuple[str, Dict[str, str]]]:
    """把 CQ 码字符串切成 ``[(type, data), ...]``；无法识别的部分并入 text 段。

    非法片段（如空的 ``[CQ:]`` 或缺少 ``]``）按字面文本保留，不会被丢弃。
    """
    result: List[Tuple[str, Dict[str, str]]] = []
    if not text:
        return result
    buffer: List[str] = []
    index = 0
    total = len(text)

    def flush() -> None:
        if buffer:
            result.append(("text", {"text": unescape("".join(buffer))}))
            buffer.clear()

    while index < total:
        if text.startswith(CQ_PREFIX, index):
            end = text.find("]", index + len(CQ_PREFIX))
            if end == -1:
                buffer.append(text[index:])
                break
            seg_type, data = _parse_body(text[index + len(CQ_PREFIX):end])
            if seg_type:
                flush()
                result.append((seg_type, data))
            else:
                buffer.append(text[index:end + 1])
            index = end + 1
            continue
        buffer.append(text[index])
        index += 1
    flush()
    return result


def build_cq(segments: List[Tuple[str, Dict[str, str]]]) -> str:
    """把 ``[(type, data), ...]`` 组装成 CQ 码字符串；text 段直接输出正文。"""
    out: List[str] = []
    for seg_type, data in segments:
        if seg_type == "text":
            out.append(escape_text(str(data.get("text", ""))))
            continue
        params = "".join(
            f",{key}={escape_value(str(value))}" for key, value in data.items()
        )
        out.append(f"{CQ_PREFIX}{seg_type}{params}]")
    return "".join(out)
