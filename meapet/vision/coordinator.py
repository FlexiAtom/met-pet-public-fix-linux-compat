"""Route a captured image or relay observation through the main backend."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping

from meapet.agent.base import (
    AgentTurnRequest,
    ImageAttachment,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.conversation.types import ReplySegment
from meapet.vision.observation import VisionObservation


SILENT_DISPLAY_TOKEN = "__MEAPET_SILENT__"

_WATCH_RULES = f"""这是一次用户已授权的桌面观察。
请决定是否值得主动说一句：
- 如果画面有明确活动，说具体看到的事，不要空泛提醒休息。
- 如果是锁屏、黑屏、空桌面或刚交互过且无新信息，返回一个合法分段，但 DISPLAY 必须精确为 {SILENT_DISPLAY_TOKEN}。
- 其余情况按桌宠前端分段协议返回；不要暴露推理或隐私文本。"""


@dataclass(frozen=True)
class VisionReply:
    segments: tuple[ReplySegment, ...]
    silent: bool = False


class VisionTurnError(RuntimeError):
    def __init__(self, category: str, safe_message: str, retryable: bool = False):
        super().__init__(safe_message)
        self.category = str(category)
        self.safe_message = str(safe_message)
        self.retryable = bool(retryable)


class VisionCoordinator:
    def __init__(self, reply_adapter) -> None:
        if reply_adapter is None or not hasattr(reply_adapter, "stream_turn"):
            raise ValueError("reply_adapter must support stream_turn")
        self.reply_adapter = reply_adapter

    async def inherit(
        self,
        attachment: ImageAttachment,
        *,
        idle_minutes: float,
        frontend_context: Mapping[str, object],
        tts_enabled: bool,
    ) -> VisionReply:
        prompt = (
            f"{_WATCH_RULES}\n\n"
            f"距离上次交互约 {max(0, int(idle_minutes))} 分钟。\n"
            "直接查看本轮附带的截图，一次完成理解与回复。"
        )
        return await self._run(
            prompt,
            attachments=(attachment,),
            frontend_context=frontend_context,
            tts_enabled=tts_enabled,
        )

    async def relay(
        self,
        observation: VisionObservation,
        *,
        idle_minutes: float,
        frontend_context: Mapping[str, object],
        tts_enabled: bool,
    ) -> VisionReply:
        if not isinstance(observation, VisionObservation):
            raise ValueError("relay requires a structured observation")
        prompt = (
            f"{_WATCH_RULES}\n\n"
            f"距离上次交互约 {max(0, int(idle_minutes))} 分钟。\n"
            "以下是独立视觉模型生成的有界观察，可能不完整；"
            "只根据观察回复，不要假装看到了未提及的细节。\n"
            f"观察 JSON：{observation.to_json()}"
        )
        return await self._run(
            prompt,
            attachments=(),
            frontend_context=frontend_context,
            tts_enabled=tts_enabled,
        )

    async def _run(
        self,
        prompt: str,
        *,
        attachments: tuple[ImageAttachment, ...],
        frontend_context: Mapping[str, object],
        tts_enabled: bool,
    ) -> VisionReply:
        turn_id = f"vision-{uuid.uuid4().hex}"
        request = AgentTurnRequest(
            turn_id=turn_id,
            user_text=prompt,
            frontend_context=frontend_context,
            tts_enabled=tts_enabled,
            attachments=attachments,
        )
        completed = None
        async for event in self.reply_adapter.stream_turn(request):
            if isinstance(event, TurnFailed):
                raise VisionTurnError(
                    event.category,
                    event.safe_message,
                    event.retryable,
                )
            if isinstance(event, TurnCancelled):
                raise VisionTurnError("cancelled", "本次识图已取消。")
            if isinstance(event, TurnCompleted):
                completed = event.result
        if completed is None:
            raise VisionTurnError("protocol", "模型没有完成本次识图回复。")
        segments = tuple(
            segment
            for segment in completed.segments
            if segment.display_text.strip() != SILENT_DISPLAY_TOKEN
        )
        if not segments:
            return VisionReply((), True)
        return VisionReply(segments, False)


__all__ = [
    "SILENT_DISPLAY_TOKEN",
    "VisionCoordinator",
    "VisionReply",
    "VisionTurnError",
]
