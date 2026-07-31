"""Agent Link v1 的稳定 JSON 信封与校验规则。"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping


AGENT_LINK_VERSION = "1.0"
AGENT_LINK_MAJOR_VERSION = 1
MAX_IDENTIFIER_CHARS = 256
MAX_EXTENSIONS_BYTES = 64 * 1024

_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_EXTENSION_RE = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9_-]*(?:\.[a-zA-Z0-9][a-zA-Z0-9_-]*)+$"
)


class AgentLinkProtocolError(RuntimeError):
    """收到不符合 Agent Link v1 契约的消息。"""

    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = str(code or "PROTOCOL_ERROR")
        self.safe_message = str(safe_message or "Agent Link 协议错误")


def _safe_identifier(
    name: str,
    value: object,
    *,
    required: bool,
) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise AgentLinkProtocolError(
            "INVALID_ENVELOPE",
            f"Agent Link 消息的 {name} 必须是字符串",
        )
    result = value.strip()
    if required and not result:
        raise AgentLinkProtocolError(
            "INVALID_ENVELOPE",
            f"Agent Link 消息缺少 {name}",
        )
    if (
        len(result) > MAX_IDENTIFIER_CHARS
        or any(char in result for char in "\r\n\x00")
    ):
        raise AgentLinkProtocolError(
            "INVALID_ENVELOPE",
            f"Agent Link 消息的 {name} 非法",
        )
    return result


def normalize_agent_link_version(value: object) -> str:
    """校验并返回 ``major.minor`` 形式的兼容 Agent Link 版本。"""
    if not isinstance(value, str):
        raise AgentLinkProtocolError(
            "INVALID_ENVELOPE",
            "Agent Link 消息的 version 必须是字符串",
        )
    version = value.strip()
    match = _VERSION_RE.fullmatch(version)
    if match is None:
        raise AgentLinkProtocolError(
            "INVALID_ENVELOPE",
            "Agent Link 消息的 version 必须使用 major.minor 格式",
        )
    major = int(match.group(1))
    if major != AGENT_LINK_MAJOR_VERSION:
        raise AgentLinkProtocolError(
            "UNSUPPORTED_VERSION",
            "Agent Link 协议主版本不兼容",
        )
    return version


def _normalize_extensions(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise AgentLinkProtocolError(
            "INVALID_EXTENSIONS",
            "Agent Link extensions 必须是对象",
        )
    result = dict(value)
    for key in result:
        if not _EXTENSION_RE.fullmatch(str(key)):
            raise AgentLinkProtocolError(
                "INVALID_EXTENSIONS",
                "Agent Link 自定义扩展必须使用命名空间键",
            )
    try:
        encoded = json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AgentLinkProtocolError(
            "INVALID_EXTENSIONS",
            "Agent Link extensions 不是有效 JSON",
        ) from exc
    if len(encoded) > MAX_EXTENSIONS_BYTES:
        raise AgentLinkProtocolError(
            "INVALID_EXTENSIONS",
            "Agent Link extensions 超过大小限制",
        )
    return result


@dataclass(frozen=True)
class AgentLinkFrame:
    """解析后的固定信封；未知非必需顶层字段按前向兼容规则忽略。"""

    version: str
    type: str
    id: str
    session_id: str
    reply_to: str
    payload: dict[str, Any]
    extensions: dict[str, Any]

    @classmethod
    def parse(cls, raw: object) -> "AgentLinkFrame":
        if not isinstance(raw, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_ENVELOPE",
                "Agent Link 消息必须是 JSON 对象",
            )
        version = normalize_agent_link_version(raw.get("version"))
        raw_type = raw.get("type")
        if not isinstance(raw_type, str):
            raise AgentLinkProtocolError(
                "INVALID_ENVELOPE",
                "Agent Link 消息的 type 必须是字符串",
            )
        message_type = raw_type.strip()
        if not _TYPE_RE.fullmatch(message_type):
            raise AgentLinkProtocolError(
                "INVALID_ENVELOPE",
                "Agent Link 消息 type 非法",
            )
        payload = raw.get("payload")
        if payload is None:
            payload = {}
        if not isinstance(payload, Mapping):
            raise AgentLinkProtocolError(
                "INVALID_ENVELOPE",
                "Agent Link 消息 payload 必须是对象",
            )
        return cls(
            version=version,
            type=message_type,
            id=_safe_identifier("id", raw.get("id"), required=True),
            session_id=_safe_identifier(
                "session_id",
                raw.get("session_id"),
                required=False,
            ),
            reply_to=_safe_identifier(
                "reply_to",
                raw.get("reply_to"),
                required=False,
            ),
            payload=dict(payload),
            extensions=_normalize_extensions(raw.get("extensions")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.type,
            "id": self.id,
            "session_id": self.session_id,
            "reply_to": self.reply_to,
            "payload": dict(self.payload),
            "extensions": dict(self.extensions),
        }


def make_agent_link_frame(
    message_type: str,
    payload: Mapping[str, object] | None = None,
    *,
    message_id: str = "",
    session_id: str = "",
    reply_to: str = "",
    extensions: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """构造并自校验一条 Agent Link v1 消息。"""
    frame = {
        "version": AGENT_LINK_VERSION,
        "type": str(message_type or "").strip(),
        "id": (
            str(message_id or "").strip()
            or f"msg-{uuid.uuid4().hex}"
        ),
        "session_id": str(session_id or "").strip(),
        "reply_to": str(reply_to or "").strip(),
        "payload": dict(payload or {}),
        "extensions": dict(extensions or {}),
    }
    return AgentLinkFrame.parse(frame).to_dict()
