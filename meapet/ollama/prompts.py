"""Ollama 本地模型专用的提示词模板（精简、无复杂 TTS 元数据）。"""

from __future__ import annotations

from typing import Mapping, Sequence

# 精简角色设定：去掉日语行、TTS JSON 等复杂要求
OLLAMA_PERSONA_PROMPT = """你是梅尔，《霞流宝石心》游戏中的猫娘天才。茶发褐瞳144cm，面无表情。
性格：毒舌冷淡、学术狂热、嘴硬心软。
说话：句尾加「喵」；极简20-40字；解释≤80字；害羞时转移话题；开心偶尔「嘿嘿」。
知识：全科全能。信条「知道越多越不可怕」。
对主人：亲密但毒舌，称「主人」。"""

# 输出格式：只要求中文对白，情绪标签可选，无需日语和 TTS 元数据
OLLAMA_OUTPUT_FORMAT = """输出格式：
- 只输出一行中文对白。
- 行首可带 [情绪] 标签，如 [annoyed]、[happy]。
- 禁感叹号/卖萌/长篇大论；问啥答啥。
- 不要输出日语、TTS 元数据、Markdown 代码块。"""

OLLAMA_SYSTEM_PROMPT = f"{OLLAMA_PERSONA_PROMPT}\n{OLLAMA_OUTPUT_FORMAT}"


def build_ollama_system_prompt(
    frontend_context: Mapping[str, object] | None = None,
    memory_context: str = "",
) -> str:
    """构建 Ollama 使用的完整系统提示。"""
    parts = [OLLAMA_SYSTEM_PROMPT]
    if memory_context:
        parts.append(f"\n记忆上下文：\n{memory_context}")
    if frontend_context:
        import json
        parts.append(
            f"\n前端只读摘要：{json.dumps(frontend_context, ensure_ascii=False, separators=(',',':'))}"
        )
    return "\n\n".join(parts)


def build_ollama_messages(
    user_text: str,
    history: Sequence[Mapping[str, str]],
    *,
    frontend_context: Mapping[str, object] | None = None,
    memory_context: str = "",
    attachments: tuple = (),
) -> list[dict[str, object]]:
    """构建 Ollama 的消息列表（不使用 OUTPUT_INSTRUCTION 和复杂分段）。"""
    system = build_ollama_system_prompt(frontend_context, memory_context)
    messages = [{"role": "system", "content": system}]
    # 添加上下文历史（最多 6 轮）
    for item in (list(history) if history else [])[-12:]:
        role = str(item.get("role", "")).strip().lower()
        if role in ("user", "assistant"):
            messages.append({"role": role, "content": str(item.get("content", ""))})
    # 当前用户消息（含图片）
    if attachments:
        content_parts = [{"type": "text", "text": user_text}]
        for att in attachments:
            content_parts.append({
                "type": "image",
                "media_type": att.media_type,
                "data": att.data,
            })
        messages.append({"role": "user", "content": content_parts})
    else:
        messages.append({"role": "user", "content": user_text})
    return messages

