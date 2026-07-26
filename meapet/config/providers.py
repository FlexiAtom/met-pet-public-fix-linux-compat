"""内置模型供应商预设。

每条预设只是"便捷填充"数据：显示名 + OpenAI 兼容 api_base + 推荐直连协议 +
该厂商 API Key 的常见环境变量名。选择预设只会把这些值填进直连配置，
provider 身份仍统一保存为 custom（见 config.store.normalize_direct_provider）。

这些都是公开的接口地址与厂商名等事实性配置信息，用 MeaPet 自己的结构组织，
供向导下拉与 store 的协议/密钥识别复用（单一数据源，避免各处硬编码漂移）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


# 直连协议标识，与 meapet/direct/client.py 支持的协议一致。
PROTO_OPENAI = "openai_chat"
PROTO_OLLAMA = "ollama_chat"
PROTO_ANTHROPIC = "anthropic_messages"


@dataclass(frozen=True)
class ProviderPreset:
    """一个模型供应商的便捷预设。

    id            稳定标识（family），用于协议/密钥映射。
    name          下拉展示名。
    api_base      OpenAI 兼容基础地址；本地/自建（如 Azure）留空由用户填写。
    protocol      推荐直连协议（openai_chat / ollama_chat / anthropic_messages）。
    env_keys      该厂商 API Key 的常见环境变量名（含中立的 MEAPET_API_KEY 兜底）。
    url_signatures 地址中出现即可判定为本供应商的子串（供 store 反查 family）。
    requires_key  是否需要 API Key（本地推理如 Ollama 不需要）。
    models        该厂商常见模型 ID（首个作为选中预设时的默认模型）；
                  聚合/本地供应商无固定清单时留空，交由「获取模型列表」或手填。
    headers       该厂商建议附加的自定义请求头（键值对元组，dataclass 需可哈希）。
                  鉴权头本身由协议决定（openai/ollama 用 Authorization: Bearer，
                  anthropic 用 x-api-key + anthropic-version），此处只放额外头，
                  例如 OpenRouter 用于统计来源的 HTTP-Referer / X-Title。
    note          UI 提示（例如"填你的部署地址"）。
    """

    id: str
    name: str
    api_base: str = ""
    protocol: str = PROTO_OPENAI
    env_keys: Tuple[str, ...] = ()
    url_signatures: Tuple[str, ...] = ()
    requires_key: bool = True
    models: Tuple[str, ...] = ()
    headers: Tuple[Tuple[str, str], ...] = ()
    note: str = ""

    @property
    def default_model(self) -> str:
        return self.models[0] if self.models else ""

    @property
    def headers_dict(self) -> dict:
        return {str(k): str(v) for k, v in self.headers}

    @property
    def family_env_keys(self) -> Tuple[str, ...]:
        # 统一追加中立兜底变量，去重且保持顺序。
        keys = list(self.env_keys)
        if "MEAPET_API_KEY" not in keys:
            keys.append("MEAPET_API_KEY")
        return tuple(dict.fromkeys(keys))


# 云端 OpenAI 兼容供应商 + Anthropic 原生 + 本地推理。顺序即下拉展示顺序。
PROVIDER_PRESETS: Tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "openai", "OpenAI", "https://api.openai.com/v1",
        env_keys=("OPENAI_API_KEY",), url_signatures=("api.openai.com",),
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "o3-mini"),
    ),
    ProviderPreset(
        "deepseek", "DeepSeek 深度求索", "https://api.deepseek.com/v1",
        env_keys=("DEEPSEEK_API_KEY",), url_signatures=("deepseek.com",),
        models=("deepseek-chat", "deepseek-reasoner"),
    ),
    ProviderPreset(
        "anthropic", "Anthropic Claude", "https://api.anthropic.com/v1",
        protocol=PROTO_ANTHROPIC,
        env_keys=("ANTHROPIC_API_KEY",), url_signatures=("api.anthropic.com",),
        models=(
            "claude-3-5-sonnet-latest",
            "claude-3-5-haiku-latest",
            "claude-3-opus-latest",
        ),
    ),
    ProviderPreset(
        "gemini", "Google Gemini（OpenAI 兼容）",
        "https://generativelanguage.googleapis.com/v1beta/openai/",
        env_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        url_signatures=("generativelanguage.googleapis.com",),
        models=("gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"),
    ),
    ProviderPreset(
        "qwen", "通义千问 Qwen（DashScope 兼容模式）",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        env_keys=("DASHSCOPE_API_KEY",), url_signatures=("dashscope.aliyuncs.com",),
        models=("qwen-plus", "qwen-turbo", "qwen-max"),
    ),
    ProviderPreset(
        "zhipu", "智谱 GLM", "https://open.bigmodel.cn/api/paas/v4",
        env_keys=("ZHIPU_API_KEY", "ZHIPUAI_API_KEY"),
        url_signatures=("open.bigmodel.cn", "bigmodel.cn"),
        models=("glm-4-plus", "glm-4-flash", "glm-4-air"),
    ),
    ProviderPreset(
        "moonshot", "月之暗面 Kimi（Moonshot）", "https://api.moonshot.cn/v1",
        env_keys=("MOONSHOT_API_KEY",), url_signatures=("moonshot.cn",),
        models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"),
    ),
    ProviderPreset(
        "xai", "xAI Grok", "https://api.x.ai/v1",
        env_keys=("XAI_API_KEY",), url_signatures=("api.x.ai",),
        models=("grok-2-latest", "grok-2-vision-latest"),
    ),
    ProviderPreset(
        "minimax", "MiniMax", "https://api.minimaxi.com/v1",
        env_keys=("MINIMAX_API_KEY",), url_signatures=("minimaxi.com",),
        models=("MiniMax-Text-01", "abab6.5s-chat"),
    ),
    ProviderPreset(
        "mimo", "小米 MiMo", "https://api.xiaomimimo.com/v1",
        env_keys=("MIMO_API_KEY", "XIAOMIMIMO_API_KEY"),
        url_signatures=("xiaomimimo", "mimo.mi.com"),
    ),
    ProviderPreset(
        "siliconflow", "SiliconFlow 硅基流动", "https://api.siliconflow.cn/v1",
        env_keys=("SILICONFLOW_API_KEY",), url_signatures=("siliconflow.cn",),
        note="聚合多家模型，点「获取模型列表」或手填模型 ID。",
    ),
    ProviderPreset(
        "openrouter", "OpenRouter", "https://openrouter.ai/api/v1",
        env_keys=("OPENROUTER_API_KEY",), url_signatures=("openrouter.ai",),
        # OpenRouter 用这两个头标记调用来源，缺失不影响可用性。
        headers=(("HTTP-Referer", "https://github.com/suan-11/mea-pet-public"),
                 ("X-Title", "MeaPet")),
        note="聚合多家模型，模型 ID 形如 openai/gpt-4o，点「获取模型列表」查看。",
    ),
    ProviderPreset(
        "groq", "Groq", "https://api.groq.com/openai/v1",
        env_keys=("GROQ_API_KEY",), url_signatures=("api.groq.com",),
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
    ),
    ProviderPreset(
        "modelscope", "魔搭 ModelScope", "https://api-inference.modelscope.cn/v1",
        env_keys=("MODELSCOPE_API_KEY",), url_signatures=("modelscope.cn",),
        note="聚合多家模型，点「获取模型列表」或手填模型 ID。",
    ),
    ProviderPreset(
        "nvidia", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1",
        env_keys=("NVIDIA_API_KEY",), url_signatures=("integrate.api.nvidia.com",),
        note="模型 ID 形如 meta/llama-3.1-70b-instruct，点「获取模型列表」查看。",
    ),
    ProviderPreset(
        "ai302", "302.AI", "https://api.302.ai/v1",
        env_keys=("AI302_API_KEY",), url_signatures=("api.302.ai",),
        note="聚合多家模型，点「获取模型列表」或手填模型 ID。",
    ),
    ProviderPreset(
        "ppio", "PPIO 派欧云", "https://api.ppinfra.com/v3/openai",
        env_keys=("PPIO_API_KEY",), url_signatures=("ppinfra.com",),
        note="聚合多家模型，点「获取模型列表」或手填模型 ID。",
    ),
    ProviderPreset(
        "aihubmix", "AIHubMix", "https://aihubmix.com/v1",
        env_keys=("AIHUBMIX_API_KEY",), url_signatures=("aihubmix.com",),
        note="聚合多家模型，点「获取模型列表」或手填模型 ID。",
    ),
    ProviderPreset(
        "longcat", "LongCat", "https://api.longcat.chat/openai",
        env_keys=("LONGCAT_API_KEY",), url_signatures=("api.longcat.chat",),
    ),
    ProviderPreset(
        "ollama", "Ollama（本地）", "http://127.0.0.1:11434",
        protocol=PROTO_OLLAMA, requires_key=False,
        url_signatures=("11434",),
        note="本地运行的 Ollama，无需 API Key；模型 ID 即你 ollama pull 的名字。",
    ),
    ProviderPreset(
        "lmstudio", "LM Studio（本地）", "http://127.0.0.1:1234/v1",
        requires_key=False, url_signatures=("1234/v1",),
        note="本地运行的 LM Studio（OpenAI 兼容），无需 API Key；点「获取模型列表」。",
    ),
    ProviderPreset(
        "azure", "Azure OpenAI", "",
        env_keys=("AZURE_OPENAI_API_KEY",),
        note="填写你的 Azure 部署地址，形如 https://<资源名>.openai.azure.com/openai/deployments/<部署名>",
    ),
)

# 自定义/自动识别的占位项 id（下拉第一项，保留手动填地址的旧行为）。
CUSTOM_ID = ""

_BY_ID = {p.id: p for p in PROVIDER_PRESETS}


def all_presets() -> Tuple[ProviderPreset, ...]:
    return PROVIDER_PRESETS


def preset_by_id(preset_id: str) -> ProviderPreset | None:
    return _BY_ID.get(str(preset_id or "").strip())


def detect_preset_by_url(*parts: object) -> ProviderPreset | None:
    """按地址子串反查供应商预设；未命中返回 None。

    ollama（本地 11434）信号最弱，仅当没有其它更明确的匹配时才采纳，
    避免把本地反代到云端的地址误判成 ollama。
    """
    ollama_hit: ProviderPreset | None = None
    for part in parts:
        text = str(part or "").strip().lower()
        if not text:
            continue
        for preset in PROVIDER_PRESETS:
            for sig in preset.url_signatures:
                if sig and sig in text:
                    if preset.id == "ollama":
                        ollama_hit = ollama_hit or preset
                    else:
                        return preset
    return ollama_hit
