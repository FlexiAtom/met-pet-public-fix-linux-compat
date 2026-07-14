"""
统一配置：只使用 config.json

结构（与 config.example.json 对齐）：
- llm / vision / tts / live2d / character / sprite_dir
- display（含 size_factor / font_scale）
- watcher（含 interval）
- bubble_duration_ms
- tts.sync_with_audio

密钥：环境变量优先于 config 明文（见 resolve_*）。
"""
from __future__ import annotations

import copy
import json
import os
import stat
import sys  # 新增
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

from meapet.config.normalizers import (
    canonical_tts_language,
    normalize_gsv_ref_language,
)
from meapet.ui_theme import normalize_ui_font_scale
from meapet.utils import mask_secret, normalize_watcher
from meapet.vision.policy import normalize_vision_mode


# backend / 字段 → 候选环境变量（按顺序）
ENV_LLM_KEY = {
    "deepseek": ("DEEPSEEK_API_KEY", "MEAPET_API_KEY"),
    "mimo": ("MIMO_API_KEY", "XIAOMIMIMO_API_KEY", "MEAPET_API_KEY"),
    "ollama": (),
    "openclaw": (),
}

ENV_TTS_KEY = ("MIMO_API_KEY", "XIAOMIMIMO_API_KEY")
ENV_TRANSLATE_KEY = ("TRANSLATE_API_KEY", "DEEPSEEK_API_KEY")
ENV_VISION_KEY = ENV_LLM_KEY["mimo"]

SUPPORTED_VISION_BACKENDS = {"ollama", "mimo"}
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MIMO_API_BASE = "https://api.xiaomimimo.com/v1"

_ENV_PLACEHOLDERS = ("", "$ENV", "${ENV}", "env", "ENV")

DEFAULT_BUBBLE = {
    "default": 5000,
    "reply": 8000,
    "watch": 7000,
    "interaction": 3000,
    "thinking": 0,
}

DEFAULT_WATCHER_INTERVAL = {"min_ms": 180000, "max_ms": 360000}

DEFAULT_AGENT_CONTROL = {
    "enabled": False,
    "listen_host": "127.0.0.1",
    "port": 8765,
    "allowed_agent_ip": "127.0.0.1",
    "auth_token": "",
    "allow_insecure_http": False,
    "cert_file": "",
    "key_file": "",
    "ca_file": "",
}


def project_root() -> str:
    print(f"[DEBUG store] project_root called", file=sys.stderr, flush=True)
    from meapet.paths import project_root as _pr
    result = _pr()
    print(f"[DEBUG store] project_root returning: {result!r}", file=sys.stderr, flush=True)
    return result


def config_path(name: str = "config.json") -> str:
    print(f"[DEBUG store] config_path called: name={name!r}", file=sys.stderr, flush=True)
    result = os.path.join(project_root(), name)
    print(f"[DEBUG store] config_path returning: {result!r}", file=sys.stderr, flush=True)
    return result


def resolve_startup_config_path(
    root: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """返回与当前工作目录无关的启动配置路径。"""
    print(f"[DEBUG store] resolve_startup_config_path called: root={root!r}", file=sys.stderr, flush=True)
    base = Path(root) if root is not None else Path(project_root())
    primary = base / "config.json"
    if primary.is_file():
        result = str(primary)
        print(f"[DEBUG store] resolve_startup_config_path: found config.json, returning {result!r}", file=sys.stderr, flush=True)
        return result
    result = str(base / "config.example.json")
    print(f"[DEBUG store] resolve_startup_config_path: no config.json, falling back to example: {result!r}", file=sys.stderr, flush=True)
    return result


def resolve_writable_config_path(
    path: Optional[Union[str, os.PathLike[str]]] = None,
    root: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """把启动/读取路径映射为可写的 config.json。

    从 config.example.json 启动时，首次保存必须落到同目录 config.json，
    避免改写仓库模板。
    """
    print(f"[DEBUG store] resolve_writable_config_path called: path={path!r}, root={root!r}", file=sys.stderr, flush=True)
    base = Path(root) if root is not None else Path(project_root())
    if path is None or str(path).strip() == "":
        result = str(base / "config.json")
        print(f"[DEBUG store] resolve_writable_config_path: path is None/empty, returning default: {result!r}", file=sys.stderr, flush=True)
        return result
    candidate = Path(path)
    if candidate.name == "config.example.json":
        result = str(candidate.with_name("config.json"))
        print(f"[DEBUG store] resolve_writable_config_path: converting example to config.json: {result!r}", file=sys.stderr, flush=True)
        return result
    result = str(candidate)
    print(f"[DEBUG store] resolve_writable_config_path: returning as-is: {result!r}", file=sys.stderr, flush=True)
    return result


def resolve_resource_path(
    path: Union[str, os.PathLike[str]] = "",
    root: Optional[Union[str, os.PathLike[str]]] = None,
) -> str:
    """把相对资源路径锚定到项目根，避免依赖进程 cwd。

    绝对路径原样规范化；空字符串返回空字符串。
    """
    print(f"[DEBUG store] resolve_resource_path called: path={path!r}, root={root!r}", file=sys.stderr, flush=True)
    raw = str(path or "").strip()
    if not raw:
        print(f"[DEBUG store] resolve_resource_path: empty path, returning empty string", file=sys.stderr, flush=True)
        return ""
    p = Path(raw)
    if p.is_absolute():
        result = str(p)
        print(f"[DEBUG store] resolve_resource_path: absolute path, returning {result!r}", file=sys.stderr, flush=True)
        return result
    base = Path(root) if root is not None else Path(project_root())
    result = str((base / p).resolve())
    print(f"[DEBUG store] resolve_resource_path: relative path resolved to {result!r}", file=sys.stderr, flush=True)
    return result


def _first_env(names: Tuple[str, ...]) -> str:
    print(f"[DEBUG store] _first_env called: names={names!r}", file=sys.stderr, flush=True)
    for n in names:
        if not n:
            continue
        v = os.environ.get(n, "").strip()
        if v:
            print(f"[DEBUG store] _first_env: found env var {n!r} = {mask_secret(v)!r}", file=sys.stderr, flush=True)
            return v
    print(f"[DEBUG store] _first_env: no env var found, returning empty string", file=sys.stderr, flush=True)
    return ""




# 小米官方 API model id（不要用 HuggingFace 仓库名 XiaomiMiMo/...）
# 文档: https://mimo.mi.com/docs/en-US/quick-start/summary/model
MIMO_MODEL_ALIASES = {
    "xiaomimimo/mimo-v2.5": "mimo-v2.5",
    "xiaomimimo/mimo-v2.5-pro": "mimo-v2.5-pro",
    "mimo-v2.5": "mimo-v2.5",
    "mimo-v2.5-pro": "mimo-v2.5-pro",
    "mimo": "mimo-v2.5",
    "minicpm-v": "mimo-v2.5",  # 误填时给 vision 一条生路
    "qwen3.5:4b": "mimo-v2.5",  # 同上
}

def normalize_mimo_model_id(model: str, *, for_vision: bool = False) -> str:
    """把常见错误/别名映射成官方 API model id。

    默认使用多模态 `mimo-v2.5`（对话/识图通用）。
    仅当用户显式写 pro 相关名字时才映射到 `mimo-v2.5-pro`。
    """
    print(f"[DEBUG store] normalize_mimo_model_id called: model={model!r}, for_vision={for_vision}", file=sys.stderr, flush=True)
    raw = (model or "").strip()
    if not raw:
        print(f"[DEBUG store] normalize_mimo_model_id: empty model, returning default 'mimo-v2.5'", file=sys.stderr, flush=True)
        return "mimo-v2.5"
    key = raw.lower()
    if key in MIMO_MODEL_ALIASES:
        result = MIMO_MODEL_ALIASES[key]
        print(f"[DEBUG store] normalize_mimo_model_id: alias match, returning {result!r}", file=sys.stderr, flush=True)
        return result
    # HF 风格: XiaomiMiMo/MiMo-V2.5 / XiaomiMiMo/MiMo-V2.5-Pro
    if "mimo-v2.5-pro" in key or "mimo_v2.5_pro" in key or "mimo-v2.5pro" in key:
        print(f"[DEBUG store] normalize_mimo_model_id: detected pro variant, returning 'mimo-v2.5-pro'", file=sys.stderr, flush=True)
        return "mimo-v2.5-pro"
    if "mimo-v2.5" in key or "mimo_v2.5" in key or key.endswith("mimo-v2.5"):
        print(f"[DEBUG store] normalize_mimo_model_id: detected v2.5 variant, returning 'mimo-v2.5'", file=sys.stderr, flush=True)
        return "mimo-v2.5"
    if raw.startswith("XiaomiMiMo/") or raw.startswith("xiaomimimo/"):
        # HF 仓库名默认落到多模态基座，不默认 pro
        print(f"[DEBUG store] normalize_mimo_model_id: HF repo style, returning 'mimo-v2.5'", file=sys.stderr, flush=True)
        return "mimo-v2.5"
    print(f"[DEBUG store] normalize_mimo_model_id: no alias matched, returning original {raw!r}", file=sys.stderr, flush=True)
    return raw
def resolve_secret(file_value: str = "", env_names: Tuple[str, ...] = ()) -> str:
    print(f"[DEBUG store] resolve_secret called: file_value={mask_secret(file_value)!r}, env_names={env_names!r}", file=sys.stderr, flush=True)
    env_val = _first_env(env_names)
    raw = (file_value or "").strip()
    if raw.startswith("${") and raw.endswith("}") and len(raw) > 3:
        env_var = raw[2:-1]
        result = os.environ.get(env_var, "").strip() or env_val
        print(f"[DEBUG store] resolve_secret: ${{\"{env_var}\"}} pattern, result={mask_secret(result)!r}", file=sys.stderr, flush=True)
        return result
    if raw.startswith("$") and len(raw) > 1 and raw[1:].replace("_", "").isalnum():
        env_var = raw[1:]
        result = os.environ.get(env_var, "").strip() or env_val
        print(f"[DEBUG store] resolve_secret: $\"{env_var}\" pattern, result={mask_secret(result)!r}", file=sys.stderr, flush=True)
        return result
    if raw in _ENV_PLACEHOLDERS or raw.upper() == "$ENV":
        print(f"[DEBUG store] resolve_secret: placeholder match, returning env_val={mask_secret(env_val)!r}", file=sys.stderr, flush=True)
        return env_val
    if env_val:
        print(f"[DEBUG store] resolve_secret: env_val present, returning it: {mask_secret(env_val)!r}", file=sys.stderr, flush=True)
        return env_val
    print(f"[DEBUG store] resolve_secret: returning raw file_value: {mask_secret(raw)!r}", file=sys.stderr, flush=True)
    return raw


def save_config(config: dict, path: Optional[str] = None) -> None:
    print(f"[DEBUG store] save_config called: path={path!r}, config keys={list(config.keys())}", file=sys.stderr, flush=True)
    cpath = path or config_path()
    print(f"[DEBUG store] save_config: resolved cpath={cpath!r}", file=sys.stderr, flush=True)
    existing = load_json(cpath, {})
    print(f"[DEBUG store] save_config: loaded existing keys={list(existing.keys())}", file=sys.stderr, flush=True)
    merged = _deep_merge(existing, config)
    print(f"[DEBUG store] save_config: merged keys={list(merged.keys())}", file=sys.stderr, flush=True)
    normalized = normalize_config(merged)
    print(f"[DEBUG store] save_config: normalized keys={list(normalized.keys())}", file=sys.stderr, flush=True)
    save_json(cpath, normalized)
    print(f"[DEBUG store] save_config: saved to {cpath!r}", file=sys.stderr, flush=True)


def resolve_llm_api_key(llm_cfg: dict) -> str:
    print(f"[DEBUG store] resolve_llm_api_key called: llm_cfg keys={list(llm_cfg.keys())}", file=sys.stderr, flush=True)
    backend = (llm_cfg.get("backend") or "ollama").lower()
    names = ENV_LLM_KEY.get(backend, ("MEAPET_API_KEY",))
    print(f"[DEBUG store] resolve_llm_api_key: backend={backend!r}, env_names={names!r}", file=sys.stderr, flush=True)
    result = resolve_secret(llm_cfg.get("api_key", ""), names)
    print(f"[DEBUG store] resolve_llm_api_key returning: {mask_secret(result)!r}", file=sys.stderr, flush=True)
    return result


def resolve_direct_api_key(llm_cfg: dict) -> str:
    print(f"[DEBUG store] resolve_direct_api_key called: llm_cfg keys={list(llm_cfg.keys())}", file=sys.stderr, flush=True)
    direct = llm_cfg.get("direct") if isinstance(llm_cfg.get("direct"), dict) else {}
    provider = str(direct.get("provider") or llm_cfg.get("backend") or "custom").lower()
    names = ENV_LLM_KEY.get(provider, ("MEAPET_API_KEY",))
    print(f"[DEBUG store] resolve_direct_api_key: provider={provider!r}, env_names={names!r}", file=sys.stderr, flush=True)
    value = resolve_secret(str(direct.get("api_key") or ""), names)
    if value:
        print(f"[DEBUG store] resolve_direct_api_key: direct key found, returning {mask_secret(value)!r}", file=sys.stderr, flush=True)
        return value
    fallback = resolve_llm_api_key(llm_cfg)
    print(f"[DEBUG store] resolve_direct_api_key: fallback to llm key: {mask_secret(fallback)!r}", file=sys.stderr, flush=True)
    return fallback


def resolve_tts_api_key(tts_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    print(f"[DEBUG store] resolve_tts_api_key called: tts_cfg keys={list(tts_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys()) if llm_cfg else None}", file=sys.stderr, flush=True)
    llm_cfg = llm_cfg or {}
    resolved = resolve_secret(tts_cfg.get("api_key", ""), ENV_TTS_KEY)
    print(f"[DEBUG store] resolve_tts_api_key: resolved from tts={mask_secret(resolved)!r}", file=sys.stderr, flush=True)
    if resolved:
        print(f"[DEBUG store] resolve_tts_api_key: returning tts key", file=sys.stderr, flush=True)
        return resolved
    if (llm_cfg.get("backend") or "").lower() == "mimo":
        fallback = resolve_llm_api_key(llm_cfg)
        print(f"[DEBUG store] resolve_tts_api_key: falling back to llm key (mimo): {mask_secret(fallback)!r}", file=sys.stderr, flush=True)
        return fallback
    print(f"[DEBUG store] resolve_tts_api_key: returning empty string", file=sys.stderr, flush=True)
    return ""


def resolve_translate_api_key(tts_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    print(f"[DEBUG store] resolve_translate_api_key called: tts_cfg keys={list(tts_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys()) if llm_cfg else None}", file=sys.stderr, flush=True)
    llm_cfg = llm_cfg or {}
    resolved = resolve_secret(
        tts_cfg.get("translate_api_key", ""),
        ENV_TRANSLATE_KEY,
    )
    print(f"[DEBUG store] resolve_translate_api_key: resolved from tts translate={mask_secret(resolved)!r}", file=sys.stderr, flush=True)
    if resolved:
        print(f"[DEBUG store] resolve_translate_api_key: returning translate key", file=sys.stderr, flush=True)
        return resolved
    if (llm_cfg.get("backend") or "").lower() == "deepseek":
        fallback = resolve_llm_api_key(llm_cfg)
        print(f"[DEBUG store] resolve_translate_api_key: falling back to llm key (deepseek): {mask_secret(fallback)!r}", file=sys.stderr, flush=True)
        return fallback
    print(f"[DEBUG store] resolve_translate_api_key: returning empty string", file=sys.stderr, flush=True)
    return ""


def resolve_vision_backend(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    print(f"[DEBUG store] resolve_vision_backend called: vision_cfg keys={list(vision_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys()) if llm_cfg else None}", file=sys.stderr, flush=True)
    llm_cfg = llm_cfg or {}
    backend = (
        vision_cfg.get("backend")
        or llm_cfg.get("backend")
        or "ollama"
    ).lower()
    result = backend if backend in SUPPORTED_VISION_BACKENDS else "ollama"
    print(f"[DEBUG store] resolve_vision_backend: backend={backend!r}, in_supported={backend in SUPPORTED_VISION_BACKENDS}, returning {result!r}", file=sys.stderr, flush=True)
    return result


def resolve_vision_api_key(vision_cfg: dict, llm_cfg: Optional[dict] = None) -> str:
    print(f"[DEBUG store] resolve_vision_api_key called: vision_cfg keys={list(vision_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys()) if llm_cfg else None}", file=sys.stderr, flush=True)
    llm_cfg = llm_cfg or {}
    backend = resolve_vision_backend(vision_cfg, llm_cfg)
    print(f"[DEBUG store] resolve_vision_api_key: backend={backend!r}", file=sys.stderr, flush=True)
    if backend != "mimo":
        print(f"[DEBUG store] resolve_vision_api_key: not mimo, returning empty string", file=sys.stderr, flush=True)
        return ""
    resolved = resolve_secret(
        vision_cfg.get("api_key", ""),
        ENV_LLM_KEY["mimo"],
    )
    print(f"[DEBUG store] resolve_vision_api_key: resolved from vision={mask_secret(resolved)!r}", file=sys.stderr, flush=True)
    if resolved:
        print(f"[DEBUG store] resolve_vision_api_key: returning vision key", file=sys.stderr, flush=True)
        return resolved
    if (llm_cfg.get("backend") or "").lower() == "mimo":
        fallback = resolve_llm_api_key(llm_cfg)
        print(f"[DEBUG store] resolve_vision_api_key: falling back to llm key (mimo): {mask_secret(fallback)!r}", file=sys.stderr, flush=True)
        return fallback
    print(f"[DEBUG store] resolve_vision_api_key: returning empty string", file=sys.stderr, flush=True)
    return ""


def resolve_vision_api_base(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    print(f"[DEBUG store] resolve_vision_api_base called: vision_cfg keys={list(vision_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys()) if llm_cfg else None}", file=sys.stderr, flush=True)
    llm_cfg = llm_cfg or {}
    if resolve_vision_backend(vision_cfg, llm_cfg) != "mimo":
        print(f"[DEBUG store] resolve_vision_api_base: not mimo, returning empty string", file=sys.stderr, flush=True)
        return ""
    explicit = (vision_cfg.get("api_base") or "").strip()
    if explicit:
        print(f"[DEBUG store] resolve_vision_api_base: explicit vision api_base={explicit!r}", file=sys.stderr, flush=True)
        return explicit
    if (llm_cfg.get("backend") or "").lower() == "mimo":
        inherited = (llm_cfg.get("api_base") or "").strip()
        if inherited:
            print(f"[DEBUG store] resolve_vision_api_base: inherited from llm api_base={inherited!r}", file=sys.stderr, flush=True)
            return inherited
    print(f"[DEBUG store] resolve_vision_api_base: returning DEFAULT_MIMO_API_BASE={DEFAULT_MIMO_API_BASE!r}", file=sys.stderr, flush=True)
    return DEFAULT_MIMO_API_BASE


def resolve_vision_host(
    vision_cfg: dict,
    llm_cfg: Optional[dict] = None,
) -> str:
    print(f"[DEBUG store] resolve_vision_host called: vision_cfg keys={list(vision_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys()) if llm_cfg else None}", file=sys.stderr, flush=True)
    llm_cfg = llm_cfg or {}
    explicit = (vision_cfg.get("host") or "").strip()
    if explicit:
        print(f"[DEBUG store] resolve_vision_host: explicit vision host={explicit!r}", file=sys.stderr, flush=True)
        return explicit
    if (llm_cfg.get("backend") or "").lower() == "ollama":
        inherited = (llm_cfg.get("host") or "").strip()
        if inherited:
            print(f"[DEBUG store] resolve_vision_host: inherited from llm host={inherited!r}", file=sys.stderr, flush=True)
            return inherited
    print(f"[DEBUG store] resolve_vision_host: returning DEFAULT_OLLAMA_HOST={DEFAULT_OLLAMA_HOST!r}", file=sys.stderr, flush=True)
    return DEFAULT_OLLAMA_HOST


def load_json(path: str, default: Optional[dict] = None) -> dict:
    print(f"[DEBUG store] load_json called: path={path!r}, default keys={list(default.keys()) if default else None}", file=sys.stderr, flush=True)
    default = default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        result = data if isinstance(data, dict) else copy.deepcopy(default)
        print(f"[DEBUG store] load_json: loaded successfully, result keys={list(result.keys())}", file=sys.stderr, flush=True)
        return result
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(f"[DEBUG store] load_json: exception {type(exc).__name__}, returning default", file=sys.stderr, flush=True)
        return copy.deepcopy(default)


def save_json(path: str, data: dict) -> None:
    """原子写入 JSON；数据内容（包括现有 Key）原样保存。"""
    print(f"[DEBUG store] save_json called: path={path!r}, data keys={list(data.keys())}", file=sys.stderr, flush=True)
    target = os.path.abspath(path)
    parent = os.path.dirname(target) or os.curdir
    existing_mode = None
    try:
        existing_mode = stat.S_IMODE(os.stat(target).st_mode)
        print(f"[DEBUG store] save_json: existing_mode={oct(existing_mode) if existing_mode else None}", file=sys.stderr, flush=True)
    except OSError:
        print(f"[DEBUG store] save_json: could not stat target, no existing_mode", file=sys.stderr, flush=True)
        pass

    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{os.path.basename(target)}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            tmp_path = f.name
            print(f"[DEBUG store] save_json: created temp file {tmp_path!r}", file=sys.stderr, flush=True)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
            print(f"[DEBUG store] save_json: written and fsynced", file=sys.stderr, flush=True)
        if existing_mode is not None:
            os.chmod(tmp_path, existing_mode)
            print(f"[DEBUG store] save_json: chmod to {oct(existing_mode)}", file=sys.stderr, flush=True)
        os.replace(tmp_path, target)
        print(f"[DEBUG store] save_json: replaced target with temp file", file=sys.stderr, flush=True)
        tmp_path = ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
                print(f"[DEBUG store] save_json: cleaned up temp file {tmp_path!r}", file=sys.stderr, flush=True)
            except OSError:
                print(f"[DEBUG store] save_json: failed to clean up temp file {tmp_path!r}", file=sys.stderr, flush=True)
                pass


def _deep_merge(base: dict, overlay: dict) -> dict:
    print(f"[DEBUG store] _deep_merge called: base keys={list(base.keys())}, overlay keys={list(overlay.keys())}", file=sys.stderr, flush=True)
    out = copy.deepcopy(base or {})
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            print(f"[DEBUG store] _deep_merge: recursing into key {k!r}", file=sys.stderr, flush=True)
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
            print(f"[DEBUG store] _deep_merge: set key {k!r} to new value", file=sys.stderr, flush=True)
    print(f"[DEBUG store] _deep_merge: returning out with {len(out)} keys", file=sys.stderr, flush=True)
    return out


def _normalize_llm_contract(value: object) -> dict:
    print(f"[DEBUG store] _normalize_llm_contract called: value type={type(value).__name__}, value={value!r}", file=sys.stderr, flush=True)
    llm = copy.deepcopy(value) if isinstance(value, dict) else {}
    backend = str(llm.get("backend") or "ollama").strip().lower() or "ollama"
    requested_mode = str(llm.get("mode") or "").strip().lower()
    if requested_mode not in {"direct", "agent"}:
        requested_mode = "agent" if backend in {"hermes", "openclaw"} else "direct"
    print(f"[DEBUG store] _normalize_llm_contract: backend={backend!r}, requested_mode={requested_mode!r}", file=sys.stderr, flush=True)

    direct = copy.deepcopy(llm.get("direct")) if isinstance(llm.get("direct"), dict) else {}
    provider = backend if backend not in {"hermes", "openclaw"} else "ollama"
    direct.setdefault("provider", provider)
    direct.setdefault("protocol", "ollama_chat" if provider == "ollama" else "openai_chat")
    direct.setdefault("api_base", str(llm.get("api_base") or "").strip())
    direct.setdefault("host", str(llm.get("host") or "").strip())
    direct.setdefault("model", str(llm.get("model") or "").strip())
    direct.setdefault("api_key", str(llm.get("api_key") or "").strip())
    direct.setdefault("temperature", llm.get("temperature", 0.7))
    direct.setdefault("max_tokens", llm.get("max_tokens", 512))
    print(f"[DEBUG store] _normalize_llm_contract: direct keys={list(direct.keys())}", file=sys.stderr, flush=True)

    agent = copy.deepcopy(llm.get("agent")) if isinstance(llm.get("agent"), dict) else {}
    kind = str(agent.get("kind") or "").strip().lower()
    if kind not in {"hermes", "openclaw"}:
        kind = backend if backend in {"hermes", "openclaw"} else "hermes"
    default_url = (
        "ws://127.0.0.1:18789"
        if kind == "openclaw"
        else "http://127.0.0.1:8642"
    )
    agent["kind"] = kind
    agent.setdefault(
        "base_url",
        str(llm.get("bridge_url") or default_url).strip() or default_url,
    )
    agent.setdefault("auth_token", "")
    agent.setdefault("session_id", "")
    agent.setdefault("session_key", "")
    agent.setdefault("history_turns", 5)
    agent.setdefault("allow_insecure_ws", False)
    agent.setdefault("identity_path", "")
    tls = copy.deepcopy(agent.get("tls")) if isinstance(agent.get("tls"), dict) else {}
    tls.setdefault("verify", True)
    tls.setdefault("ca_file", "")
    agent["tls"] = tls
    print(f"[DEBUG store] _normalize_llm_contract: agent keys={list(agent.keys())}", file=sys.stderr, flush=True)

    llm["mode"] = requested_mode
    llm["direct"] = direct
    llm["agent"] = agent
    print(f"[DEBUG store] _normalize_llm_contract: returning llm keys={list(llm.keys())}", file=sys.stderr, flush=True)
    return llm


def _normalize_agent_control(value: object) -> dict:
    print(f"[DEBUG store] _normalize_agent_control called: value type={type(value).__name__}, value={value!r}", file=sys.stderr, flush=True)
    control = copy.deepcopy(value) if isinstance(value, dict) else {}
    for key, default in DEFAULT_AGENT_CONTROL.items():
        control.setdefault(key, default)
    control["enabled"] = bool(control.get("enabled", False))
    control["allow_insecure_http"] = bool(
        control.get("allow_insecure_http", False)
    )
    control["listen_host"] = (
        str(control.get("listen_host") or "127.0.0.1").strip() or "127.0.0.1"
    )
    control["allowed_agent_ip"] = (
        str(control.get("allowed_agent_ip") or "127.0.0.1").strip()
        or "127.0.0.1"
    )
    try:
        port = int(control.get("port", 8765))
    except (TypeError, ValueError):
        port = 8765
    control["port"] = port if 1 <= port <= 65535 else 8765
    for key in ("auth_token", "cert_file", "key_file", "ca_file"):
        control[key] = str(control.get(key) or "").strip()
    print(f"[DEBUG store] _normalize_agent_control: returning control keys={list(control.keys())}", file=sys.stderr, flush=True)
    return control


def _normalize_reference_audios(tts: dict) -> dict:
    print(f"[DEBUG store] _normalize_reference_audios called: tts keys={list(tts.keys())}", file=sys.stderr, flush=True)
    raw_mapping = tts.get("reference_audios")
    mapping = {}
    if isinstance(raw_mapping, dict):
        for raw_language, raw_entry in raw_mapping.items():
            language = normalize_gsv_ref_language(raw_language)
            if isinstance(raw_entry, dict):
                path = str(raw_entry.get("path") or "").strip()
                text = str(raw_entry.get("text") or "").strip()
            else:
                path = str(raw_entry or "").strip()
                text = ""
            if path or text:
                mapping[language] = {"path": path, "text": text}
                print(f"[DEBUG store] _normalize_reference_audios: mapped language={language!r}, path={path!r}, text_len={len(text)}", file=sys.stderr, flush=True)

    legacy_path = str(tts.get("gsv_ref_wav") or "").strip()
    legacy_language = normalize_gsv_ref_language(tts.get("gsv_ref_lang"))
    if legacy_path and legacy_language not in mapping:
        mapping[legacy_language] = {"path": legacy_path, "text": ""}
        print(f"[DEBUG store] _normalize_reference_audios: migrated legacy ref: lang={legacy_language!r}, path={legacy_path!r}", file=sys.stderr, flush=True)
    print(f"[DEBUG store] _normalize_reference_audios: returning mapping keys={list(mapping.keys())}", file=sys.stderr, flush=True)
    return mapping


def normalize_config(config: dict) -> dict:
    """补全默认字段、规范化 watcher / bubble / display / tts.sync"""
    print(f"[DEBUG store] normalize_config called: config keys={list(config.keys())}", file=sys.stderr, flush=True)
    cfg = copy.deepcopy(config or {})

    cfg["llm"] = _normalize_llm_contract(cfg.get("llm"))
    cfg.setdefault("vision", {})
    cfg.setdefault("tts", {})
    cfg.setdefault("display", {})
    cfg.setdefault("character", {})
    cfg.setdefault("live2d", {})
    cfg["agent_control"] = _normalize_agent_control(cfg.get("agent_control"))
    print(f"[DEBUG store] normalize_config: after llm/agent_control normalization", file=sys.stderr, flush=True)

    # bubble
    bub = cfg.get("bubble_duration_ms") if isinstance(cfg.get("bubble_duration_ms"), dict) else {}
    for k, v in DEFAULT_BUBBLE.items():
        bub.setdefault(k, v)
    cfg["bubble_duration_ms"] = bub
    print(f"[DEBUG store] normalize_config: bubble keys={list(bub.keys())}", file=sys.stderr, flush=True)

    # display
    disp = cfg.get("display") if isinstance(cfg.get("display"), dict) else {}
    disp.setdefault("scale", 0.5)
    disp.setdefault("fps", 30)
    disp.setdefault("size_factor", 1.0)
    disp["font_scale"] = normalize_ui_font_scale(
        disp.get("font_scale", 1.0)
    )
    disp["reduced_motion"] = bool(disp.get("reduced_motion", False))
    cfg["display"] = disp
    print(f"[DEBUG store] normalize_config: display keys={list(disp.keys())}", file=sys.stderr, flush=True)

    # UI 一次性引导等非敏感本地状态
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    ui["first_run_hint_shown"] = bool(ui.get("first_run_hint_shown", False))
    try:
        timeline_turns = int(ui.get("timeline_turns", 5))
    except (TypeError, ValueError):
        timeline_turns = 5
    ui["timeline_turns"] = max(0, min(timeline_turns, 100))
    cfg["ui"] = ui
    print(f"[DEBUG store] normalize_config: ui keys={list(ui.keys())}", file=sys.stderr, flush=True)

    # TTS：音频同步 + 可选固定 GPT-SoVITS 参考音频
    tts = cfg.get("tts") if isinstance(cfg.get("tts"), dict) else {}
    if "sync_with_audio" not in tts:
        tts["sync_with_audio"] = False
    else:
        tts["sync_with_audio"] = bool(tts["sync_with_audio"])
    tts["gsv_ref_wav"] = str(tts.get("gsv_ref_wav") or "").strip()
    tts["gsv_ref_lang"] = normalize_gsv_ref_language(
        tts.get("gsv_ref_lang")
    )
    tts["reference_audios"] = _normalize_reference_audios(tts)
    tts["translate_to_jp"] = bool(tts.get("translate_to_jp", False))
    tts["translate_target_language"] = canonical_tts_language(
        tts.get("translate_target_language")
        or tts.get("voice_lang")
        or "jp"
    )
    raw_supported = tts.get("supported_languages")
    if isinstance(raw_supported, (list, tuple)):
        supported = []
        for value in raw_supported:
            language = canonical_tts_language(value)
            if language and language not in supported:
                supported.append(language)
        tts["supported_languages"] = supported
    else:
        tts.pop("supported_languages", None)
    cfg["tts"] = tts
    print(f"[DEBUG store] normalize_config: tts keys={list(tts.keys())}", file=sys.stderr, flush=True)

    # watcher 统一结构（interval 内嵌，不再用顶层 watcher_interval）
    w_in = cfg.get("watcher") if isinstance(cfg.get("watcher"), dict) else {}
    # 兼容旧顶层 watcher_interval
    if "interval" not in w_in or not isinstance(w_in.get("interval"), dict):
        top_wi = cfg.get("watcher_interval") if isinstance(cfg.get("watcher_interval"), dict) else {}
        if top_wi:
            w_in = dict(w_in)
            w_in["interval"] = {
                "min_ms": int(top_wi.get("min_ms", DEFAULT_WATCHER_INTERVAL["min_ms"])),
                "max_ms": int(top_wi.get("max_ms", DEFAULT_WATCHER_INTERVAL["max_ms"])),
            }
            print(f"[DEBUG store] normalize_config: migrated old watcher_interval to watcher.interval", file=sys.stderr, flush=True)
    w = normalize_watcher(w_in)
    # normalize_watcher 已含 interval；强制安全底线
    w["require_confirm"] = True
    w["confirm_once_session"] = False
    watcher_out = copy.deepcopy(w_in)
    interval_out = (
        copy.deepcopy(watcher_out.get("interval"))
        if isinstance(watcher_out.get("interval"), dict)
        else {}
    )
    interval_out.update(w["interval"])
    watcher_out.update({
        "enabled": w["enabled"],
        "allow_cloud": w["allow_cloud"],
        "require_confirm": True,
        "confirm_once_session": False,
        "interval": interval_out,
    })
    raw_capture = (
        watcher_out.get("capture")
        if isinstance(watcher_out.get("capture"), dict)
        else {}
    )
    scope = str(raw_capture.get("scope") or "full_screen").strip().lower()
    if scope not in {"full_screen", "region", "application"}:
        scope = "full_screen"
    region = raw_capture.get("region")
    normalized_region = None
    if isinstance(region, dict):
        try:
            candidate = {
                key: int(region[key])
                for key in ("x", "y", "width", "height")
            }
            if candidate["width"] > 0 and candidate["height"] > 0:
                normalized_region = candidate
        except (KeyError, TypeError, ValueError):
            normalized_region = None
    if scope == "region" and normalized_region is None:
        scope = "full_screen"
    application = str(raw_capture.get("application") or "").strip()[:256]
    if scope == "application" and not application:
        scope = "full_screen"
    watcher_out["capture"] = {
        "scope": scope,
        "region": normalized_region if scope == "region" else None,
        "application": application if scope == "application" else "",
    }
    print(f"[DEBUG store] normalize_config: watcher keys={list(watcher_out.keys())}, capture scope={scope!r}", file=sys.stderr, flush=True)

    vision = (
        copy.deepcopy(cfg.get("vision"))
        if isinstance(cfg.get("vision"), dict)
        else {}
    )
    if "mode" in vision:
        vision_mode = normalize_vision_mode(vision.get("mode"))
    else:
        # 旧 watcher 会独立调用视觉模型，因此只能忠实迁移为 relay。
        legacy_enabled = bool(
            vision.get("enabled", watcher_out.get("enabled", False))
        )
        vision_mode = "relay" if legacy_enabled else "disabled"
    vision["mode"] = vision_mode
    vision["enabled"] = vision_mode != "disabled"
    vision["main_model_supports_images"] = bool(
        vision.get("main_model_supports_images", False)
    )
    if vision_mode == "disabled":
        watcher_out["enabled"] = False
    cfg["vision"] = vision
    cfg["watcher"] = watcher_out
    print(f"[DEBUG store] normalize_config: vision keys={list(vision.keys())}, watcher enabled={watcher_out.get('enabled')}", file=sys.stderr, flush=True)
    # 保留旧 watcher_interval 和未知字段，避免规范化时删除用户配置。
    print(f"[DEBUG store] normalize_config: returning cfg with {len(cfg)} keys", file=sys.stderr, flush=True)
    return cfg


def load_config(path: Optional[str] = None) -> dict:
    """加载统一 config.json 并补全默认字段。"""
    print(f"[DEBUG store] load_config called: path={path!r}", file=sys.stderr, flush=True)
    cpath = path or config_path()
    print(f"[DEBUG store] load_config: resolved cpath={cpath!r}", file=sys.stderr, flush=True)
    raw = load_json(cpath, {})
    print(f"[DEBUG store] load_config: loaded raw keys={list(raw.keys())}", file=sys.stderr, flush=True)
    result = normalize_config(raw)
    print(f"[DEBUG store] load_config: returning normalized config with {len(result)} keys", file=sys.stderr, flush=True)
    return result


def scrub_secrets(config: dict) -> dict:
    print(f"[DEBUG store] scrub_secrets called: config keys={list(config.keys())}", file=sys.stderr, flush=True)
    out = copy.deepcopy(config or {})
    if "llm" in out and isinstance(out["llm"], dict):
        out["llm"]["api_key"] = ""
        direct = out["llm"].get("direct")
        if isinstance(direct, dict):
            direct["api_key"] = ""
        agent = out["llm"].get("agent")
        if isinstance(agent, dict):
            agent["auth_token"] = ""
    if "tts" in out and isinstance(out["tts"], dict):
        out["tts"]["api_key"] = ""
        out["tts"]["translate_api_key"] = ""
    if "vision" in out and isinstance(out["vision"], dict):
        out["vision"]["api_key"] = ""
    if "agent_control" in out and isinstance(out["agent_control"], dict):
        out["agent_control"]["auth_token"] = ""
    print(f"[DEBUG store] scrub_secrets: secrets scrubbed", file=sys.stderr, flush=True)
    return out


def secret_status(config: dict) -> Dict[str, str]:
    print(f"[DEBUG store] secret_status called: config keys={list(config.keys())}", file=sys.stderr, flush=True)
    llm = config.get("llm") or {}
    tts = config.get("tts") or {}
    vision = config.get("vision") or {}
    llm_key = resolve_llm_api_key(llm)
    tts_key = resolve_tts_api_key(tts, llm)
    tr_key = resolve_translate_api_key(tts, llm)
    vis_key = resolve_vision_api_key(vision, llm)

    def src(file_val: str, resolved: str, envs: Tuple[str, ...]) -> str:
        if not resolved:
            return "missing"
        env_hit = _first_env(envs)
        if env_hit and resolved == env_hit:
            return "env:" + ",".join(envs[:2])
        if (file_val or "").strip():
            return "file"
        return "unknown"

    result = {
        "llm": src(llm.get("api_key", ""), llm_key, ENV_LLM_KEY.get((llm.get("backend") or "").lower(), ("MEAPET_API_KEY",))),
        "tts": src(tts.get("api_key", ""), tts_key, ENV_TTS_KEY),
        "translate": src(tts.get("translate_api_key", ""), tr_key, ENV_TRANSLATE_KEY),
        "vision": src(vision.get("api_key", ""), vis_key, ENV_VISION_KEY),
        "llm_preview": mask_secret(llm_key) if llm_key else "",
    }
    print(f"[DEBUG store] secret_status returning: {result}", file=sys.stderr, flush=True)
    return result

