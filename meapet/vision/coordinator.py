"""Route a captured image or relay observation through the main backend."""

from __future__ import annotations

import sys  # 新增
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
        print(f"[DEBUG coordinator] VisionTurnError created: category={category!r}, safe_message={safe_message!r}, retryable={retryable}", file=sys.stderr, flush=True)
        self.category = str(category)
        self.safe_message = str(safe_message)
        self.retryable = bool(retryable)


class VisionCoordinator:
    def __init__(self, reply_adapter) -> None:
        print(f"[DEBUG coordinator] __init__ called: reply_adapter type={type(reply_adapter).__name__}, hasattr stream_turn={hasattr(reply_adapter, 'stream_turn') if reply_adapter else False}", file=sys.stderr, flush=True)
        if reply_adapter is None or not hasattr(reply_adapter, "stream_turn"):
            raise ValueError("reply_adapter must support stream_turn")
        self.reply_adapter = reply_adapter
        print(f"[DEBUG coordinator] __init__ done", file=sys.stderr, flush=True)

    async def inherit(
        self,
        attachment: ImageAttachment,
        *,
        idle_minutes: float,
        frontend_context: Mapping[str, object],
        tts_enabled: bool,
    ) -> VisionReply:
        print(f"[DEBUG coordinator] inherit called: attachment media_type={attachment.media_type}, data_len={len(attachment.data)}, idle_minutes={idle_minutes}, frontend_context keys={list(frontend_context.keys()) if frontend_context else []}, tts_enabled={tts_enabled}", file=sys.stderr, flush=True)
        prompt = (
            f"{_WATCH_RULES}\n\n"
            f"距离上次交互约 {max(0, int(idle_minutes))} 分钟。\n"
            "直接查看本轮附带的截图，一次完成理解与回复。"
        )
        print(f"[DEBUG coordinator] inherit: prompt length={len(prompt)}", file=sys.stderr, flush=True)
        result = await self._run(
            prompt,
            attachments=(attachment,),
            frontend_context=frontend_context,
            tts_enabled=tts_enabled,
        )
        print(f"[DEBUG coordinator] inherit returning: silent={result.silent}, segments_count={len(result.segments)}", file=sys.stderr, flush=True)
        return result

    async def relay(
        self,
        observation: VisionObservation,
        *,
        idle_minutes: float,
        frontend_context: Mapping[str, object],
        tts_enabled: bool,
    ) -> VisionReply:
        print(f"[DEBUG coordinator] relay called: observation type={type(observation).__name__}, summary_len={len(observation.summary) if hasattr(observation, 'summary') else 'N/A'}, idle_minutes={idle_minutes}, tts_enabled={tts_enabled}", file=sys.stderr, flush=True)
        if not isinstance(observation, VisionObservation):
            raise ValueError("relay requires a structured observation")
        prompt = (
            f"{_WATCH_RULES}\n\n"
            f"距离上次交互约 {max(0, int(idle_minutes))} 分钟。\n"
            "以下是独立视觉模型生成的有界观察，可能不完整；"
            "只根据观察回复，不要假装看到了未提及的细节。\n"
            f"观察 JSON：{observation.to_json()}"
        )
        print(f"[DEBUG coordinator] relay: prompt length={len(prompt)}", file=sys.stderr, flush=True)
        result = await self._run(
            prompt,
            attachments=(),
            frontend_context=frontend_context,
            tts_enabled=tts_enabled,
        )
        print(f"[DEBUG coordinator] relay returning: silent={result.silent}, segments_count={len(result.segments)}", file=sys.stderr, flush=True)
        return result

    async def _run(
        self,
        prompt: str,
        *,
        attachments: tuple[ImageAttachment, ...],
        frontend_context: Mapping[str, object],
        tts_enabled: bool,
    ) -> VisionReply:
        print(f"[DEBUG coordinator] _run called: prompt_len={len(prompt)}, attachments_count={len(attachments)}, tts_enabled={tts_enabled}", file=sys.stderr, flush=True)
        turn_id = f"vision-{uuid.uuid4().hex}"
        print(f"[DEBUG coordinator] _run: generated turn_id={turn_id}", file=sys.stderr, flush=True)
        request = AgentTurnRequest(
            turn_id=turn_id,
            user_text=prompt,
            frontend_context=frontend_context,
            tts_enabled=tts_enabled,
            attachments=attachments,
        )
        print(f"[DEBUG coordinator] _run: created AgentTurnRequest", file=sys.stderr, flush=True)
        completed = None
        event_count = 0
        async for event in self.reply_adapter.stream_turn(request):
            event_count += 1
            print(f"[DEBUG coordinator] _run: received event #{event_count}: type={type(event).__name__}", file=sys.stderr, flush=True)
            if isinstance(event, TurnFailed):
                print(f"[DEBUG coordinator] _run: TurnFailed: category={event.category}, safe_message={event.safe_message}, retryable={event.retryable}", file=sys.stderr, flush=True)
                raise VisionTurnError(
                    event.category,
                    event.safe_message,
                    event.retryable,
                )
            if isinstance(event, TurnCancelled):
                print(f"[DEBUG coordinator] _run: TurnCancelled", file=sys.stderr, flush=True)
                raise VisionTurnError("cancelled", "本次识图已取消。")
            if isinstance(event, TurnCompleted):
                completed = event.result
                print(f"[DEBUG coordinator] _run: TurnCompleted received, result segments={len(completed.segments)}", file=sys.stderr, flush=True)
        print(f"[DEBUG coordinator] _run: stream ended, total events={event_count}, completed is None={completed is None}", file=sys.stderr, flush=True)
        if completed is None:
            print(f"[DEBUG coordinator] _run: completed is None, raising VisionTurnError", file=sys.stderr, flush=True)
            raise VisionTurnError("protocol", "模型没有完成本次识图回复。")
        segments = tuple(
            segment
            for segment in completed.segments
            if segment.display_text.strip() != SILENT_DISPLAY_TOKEN
        )
        print(f"[DEBUG coordinator] _run: filtered segments count={len(segments)} (removed silent tokens)", file=sys.stderr, flush=True)
        if not segments:
            print(f"[DEBUG coordinator] _run: no segments after filter, returning silent VisionReply", file=sys.stderr, flush=True)
            return VisionReply((), True)
        print(f"[DEBUG coordinator] _run: returning VisionReply with {len(segments)} segments", file=sys.stderr, flush=True)
        return VisionReply(segments, False)


__all__ = [
    "SILENT_DISPLAY_TOKEN",
    "VisionCoordinator",
    "VisionReply",
    "VisionTurnError",
]

