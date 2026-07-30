"""MeaPet 的公开配置默认值。

该模块只依赖标准库，供配置规范化、运行时、向导和启动预检共同引用。模板
``config.example.json`` 由回归测试校验与这里一致，避免某个页面自行维护一套
看似相同、实际已漂移的兜底值。
"""

from __future__ import annotations

from types import MappingProxyType


DEFAULT_OPENAI_API_BASE = "https://api.openai.com/v1"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_MIMO_API_BASE = "https://api.xiaomimimo.com/v1"

DEFAULT_HERMES_WS_URL = "ws://127.0.0.1:9119/api/ws"
DEFAULT_OPENCLAW_WS_URL = "ws://127.0.0.1:18789"
DEFAULT_AGENT_LINK_WS_URL = "ws://127.0.0.1:8766/agent-link"

DEFAULT_DIRECT_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_VISION_MODEL = "qwen3.5:4b"
DEFAULT_MIMO_VISION_MODEL = "mimo-v2.5"
DEFAULT_MIMO_TTS_MODEL = "mimo-v2.5-tts"
DEFAULT_MIMO_TTS_CLONE_MODEL = "mimo-v2.5-tts-voiceclone"

DEFAULT_GSV_GPT_WEIGHTS_DIR = "./models/GPT_weights"
DEFAULT_GSV_SOVITS_WEIGHTS_DIR = "./models/SoVITS_weights"
DEFAULT_GSV_GPT_MODEL = "mea_pro-e50.ckpt"
DEFAULT_GSV_SOVITS_MODEL = "mea_pro_e24_s13704.pth"

DEFAULT_AGENT_TIMEOUT_SECONDS = 120.0
DEFAULT_AGENT_HISTORY_TURNS = 5
DEFAULT_CONTROL_PORT = 8765
MIN_ERROR_BUBBLE_MS = 10_000
MIN_CONFIG_APPLIED_BUBBLE_MS = 3_500

DEFAULT_WATCHER_INTERVAL = MappingProxyType(
    {
        "min_ms": 180_000,
        "max_ms": 360_000,
    }
)

DEFAULT_BUBBLE_DURATIONS = MappingProxyType(
    {
        "default": 5_000,
        "reply": 8_000,
        "watch": 7_000,
        "interaction": 3_000,
        "thinking": 0,
    }
)

DEFAULT_AGENT_CONTROL = MappingProxyType(
    {
        "enabled": False,
        "listen_host": "127.0.0.1",
        "port": DEFAULT_CONTROL_PORT,
        "allowed_agent_ip": "127.0.0.1",
        "auth_token": "",
        "allow_insecure_http": False,
        "cert_file": "",
        "key_file": "",
        "ca_file": "",
    }
)

# Live2D 视觉视口兼容键：保留历史椭圆参数，但运行时仅使用其外接矩形
# 裁去画布透明留白，不再把椭圆形状应用到绘制或 OS 窗口区域。
DEFAULT_LIVE2D_WINDOW_MASK = MappingProxyType(
    {
        "enabled": True,
        "cx": 0.54,
        "cy": 0.40,
        "rw": 0.30,
        "rh": 0.40,
    }
)

# 模型在桌面上保持不动的画布参照点。默认取推荐视觉视口的底部中心，
# 使旧配置升级后与原先固定“窗口底部中心”的视觉位置一致。
DEFAULT_LIVE2D_PLACEMENT_ANCHOR = MappingProxyType(
    {
        "x": 0.54,
        "y": 0.80,
    }
)


def default_agent_url(kind: object) -> str:
    """按 Agent 类型返回本地 WebSocket 默认地址。"""
    normalized = str(kind or "").strip().lower()
    if normalized == "openclaw":
        return DEFAULT_OPENCLAW_WS_URL
    if normalized == "agent_link":
        return DEFAULT_AGENT_LINK_WS_URL
    return DEFAULT_HERMES_WS_URL


def bubble_duration_ms(
    config: object,
    kind: str = "default",
    *,
    fallback: int | None = None,
) -> int:
    """读取并约束气泡时长，未知类别回落到统一默认值。"""
    normalized_kind = str(kind or "default").strip() or "default"
    default = DEFAULT_BUBBLE_DURATIONS.get(
        normalized_kind,
        DEFAULT_BUBBLE_DURATIONS["default"],
    )
    if fallback is not None:
        try:
            default = max(0, int(fallback))
        except (TypeError, ValueError):
            pass
    root = config if isinstance(config, dict) else {}
    durations = root.get("bubble_duration_ms")
    durations = durations if isinstance(durations, dict) else {}
    try:
        return max(0, int(durations.get(normalized_kind, default)))
    except (TypeError, ValueError):
        return int(default)
