"""Side-effect-free routing policy for screenshots and vision models."""

from __future__ import annotations

import sys  # 新增
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
    print(f"[DEBUG policy] normalize_vision_mode called: value={value!r}", file=sys.stderr, flush=True)
    mode = str(value or "disabled").strip().lower()
    result = mode if mode in VISION_MODES else "disabled"
    print(f"[DEBUG policy] normalize_vision_mode returning: {result!r}", file=sys.stderr, flush=True)
    return result


def _explicit_capability(
    vision: Mapping[str, object],
    llm: Mapping[str, object],
) -> bool | None:
    print(f"[DEBUG policy] _explicit_capability called: vision keys={list(vision.keys())}, llm keys={list(llm.keys())}", file=sys.stderr, flush=True)
    value = vision.get("main_model_supports_images")
    if isinstance(value, bool):
        print(f"[DEBUG policy] _explicit_capability: found explicit vision.main_model_supports_images={value}", file=sys.stderr, flush=True)
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
            print(f"[DEBUG policy] _explicit_capability: found capabilities.vision={value}", file=sys.stderr, flush=True)
            return value
    print(f"[DEBUG policy] _explicit_capability: no explicit capability found, returning None", file=sys.stderr, flush=True)
    return None


def main_model_supports_vision(
    vision_cfg: Mapping[str, object],
    llm_cfg: Mapping[str, object],
) -> bool:
    print(f"[DEBUG policy] main_model_supports_vision called: vision_cfg keys={list(vision_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys())}", file=sys.stderr, flush=True)
    explicit = _explicit_capability(vision_cfg, llm_cfg)
    if explicit is not None:
        print(f"[DEBUG policy] main_model_supports_vision: explicit={explicit}, returning directly", file=sys.stderr, flush=True)
        return explicit
    llm_mode = str(llm_cfg.get("mode") or "direct").strip().lower()
    if llm_mode == "agent":
        print(f"[DEBUG policy] main_model_supports_vision: llm mode is agent, returning False", file=sys.stderr, flush=True)
        return False

    direct = (
        llm_cfg.get("direct")
        if isinstance(llm_cfg.get("direct"), Mapping)
        else {}
    )
    provider = str(
        direct.get("provider") or llm_cfg.get("backend") or ""
    ).strip().lower()
    model = str(
        direct.get("model") or llm_cfg.get("model") or ""
    ).strip().lower()
    print(f"[DEBUG policy] main_model_supports_vision: provider={provider!r}, model={model!r}", file=sys.stderr, flush=True)
    if provider == "mimo":
        print(f"[DEBUG policy] main_model_supports_vision: provider is mimo, returning True", file=sys.stderr, flush=True)
        return True
    if provider == "ollama":
        matched = any(marker in model for marker in _KNOWN_OLLAMA_VISION_MARKERS)
        print(f"[DEBUG policy] main_model_supports_vision: provider is ollama, matched any marker={matched}, returning {matched}", file=sys.stderr, flush=True)
        return matched
    print(f"[DEBUG policy] main_model_supports_vision: unknown provider, returning False", file=sys.stderr, flush=True)
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
    print(f"[DEBUG policy] resolve_vision_route called: vision_cfg={dict(vision_cfg) if vision_cfg else None}, llm_cfg keys={list(llm_cfg.keys()) if llm_cfg else None}", file=sys.stderr, flush=True)
    vision = vision_cfg or {}
    llm = llm_cfg or {}
    mode = normalize_vision_mode(vision.get("mode"))
    print(f"[DEBUG policy] resolve_vision_route: normalized mode={mode!r}", file=sys.stderr, flush=True)
    if mode == "disabled":
        result = VisionRoute(mode, True)
        print(f"[DEBUG policy] resolve_vision_route: mode disabled, returning {result}", file=sys.stderr, flush=True)
        return result

    llm_mode = str(llm.get("mode") or "direct").strip().lower()
    if mode == "relay":
        if llm_mode == "agent":
            result = VisionRoute(mode, False, "agent_relay_forbidden")
            print(f"[DEBUG policy] resolve_vision_route: relay but agent mode, returning {result}", file=sys.stderr, flush=True)
            return result
        backend = str(vision.get("backend") or "").strip().lower()
        print(f"[DEBUG policy] resolve_vision_route: relay, backend={backend!r}", file=sys.stderr, flush=True)
        if backend not in {"ollama", "mimo"}:
            result = VisionRoute(mode, False, "relay_backend_not_configured")
            print(f"[DEBUG policy] resolve_vision_route: relay backend not configured, returning {result}", file=sys.stderr, flush=True)
            return result
        result = VisionRoute(mode, True)
        print(f"[DEBUG policy] resolve_vision_route: relay backend ok, returning {result}", file=sys.stderr, flush=True)
        return result

    # mode is inherit
    supports = main_model_supports_vision(vision, llm)
    print(f"[DEBUG policy] resolve_vision_route: inherit mode, main_model_supports_vision={supports}", file=sys.stderr, flush=True)
    if not supports:
        result = VisionRoute(mode, False, "main_model_vision_not_confirmed")
        print(f"[DEBUG policy] resolve_vision_route: inherit but model does not support vision, returning {result}", file=sys.stderr, flush=True)
        return result
    result = VisionRoute(mode, True)
    print(f"[DEBUG policy] resolve_vision_route: inherit ok, returning {result}", file=sys.stderr, flush=True)
    return result


__all__ = [
    "VISION_MODES",
    "VisionRoute",
    "main_model_supports_vision",
    "normalize_vision_mode",
    "resolve_vision_route",
]

