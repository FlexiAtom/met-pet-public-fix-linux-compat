"""从统一配置构造原生 WebSocket Agent 适配器。"""

from __future__ import annotations

import os
import uuid

from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig
from meapet.agent.hermes import HermesAdapter, HermesConfig
from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig
from meapet.config.defaults import (
    DEFAULT_AGENT_LINK_WS_URL,
    DEFAULT_AGENT_HISTORY_TURNS,
    DEFAULT_AGENT_TIMEOUT_SECONDS,
    DEFAULT_HERMES_WS_URL,
    DEFAULT_OPENCLAW_WS_URL,
)


def _resolve_secret(value: str, env_keys: tuple[str, ...]) -> str:
    raw = str(value or "").strip()
    if raw.startswith("${") and raw.endswith("}") and len(raw) > 3:
        targeted = os.environ.get(raw[2:-1], "").strip()
        if targeted:
            return targeted
    if raw.startswith("$") and len(raw) > 1:
        targeted = os.environ.get(raw[1:], "").strip()
        if targeted:
            return targeted
    for key in env_keys:
        resolved = os.environ.get(key, "").strip()
        if resolved:
            return resolved
    if raw.startswith("$"):
        return ""
    return raw


def _positive_float(value: object, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _bounded_int(
    value: object,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return min(max(result, minimum), maximum)


def _ensure_local_session(agent_cfg: dict) -> str:
    session_id = str(agent_cfg.get("session_id") or "").strip()
    if not session_id:
        session_id = f"meapet-{uuid.uuid4().hex}"
        agent_cfg["session_id"] = session_id
    return session_id


def _ensure_device_id(agent_cfg: dict) -> str:
    device_id = str(agent_cfg.get("device_id") or "").strip()
    if not device_id:
        device_id = f"meapet-device-{uuid.uuid4().hex}"
        agent_cfg["device_id"] = device_id
    return device_id


def create_agent_adapter_from_config(
    config: dict,
) -> HermesAdapter | OpenClawAdapter | AgentLinkAdapter:
    """构造 Hermes、OpenClaw 或通用 Agent Link 的 WS 适配器。

    OpenAI/Ollama/Anthropic 等 HTTP 接口属于 ``llm.direct``，不会从本工厂
    创建，也不会在 Agent 连接失败时作为隐式回落。
    """
    llm = config.setdefault("llm", {})
    agent_cfg = llm.setdefault("agent", {})
    kind = str(agent_cfg.get("kind") or "hermes").strip().lower()
    if kind not in {"hermes", "openclaw", "agent_link"}:
        raise ValueError(
            "llm.agent.kind must be 'hermes', 'openclaw' or 'agent_link'; "
            "HTTP model endpoints belong under llm.direct"
        )

    session_id = _ensure_local_session(agent_cfg)
    timeout = _positive_float(
        agent_cfg.get("timeout_seconds"),
        DEFAULT_AGENT_TIMEOUT_SECONDS,
    )
    tls = agent_cfg.get("tls")
    tls = tls if isinstance(tls, dict) else {}
    verify_tls = bool(tls.get("verify", True))
    ca_file = str(tls.get("ca_file") or "").strip()
    allow_insecure_ws = bool(agent_cfg.get("allow_insecure_ws", False))
    raw_token = str(
        agent_cfg.get("auth_token") or agent_cfg.get("api_key") or ""
    ).strip()

    if kind == "agent_link":
        token = _resolve_secret(
            raw_token,
            ("AGENT_LINK_TOKEN", "MEAPET_AGENT_TOKEN"),
        )
        extensions = agent_cfg.get("extensions")
        extensions = extensions if isinstance(extensions, dict) else {}
        return AgentLinkAdapter(
            AgentLinkConfig(
                base_url=(
                    str(agent_cfg.get("base_url") or "").strip()
                    or DEFAULT_AGENT_LINK_WS_URL
                ),
                auth_token=token,
                device_id=_ensure_device_id(agent_cfg),
                session_id=session_id,
                timeout_seconds=timeout,
                verify_tls=verify_tls,
                ca_file=ca_file,
                allow_insecure_ws=allow_insecure_ws,
                extensions=extensions,
            )
        )

    if kind == "openclaw":
        token = _resolve_secret(
            raw_token,
            ("OPENCLAW_GATEWAY_TOKEN", "MEAPET_AGENT_TOKEN"),
        )
        session_key = str(agent_cfg.get("session_key") or "").strip()
        if not session_key:
            session_key = f"agent:main:meapet:{uuid.uuid4().hex}"
            agent_cfg["session_key"] = session_key
        return OpenClawAdapter(
            OpenClawConfig(
                base_url=(
                    str(agent_cfg.get("base_url") or "").strip()
                    or DEFAULT_OPENCLAW_WS_URL
                ),
                auth_token=token,
                session_key=session_key,
                session_id=str(agent_cfg.get("upstream_session_id") or "").strip(),
                timeout_seconds=timeout,
                verify_tls=verify_tls,
                ca_file=ca_file,
                allow_insecure_ws=allow_insecure_ws,
                identity_path=str(
                    agent_cfg.get("identity_path") or ""
                ).strip(),
            )
        )

    token = _resolve_secret(
        raw_token,
        (
            "HERMES_DASHBOARD_SESSION_TOKEN",
            "HERMES_API_SERVER_KEY",
            "MEAPET_AGENT_TOKEN",
        ),
    )
    return HermesAdapter(
        HermesConfig(
            base_url=(
                str(agent_cfg.get("base_url") or "").strip()
                or DEFAULT_HERMES_WS_URL
            ),
            auth_token=token,
            model=str(agent_cfg.get("model") or "").strip(),
            session_id=str(agent_cfg.get("session_id") or "").strip(),
            session_key=str(agent_cfg.get("session_key") or "").strip(),
            remote_session_id=str(
                agent_cfg.get("remote_session_id") or ""
            ).strip(),
            history_turns=_bounded_int(
                agent_cfg.get("history_turns"),
                DEFAULT_AGENT_HISTORY_TURNS,
                minimum=0,
                maximum=100,
            ),
            timeout_seconds=timeout,
            verify_tls=verify_tls,
            ca_file=ca_file,
            allow_insecure_ws=allow_insecure_ws,
        ),
        config_sink=agent_cfg,
    )
