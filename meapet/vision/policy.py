"""Side-effect-free routing policy for screenshots and vision models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


VISION_MODES = frozenset({"disabled", "inherit", "relay"})
_KNOWN_OLLAMA_VISION_MARKERS = (
    "llava",
    "minicpm-v",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "qwen3.5",
    "vision",
)


def normalize_vision_mode(value: object) -> str:
    mode = str(value or "disabled").strip().lower()
    return mode if mode in VISION_MODES else "disabled"


def _explicit_capability(
    vision: Mapping[str, object],
    llm: Mapping[str, object],
) -> bool | None:
    value = vision.get("main_model_supports_images")
    if isinstance(value, bool):
        return value
    direct = llm.get("direct") if isinstance(llm.get("direct"), Mapping) else {}
    capabilities = (
        direct.get("capabilities")
        if isinstance(direct.get("capabilities"), Mapping)
        else llm.get("capabilities")
    )
    if isinstance(capabilities, Mapping):
        value = capabilities.get("vision")
        if isinstance(value, bool):
            return value
    return None


def main_model_supports_vision(
    vision_cfg: Mapping[str, object],
    llm_cfg: Mapping[str, object],
) -> bool:
    explicit = _explicit_capability(vision_cfg, llm_cfg)
    if explicit is not None:
        return explicit
    if str(llm_cfg.get("mode") or "direct").strip().lower() == "agent":
        return False

    direct = (
        llm_cfg.get("direct")
        if isinstance(llm_cfg.get("direct"), Mapping)
        else {}
    )
    from meapet.config.store import detect_endpoint_family

    family = detect_endpoint_family(
        direct.get("api_base"),
        direct.get("host"),
        llm_cfg.get("api_base"),
        llm_cfg.get("host"),
    )
    model = str(
        direct.get("model") or llm_cfg.get("model") or ""
    ).strip().lower()
    if family == "mimo":
        return True
    if family == "ollama":
        return any(marker in model for marker in _KNOWN_OLLAMA_VISION_MARKERS)
    return False


@dataclass(frozen=True)
class VisionRoute:
    mode: str
    available: bool
    reason: str = ""


def resolve_vision_route(
    vision_cfg: Mapping[str, object] | None,
    llm_cfg: Mapping[str, object] | None,
) -> VisionRoute:
    vision = vision_cfg or {}
    llm = llm_cfg or {}
    mode = normalize_vision_mode(vision.get("mode"))
    if mode == "disabled":
        return VisionRoute(mode, True)

    llm_mode = str(llm.get("mode") or "direct").strip().lower()
    if mode == "relay":
        if llm_mode == "agent":
            return VisionRoute(mode, False, "agent_relay_forbidden")
        backend = str(vision.get("backend") or "").strip().lower()
        if backend not in {"ollama", "mimo"}:
            return VisionRoute(mode, False, "relay_backend_not_configured")
        return VisionRoute(mode, True)

    if not main_model_supports_vision(vision, llm):
        return VisionRoute(mode, False, "main_model_vision_not_confirmed")
    return VisionRoute(mode, True)


__all__ = [
    "VISION_MODES",
    "VisionRoute",
    "main_model_supports_vision",
    "normalize_vision_mode",
    "resolve_vision_route",
]
