"""不依赖配置存储状态的轻量值规范化函数。"""

from __future__ import annotations


_GSV_REF_LANGUAGE_ALIASES = {
    "jp": "jp",
    "ja": "jp",
    "jpn": "jp",
    "japanese": "jp",
    "日文": "jp",
    "日语": "jp",
    "zh": "zh",
    "cn": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "chinese": "zh",
    "中文": "zh",
    "汉语": "zh",
    "en": "en",
    "eng": "en",
    "english": "en",
    "英文": "en",
    "英语": "en",
}


def normalize_gsv_ref_language(value: object) -> str:
    """把 GPT-SoVITS 参考音频语言规范为 ``jp`` / ``zh`` / ``en``。"""
    raw = str(value or "jp").strip().lower()
    return _GSV_REF_LANGUAGE_ALIASES.get(raw, "jp")


def normalize_input_device_index(value: object) -> "int | None":
    """把配置里的录音设备号规范为非负整数；非法值按「自动选路」处理。"""
    try:
        index = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return index if index >= 0 else None


def canonical_tts_language(value: object) -> str:
    """把 BCP-47 或常见别名压缩为 TTS 使用的主语言标签。"""
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    primary = raw.split("-", 1)[0]
    if primary in ("ja", "jp") or raw in ("jpn", "japanese", "日文", "日语"):
        return "jp"
    if primary in ("zh", "cn") or raw in ("chinese", "中文", "汉语"):
        return "zh"
    if primary == "en" or raw in ("eng", "english", "英文", "英语"):
        return "en"
    return primary


#: 一格里最多留这么多关键词：点一次抽一个，几十条已经够抽样散开，再多只是把配置面撑长。
FEEDBACK_KEYWORD_LIMIT = 64
#: 单个关键词的长度上限——超长的那条多半是把一整句话粘进来了，检索效果反而差。
FEEDBACK_KEYWORD_MAX_CHARS = 64
_FEEDBACK_KEYWORD_SEPARATORS = "，,、;；\n\r\t "


def normalize_feedback_keywords(value: object) -> list[str]:
    """把「关键词」那一格收敛成去重的非空字符串列表。

    允许列表，也允许一格里粘一串（中英文逗号、顿号、分号、空白都能分）——用户手抄关键词
    时最容易一次粘成一行。条数与长度都有上限，超出即截断，不做"看起来没收进去"的静默吞。
    """
    if value is None:
        return []
    raw_items = value if isinstance(value, (list, tuple, set)) else [value]
    words: list[str] = []
    for item in raw_items:
        piece = str(item or "").strip()
        if not piece:
            continue
        for chunk in piece.translate(
            {ord(ch): "\n" for ch in _FEEDBACK_KEYWORD_SEPARATORS}
        ).split("\n"):
            word = chunk.strip()
            if not word:
                continue
            word = word[:FEEDBACK_KEYWORD_MAX_CHARS]
            if word not in words:
                words.append(word)
    return words[:FEEDBACK_KEYWORD_LIMIT]


def normalize_click_feedback_weight(value: object, *, default: int = 0) -> int:
    """抽样权重：0–100 的整数，非法值收敛到 ``default``。存的原始值，抽样时才归一。"""
    try:
        weight = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return max(0, min(100, int(default)))
    return max(0, min(100, weight))


def normalize_poetry_sources(value: object, known: tuple) -> list[str]:
    """古诗词的源勾选：只认词汇表里的名字，去重保序；没给（``None``）按默认两源。

    两档全取消勾选是**保留的**：那是用户在菜单里做的一次选择，这里退回默认等于当场推翻它。
    源全空时这一路抽不中，理由由 ``feedback.participation_gap`` 那行灰字讲给人听，
    不靠归一化替用户改主意。
    """
    if value is None:
        return [str(name) for name in known]
    items = value if isinstance(value, (list, tuple, set)) else [value]
    chosen: list[str] = []
    for item in items:
        name = str(item or "").strip().lower()
        if name in known and name not in chosen:
            chosen.append(name)
    return chosen
