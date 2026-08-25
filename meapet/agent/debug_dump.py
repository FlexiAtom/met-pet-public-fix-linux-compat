"""
请求结构调试打印（纯文本附件拼接校验用）。

在请求即将发出前调用 dump_messages / dump_frame，把最终要发送的
messages 列表或 Agent Link frame 以可读形式打印到终端。
为兼顾可读性，附件/文件的全文会被截断为 "[文件全文 N 字符 sha256=xxxx...]"，
仅保留结构、分隔标记与文件元数据，便于人工核对拼接是否正确。
"""
from __future__ import annotations

import json
import sys
from typing import Any, Iterable, Optional


# 单条 content 中文件全文显示的最大字符数（超出则截断并标注）
_FILE_PREVIEW_CHARS = 240


def _redact_file_content(text: str, file_name: str = "", sha256: str = "") -> str:
    """把文件全文折叠为元信息摘要，避免刷屏。"""
    n = len(text)
    sha = (sha256 or "")[:8]
    if n <= _FILE_PREVIEW_CHARS:
        return text
    head = text[:_FILE_PREVIEW_CHARS]
    return f"{head}\n...[文件全文已截断：共 {n} 字符，sha256={sha or 'unknown'}]..."


def _summarize_attachment_block(content: str) -> str:
    """
    若 content 含 <<FILE: ...>> ... <<END FILE>> 隔离块，则保留结构、
    把每块内的文件正文截断为元信息；否则原样返回（长度超限时截断）。
    """
    if "<<FILE:" not in content and "<<END FILE>>" not in content:
        if len(content) <= _FILE_PREVIEW_CHARS:
            return content
        return content[:_FILE_PREVIEW_CHARS] + f"\n...[user content 截断至 {_FILE_PREVIEW_CHARS} 字符]"
    # 逐块处理：按 <<FILE: name>> ... <<END FILE>> 切分
    import re
    out = []
    pos = 0
    pattern = re.compile(r"<<FILE:\s*(?P<name>[^>]+?)>>(?P<body>.*?)<<END FILE>>", re.DOTALL)
    for m in pattern.finditer(content):
        # 块前的普通文本（用户消息前缀等）
        before = content[pos:m.start()]
        if before.strip():
            out.append(before.rstrip())
        name = m.group("name").strip()
        body = m.group("body")
        # 尝试从块内提取 sha256（若拼接时未带，则仅显示字符数）
        out.append(f"<<FILE: {name}>>")
        out.append(_redact_file_content(body, file_name=name).strip("\n"))
        out.append("<<END FILE>>")
        pos = m.end()
    tail = content[pos:]
    if tail.strip():
        out.append(tail.rstrip())
    return "\n".join(out)


def _format_message(msg: dict) -> str:
    role = str(msg.get("role", "?"))
    content = msg.get("content")
    if isinstance(content, str):
        body = _summarize_attachment_block(content)
    elif isinstance(content, list):
        # OpenAI 多部分 content: [{type, text|image_url}]
        parts = []
        for part in content:
            if not isinstance(part, dict):
                parts.append(str(part))
                continue
            ptype = part.get("type")
            if ptype == "text":
                parts.append("[text] " + _summarize_attachment_block(str(part.get("text", ""))))
            elif ptype in ("image", "image_url"):
                parts.append(f"[image] media_type={part.get('media_type') or part.get('mimeType')} file_name={part.get('file_name') or part.get('fileName')}")
            else:
                parts.append(f"[{ptype}] {json.dumps(part, ensure_ascii=False)}")
        body = "\n".join(parts)
    else:
        body = json.dumps(content, ensure_ascii=False)
    return f"  [{role}]\n{body}"


def dump_messages(title: str, messages: Iterable[dict], *, stderr: bool = True) -> None:
    """
    把最终要发送的 messages 列表打印到终端。
    每条消息显示 role + content（附件全文截断）。
    """
    msgs = list(messages)
    stream = sys.stderr if stderr else sys.stdout
    sep = "=" * 60
    lines = [
        sep,
        f"[DEBUG REQUEST] {title}",
        f"messages 数量: {len(msgs)}",
        "-" * 60,
    ]
    for i, msg in enumerate(msgs):
        lines.append(f"#{i} " + _format_message(msg if isinstance(msg, dict) else {"role": "?", "content": msg}))
        lines.append("-" * 60)
    lines.append(sep)
    try:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
    except (ValueError, OSError):
        pass


def _frame_summary(frame: Any) -> str:
    """把 Agent Link frame 的 attachments 等字段做摘要后序列化。"""
    if not isinstance(frame, dict):
        return json.dumps(frame, ensure_ascii=False, default=str)
    f = dict(frame)
    payload = f.get("payload") if isinstance(f.get("payload"), dict) else f
    if isinstance(payload, dict):
        atts = payload.get("attachments")
        if isinstance(atts, list):
            summary = []
            for a in atts:
                if not isinstance(a, dict):
                    summary.append(a)
                    continue
                item = dict(a)
                if item.get("type") == "text" and "content" in item:
                    item["content"] = _redact_file_content(
                        str(item["content"]),
                        file_name=str(item.get("file_name") or item.get("fileName") or ""),
                        sha256=str(item.get("sha256") or item.get("sha") or ""),
                    )
                summary.append(item)
            payload = dict(payload)
            payload["attachments"] = summary
            f["payload"] = payload
    return json.dumps(f, ensure_ascii=False, indent=2, default=str)


def dump_frame(title: str, frame: Any, *, stderr: bool = True) -> None:
    """把即将发送的 Agent Link frame 打印到终端（附件全文截断）。"""
    stream = sys.stderr if stderr else sys.stdout
    sep = "=" * 60
    lines = [sep, f"[DEBUG REQUEST] {title}", "-" * 60, _frame_summary(frame), sep]
    try:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
    except (ValueError, OSError):
        pass


def dump_prompt_text(title: str, text: str, *, stderr: bool = True) -> None:
    """打印 build_user_message 产出的完整 prompt 文本（附件块截断）。"""
    stream = sys.stderr if stderr else sys.stdout
    sep = "=" * 60
    body = _summarize_attachment_block(text)
    lines = [sep, f"[DEBUG REQUEST] {title}", f"长度: {len(text)} 字符", "-" * 60, body, sep]
    try:
        stream.write("\n".join(lines) + "\n")
        stream.flush()
    except (ValueError, OSError):
        pass
