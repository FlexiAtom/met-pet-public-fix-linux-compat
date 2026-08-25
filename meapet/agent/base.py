"""Agent 适配器与前端编排器之间的稳定边界。"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from typing import Mapping, Tuple

from meapet.conversation.output_protocol import ParseResult
from meapet.conversation.timeline import ConversationKey


_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_MAX_IMAGE_BYTES = 5 * 1024 * 1024

# 纯文本附件允许的扩展名白名单
_TEXT_ATTACHMENT_EXTENSIONS = frozenset(
    {"txt", "md", "csv", "json", "log", "yaml", "yml", "xml", "ini", "cfg", "env"}
)
_MAX_TEXT_FILE_NAME = 128


@dataclass(frozen=True)
class ImageAttachment:
    """仅允许内联、有界的截图，避免 SSRF 与无界载荷。"""

    media_type: str
    data: str
    file_name: str = "screenshot.jpg"

    def __post_init__(self) -> None:
        media_type = str(self.media_type or "").strip().lower()
        if media_type not in _IMAGE_MEDIA_TYPES:
            raise ValueError("image media_type is unsupported")
        data = str(self.data or "").strip()
        if not data:
            raise ValueError("image data is required")
        try:
            decoded = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("image data must be valid base64") from exc
        if not decoded or len(decoded) > _MAX_IMAGE_BYTES:
            raise ValueError("image data exceeds the allowed size")
        file_name = str(self.file_name or "screenshot.jpg").strip()
        if (
            not file_name
            or len(file_name) > 128
            or any(char in file_name for char in "/\\\r\n\x00")
        ):
            raise ValueError("image file_name is unsafe")
        object.__setattr__(self, "media_type", media_type)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "file_name", file_name)

    @property
    def decoded_size(self) -> int:
        padding = len(self.data) - len(self.data.rstrip("="))
        return (len(self.data) * 3 // 4) - padding

    def canonical_part(self) -> dict[str, str]:
        return {
            "type": "image",
            "media_type": self.media_type,
            "data": self.data,
        }


@dataclass(frozen=True)
class TextAttachment:
    """纯文本文件附件：以直接拼接方式附加到请求。

    仅存储解码后的文本内容、字符数与 SHA-256 哈希；不内嵌原始字节。
    数量不限，是否可上传由调用方按上下文预算决定。
    """

    file_name: str
    text_content: str
    char_count: int
    sha256_hash: str

    def __post_init__(self) -> None:
        file_name = str(self.file_name or "").strip()
        if (
            not file_name
            or len(file_name) > _MAX_TEXT_FILE_NAME
            or any(char in file_name for char in "/\\\r\n\x00")
        ):
            raise ValueError("text file_name is unsafe")
        text = str(self.text_content or "")
        char_count = int(self.char_count)
        if char_count != len(text):
            raise ValueError("text char_count mismatch")
        sha = str(self.sha256_hash or "").strip().lower()
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("text sha256_hash must be a 64-char hex string")
        object.__setattr__(self, "file_name", file_name)
        object.__setattr__(self, "text_content", text)
        object.__setattr__(self, "char_count", char_count)
        object.__setattr__(self, "sha256_hash", sha)

    @classmethod
    def from_bytes(cls, file_name: str, raw: bytes) -> "TextAttachment":
        """从原始字节构造：自动 BOM 探测解码为 UTF-8 文本。

        BOM 探测顺序：UTF-8-SIG / UTF-16-LE / UTF-16-BE / GBK 回退。
        若无 BOM 则按 UTF-8 解码，失败回退 GBK。
        """
        import codecs

        name = str(file_name or "").strip()
        # 扩展名白名单校验
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in _TEXT_ATTACHMENT_EXTENSIONS:
            raise ValueError(f"text file extension '.{ext}' not in whitelist")

        text: str
        # BOM 探测
        if raw.startswith(codecs.BOM_UTF8):
            text = raw[len(codecs.BOM_UTF8):].decode("utf-8", errors="strict")
        elif raw.startswith(codecs.BOM_UTF16_LE):
            text = raw[len(codecs.BOM_UTF16_LE):].decode("utf-16-le", errors="strict")
        elif raw.startswith(codecs.BOM_UTF16_BE):
            text = raw[len(codecs.BOM_UTF16_BE):].decode("utf-16-be", errors="strict")
        else:
            # 无 BOM：先尝试 UTF-8，失败回退 GBK
            try:
                text = raw.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                text = raw.decode("gbk", errors="replace")
        sha = hashlib.sha256(raw).hexdigest()
        return cls(
            file_name=name,
            text_content=text,
            char_count=len(text),
            sha256_hash=sha,
        )

    def to_prompt_block(self) -> str:
        """渲染为带隔离标记、可直接拼入 user message 的文本块。"""
        return (
            f"=== {self.file_name} ===\n"
            f"<<FILE: {self.file_name}>>\n"
            f"{self.text_content}\n"
            f"<<END FILE>>"
        )


@dataclass(frozen=True)
class AgentTurnRequest:
    turn_id: str
    user_text: str
    history: Tuple[Mapping[str, object], ...] = ()
    frontend_context: Mapping[str, object] = field(default_factory=dict)
    tts_enabled: bool = False
    attachments: Tuple[ImageAttachment, ...] = ()
    text_attachments: Tuple[TextAttachment, ...] = ()
    conversation_key: ConversationKey | None = None
    generation_id: int = 0

    def __post_init__(self) -> None:
        turn_id = str(self.turn_id or "").strip()
        if not turn_id:
            raise ValueError("turn_id is required")
        if len(turn_id) > 256 or any(char in turn_id for char in "\r\n\x00"):
            raise ValueError("turn_id is not a safe request identifier")
        object.__setattr__(self, "turn_id", turn_id)
        object.__setattr__(self, "user_text", str(self.user_text or "").strip())
        object.__setattr__(self, "history", tuple(self.history or ()))
        object.__setattr__(self, "frontend_context", dict(self.frontend_context or {}))
        object.__setattr__(self, "tts_enabled", bool(self.tts_enabled))
        conversation_key = self.conversation_key
        if conversation_key is not None and not isinstance(
            conversation_key,
            ConversationKey,
        ):
            raise TypeError("conversation_key must be a ConversationKey")
        try:
            generation_id = int(self.generation_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("generation_id must be an integer") from exc
        if generation_id < 0:
            raise ValueError("generation_id cannot be negative")
        object.__setattr__(self, "generation_id", generation_id)
        attachments = tuple(self.attachments or ())
        if len(attachments) > 4 or any(
            not isinstance(item, ImageAttachment) for item in attachments
        ):
            raise ValueError("attachments must contain at most four images")
        object.__setattr__(self, "attachments", attachments)
        text_attachments = tuple(self.text_attachments or ())
        if any(not isinstance(item, TextAttachment) for item in text_attachments):
            raise ValueError("text_attachments must contain TextAttachment instances")
        object.__setattr__(self, "text_attachments", text_attachments)


@dataclass(frozen=True)
class ToolStatus:
    state: str
    safe_text: str


@dataclass(frozen=True)
class FormatRepairRequired:
    result: ParseResult


@dataclass(frozen=True)
class TurnCompleted:
    turn_id: str
    result: ParseResult


@dataclass(frozen=True)
class TurnFailed:
    turn_id: str
    category: str
    safe_message: str
    retryable: bool = False


@dataclass(frozen=True)
class TurnCancelled:
    turn_id: str
