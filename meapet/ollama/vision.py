"""Ollama 识图策略：优先使用 relay 模式（独立视觉模型描述 + 文本传给 Ollama）。"""

from __future__ import annotations

import sys  # 新增
from typing import Mapping

from meapet.agent.base import ImageAttachment
from meapet.vision.observation import VisionObservation


def should_use_relay_for_ollama(vision_cfg: Mapping[str, object], llm_cfg: Mapping[str, object]) -> bool:
    """判断 Ollama 是否应该使用 relay 模式（独立视觉模型→文本→Ollama）。
    
    当 vision.mode 为 'relay' 或 vision.main_model_supports_images 为 False 时返回 True。
    """
    print(f"[DEBUG ollama_vision] should_use_relay_for_ollama called: vision_cfg keys={list(vision_cfg.keys())}, llm_cfg keys={list(llm_cfg.keys())}", file=sys.stderr, flush=True)
    mode = str(vision_cfg.get("mode", "disabled")).strip().lower()
    print(f"[DEBUG ollama_vision] should_use_relay_for_ollama: mode={mode!r}", file=sys.stderr, flush=True)
    if mode == "relay":
        print(f"[DEBUG ollama_vision] should_use_relay_for_ollama: mode is relay, returning True", file=sys.stderr, flush=True)
        return True
    # 如果 Ollama 模型不支持图像（非 VL 模型），强制 relay
    from meapet.vision.policy import main_model_supports_vision
    supports = main_model_supports_vision(vision_cfg, llm_cfg)
    print(f"[DEBUG ollama_vision] should_use_relay_for_ollama: main_model_supports_vision={supports}", file=sys.stderr, flush=True)
    if not supports:
        print(f"[DEBUG ollama_vision] should_use_relay_for_ollama: model does not support vision, returning True", file=sys.stderr, flush=True)
        return True
    print(f"[DEBUG ollama_vision] should_use_relay_for_ollama: model supports vision, returning False", file=sys.stderr, flush=True)
    return False


def build_ollama_vision_prompt(
    observation: VisionObservation,
    idle_minutes: float,
) -> str:
    """构建 Ollama 识图提示（基于 relay 观察结果）。"""
    print(f"[DEBUG ollama_vision] build_ollama_vision_prompt called: observation type={type(observation).__name__}, summary_len={len(observation.summary) if hasattr(observation, 'summary') else 'N/A'}, idle_minutes={idle_minutes}", file=sys.stderr, flush=True)
    result = (
        f"这是独立视觉模型生成的桌面观察摘要。请根据以下信息决定是否主动说一句。\n"
        f"距离上次交互约 {max(0, int(idle_minutes))} 分钟。\n"
        f"观察 JSON：{observation.to_json()}\n\n"
        f"规则：\n"
        f"- 如果画面有明确活动，说具体看到的事。\n"
        f"- 如果是锁屏、黑屏、空桌面或刚交互过且无新信息，回复 '__MEAPET_SILENT__'。\n"
        f"- 其余情况按普通对话回复。\n"
        f"- 只输出一行中文对白，可带 [情绪] 标签。"
    )
    print(f"[DEBUG ollama_vision] build_ollama_vision_prompt returning: prompt_len={len(result)}", file=sys.stderr, flush=True)
    return result


async def handle_ollama_vision_attachment(
    attachment: ImageAttachment,
    *,
    idle_minutes: float,
    frontend_context: Mapping[str, object],
    tts_enabled: bool,
    reply_adapter,
) -> str:
    """处理 Ollama 直接识图（inherit 模式）：构建简单提示，避免复杂分段协议。"""
    print(f"[DEBUG ollama_vision] handle_ollama_vision_attachment called: attachment media_type={attachment.media_type}, data_len={len(attachment.data)}, idle_minutes={idle_minutes}, frontend_context keys={list(frontend_context.keys()) if frontend_context else []}, tts_enabled={tts_enabled}, reply_adapter type={type(reply_adapter).__name__}", file=sys.stderr, flush=True)
    prompt = (
        f"这是一张桌面截图。请直接描述你看到了什么，并决定是否主动说一句。\n"
        f"距离上次交互约 {max(0, int(idle_minutes))} 分钟。\n"
        f"规则同上（__MEAPET_SILENT__ 表示沉默）。"
    )
    print(f"[DEBUG ollama_vision] handle_ollama_vision_attachment: built prompt length={len(prompt)}", file=sys.stderr, flush=True)
    # 复用现有的 VisionCoordinator，但使用 Ollama 专用的 prompt
    from meapet.vision.coordinator import VisionCoordinator, SILENT_DISPLAY_TOKEN
    coord = VisionCoordinator(reply_adapter)
    print(f"[DEBUG ollama_vision] handle_ollama_vision_attachment: created VisionCoordinator, calling coord._run...", file=sys.stderr, flush=True)
    reply = await coord._run(
        prompt,
        attachments=(attachment,),
        frontend_context=frontend_context,
        tts_enabled=tts_enabled,
    )
    print(f"[DEBUG ollama_vision] handle_ollama_vision_attachment: coord._run returned: silent={reply.silent}, segments_count={len(reply.segments)}", file=sys.stderr, flush=True)
    if reply.silent:
        print(f"[DEBUG ollama_vision] handle_ollama_vision_attachment: silent, returning SILENT_DISPLAY_TOKEN", file=sys.stderr, flush=True)
        return SILENT_DISPLAY_TOKEN
    result = "\n".join(seg.display_text for seg in reply.segments)
    print(f"[DEBUG ollama_vision] handle_ollama_vision_attachment: returning display_text length={len(result)}", file=sys.stderr, flush=True)
    return result

