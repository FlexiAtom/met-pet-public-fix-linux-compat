"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import shutil
import sys  # 新增
import threading
import uuid

from PyQt5.QtCore import QTimer

from meapet.utils import debug_enabled, log_error, redact_text
from meapet.agent.base import (
    AgentTurnRequest,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from meapet.agent.presentation import (
    AgentTurnPresentation,
    BeginBubble,
    CancelTurn,
    FailTurn,
    FinalizeBubble,
    FinishTurn,
    PlayAudio,
    RequestFormatRepair,
    ShowStatus,
    SubmitTTS,
    UpdateBubble,
)
from meapet.chat.engine import SYSTEM_PROMPT
from meapet.conversation.capabilities import build_agent_frontend_context
from meapet.conversation.output_protocol import (
    SegmentCompleted,
    SegmentStarted,
    SegmentTextDelta,
)
from meapet.conversation.timeline import ConversationKey
from meapet.conversation.types import (
    CompanionState,
    FrontendCapabilities,
    ReplySegment,
    normalize_voice_language,
)
from meapet.desktop import status_language
from meapet.desktop.workers import AgentChatWorker, ChatWorker, TTSWorker
from meapet.desktop.chat_input import ChatInputBox, set_awaiting_reply_state
from meapet.log import get_color_logger

log = get_color_logger("chat_flow")

# 串行队列：确保记忆操作（摘要、提取等）不会并发执行
_memory_op_lock = threading.Lock()


def _log_private_text(label: str, text: str, *, suffix: str = "") -> None:
    """默认仅记录文本长度；显式调试时才记录正文。"""
    value = str(text or "")
    tail = f" {suffix}" if suffix else ""
    if debug_enabled():
        log.debug(f"{label}: chars={len(value)}{tail}\n{value}")
    else:
        log.debug(f"{label}: chars={len(value)}{tail}")


class PetChatFlowMixin:
    def _refresh_conversation_key(self) -> ConversationKey:
        print(f"[DEBUG chat_flow] _refresh_conversation_key called", file=sys.stderr, flush=True)
        from meapet.conversation.orchestrator import ConversationOrchestrator

        llm = (getattr(self, "config", {}) or {}).get("llm") or {}
        mode = str(llm.get("mode") or "direct").strip().lower()
        print(f"[DEBUG chat_flow] _refresh_conversation_key: llm mode={mode!r}", file=sys.stderr, flush=True)
        if mode == "agent":
            agent = llm.get("agent") or {}
            key = ConversationKey(
                "agent",
                str(agent.get("kind") or "hermes"),
                str(agent.get("session_id") or "pending"),
            )
            print(f"[DEBUG chat_flow] _refresh_conversation_key: agent key kind={key.kind}, session={key.session_id!r}", file=sys.stderr, flush=True)
        else:
            direct = llm.get("direct") or {}
            key = ConversationKey(
                "direct",
                str(direct.get("provider") or llm.get("backend") or "ollama"),
                "local",
            )
            print(f"[DEBUG chat_flow] _refresh_conversation_key: direct key id={key.id}", file=sys.stderr, flush=True)
        self._conversation_key = key
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is None:
            orchestrator = ConversationOrchestrator(key)
            self._conversation_orchestrator = orchestrator
            print(f"[DEBUG chat_flow] _refresh_conversation_key: created new orchestrator", file=sys.stderr, flush=True)
        else:
            orchestrator.activate(key)
            print(f"[DEBUG chat_flow] _refresh_conversation_key: activated existing orchestrator", file=sys.stderr, flush=True)
        print(f"[DEBUG chat_flow] _refresh_conversation_key returning: key={key}", file=sys.stderr, flush=True)
        return key

    def _turn_context_is_current(self, context=None) -> bool:
        print(f"[DEBUG chat_flow] _turn_context_is_current called: context={context is not None}", file=sys.stderr, flush=True)
        if context is None:
            context = getattr(self, "_active_turn_context", None)
            print(f"[DEBUG chat_flow] _turn_context_is_current: fell back to _active_turn_context", file=sys.stderr, flush=True)
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is None or context is None:
            result = True
            print(f"[DEBUG chat_flow] _turn_context_is_current: no orchestrator or context, returning {result}", file=sys.stderr, flush=True)
            return result
        result = orchestrator.accepts(context)
        print(f"[DEBUG chat_flow] _turn_context_is_current: orchestrator.accepts={result}", file=sys.stderr, flush=True)
        return result

    def _complete_turn_context(self, context=None) -> None:
        print(f"[DEBUG chat_flow] _complete_turn_context called: context={context is not None}", file=sys.stderr, flush=True)
        if context is None:
            context = getattr(self, "_active_turn_context", None)
            print(f"[DEBUG chat_flow] _complete_turn_context: fell back to _active_turn_context", file=sys.stderr, flush=True)
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is not None and context is not None:
            orchestrator.complete(context)
            print(f"[DEBUG chat_flow] _complete_turn_context: completed context", file=sys.stderr, flush=True)
        if getattr(self, "_active_turn_context", None) is context:
            self._active_turn_context = None
            print(f"[DEBUG chat_flow] _complete_turn_context: cleared _active_turn_context", file=sys.stderr, flush=True)

    def _invalidate_active_conversation(self) -> None:
        print(f"[DEBUG chat_flow] _invalidate_active_conversation called", file=sys.stderr, flush=True)
        context = getattr(self, "_active_turn_context", None)
        timeline = getattr(self, "_conversation_timeline", None)
        if context is not None and timeline is not None:
            timeline.cancel_turn(
                context.conversation_key,
                context.turn_id,
            )
            print(f"[DEBUG chat_flow] _invalidate_active_conversation: cancelled turn in timeline", file=sys.stderr, flush=True)
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is not None:
            orchestrator.invalidate()
            print(f"[DEBUG chat_flow] _invalidate_active_conversation: invalidated orchestrator", file=sys.stderr, flush=True)
        self._active_turn_context = None
        self._active_agent_turn_id = ""
        self._active_timeline_turn_id = ""
        self._agent_tts_workers = {}
        self._pending_chat_reply = None
        self._pending_chat_context = None
        set_awaiting_reply_state(self, False)
        print(f"[DEBUG chat_flow] _invalidate_active_conversation: state reset done", file=sys.stderr, flush=True)

    def _timeline_start_turn(
        self,
        turn_id: str,
        *,
        source: str,
        user_text: str = "",
        context=None,
    ) -> None:
        print(f"[DEBUG chat_flow] _timeline_start_turn called: turn_id={turn_id[:24]!r}, source={source!r}, user_text_len={len(user_text)}", file=sys.stderr, flush=True)
        timeline = getattr(self, "_conversation_timeline", None)
        if timeline is None:
            print(f"[DEBUG chat_flow] _timeline_start_turn: no timeline, returning", file=sys.stderr, flush=True)
            return
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        if key is None:
            key = self._refresh_conversation_key()
            print(f"[DEBUG chat_flow] _timeline_start_turn: refreshed key", file=sys.stderr, flush=True)
        timeline.start_turn(
            key,
            turn_id,
            source=source,
            user_text=user_text,
        )
        print(f"[DEBUG chat_flow] _timeline_start_turn: started turn in timeline", file=sys.stderr, flush=True)

    def _record_agent_timeline_event(self, event: object, context=None) -> None:
        print(f"[DEBUG chat_flow] _record_agent_timeline_event called: event type={type(event).__name__}", file=sys.stderr, flush=True)
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_agent_turn_id", "")
            or ""
        )
        if timeline is None or key is None or not turn_id:
            print(f"[DEBUG chat_flow] _record_agent_timeline_event: missing timeline/key/turn_id, returning", file=sys.stderr, flush=True)
            return
        if isinstance(event, SegmentStarted):
            texts = getattr(self, "_timeline_segment_texts", None)
            if texts is None:
                texts = {}
                self._timeline_segment_texts = texts
            texts.setdefault(event.index, "")
            print(f"[DEBUG chat_flow] _record_agent_timeline_event: SegmentStarted index={event.index}", file=sys.stderr, flush=True)
        elif isinstance(event, SegmentTextDelta):
            texts = getattr(self, "_timeline_segment_texts", None)
            if texts is None:
                texts = {}
                self._timeline_segment_texts = texts
            texts[event.index] = texts.get(event.index, "") + event.delta
            timeline.update_segment_text(key, turn_id, event.index, texts[event.index])
            print(f"[DEBUG chat_flow] _record_agent_timeline_event: SegmentTextDelta index={event.index}, delta_len={len(event.delta)}", file=sys.stderr, flush=True)
        elif isinstance(event, SegmentCompleted):
            timeline.complete_segment(key, turn_id, event.segment)
            print(f"[DEBUG chat_flow] _record_agent_timeline_event: SegmentCompleted index={event.segment.index}", file=sys.stderr, flush=True)
        elif isinstance(event, ToolStatus):
            safe_text = str(event.safe_text or "").strip() or {
                "started": "正在处理",
                "running": "仍在处理",
                "succeeded": "处理完成",
                "failed": "处理失败",
            }.get(str(event.state or "").lower(), "状态已更新")
            timeline.add_status(
                key,
                turn_id,
                state=event.state,
                safe_text=safe_text,
            )
            print(f"[DEBUG chat_flow] _record_agent_timeline_event: ToolStatus state={event.state}", file=sys.stderr, flush=True)
        elif isinstance(event, TurnFailed):
            timeline.fail_turn(key, turn_id, event.safe_message)
            print(f"[DEBUG chat_flow] _record_agent_timeline_event: TurnFailed safe_message_len={len(event.safe_message)}", file=sys.stderr, flush=True)
        elif isinstance(event, TurnCancelled):
            timeline.cancel_turn(key, turn_id)
            print(f"[DEBUG chat_flow] _record_agent_timeline_event: TurnCancelled", file=sys.stderr, flush=True)

    def _bind_bubble_to_timeline(self, bubble, turn_id: str) -> None:
        print(f"[DEBUG chat_flow] _bind_bubble_to_timeline called: bubble type={type(bubble).__name__}, turn_id={turn_id[:24]!r}", file=sys.stderr, flush=True)
        signal = getattr(bubble, "activated", None)
        opener = getattr(self, "_show_timeline_turn", None)
        if signal is None or not callable(opener) or not turn_id:
            print(f"[DEBUG chat_flow] _bind_bubble_to_timeline: missing signal/opener/turn_id, returning", file=sys.stderr, flush=True)
            return
        try:
            signal.connect(lambda current=turn_id: opener(current))
            print(f"[DEBUG chat_flow] _bind_bubble_to_timeline: connected signal", file=sys.stderr, flush=True)
        except (AttributeError, RuntimeError, TypeError) as exc:
            print(f"[DEBUG chat_flow] _bind_bubble_to_timeline: connect failed: {type(exc).__name__}", file=sys.stderr, flush=True)

    def _start_chat(self):
        print(f"[DEBUG chat_flow] _start_chat called", file=sys.stderr, flush=True)
        log.info("[chat] 启动对话编辑器")
        clear_bubbles = getattr(self, "_clear_bubbles", None)
        if callable(clear_bubbles):
            clear_bubbles()
            print(f"[DEBUG chat_flow] _start_chat: cleared bubbles via callback", file=sys.stderr, flush=True)
        else:
            bubble = getattr(self, "bubble", None)
            if bubble is not None:
                try:
                    bubble.hide()
                    print(f"[DEBUG chat_flow] _start_chat: hid bubble", file=sys.stderr, flush=True)
                except RuntimeError:
                    print(f"[DEBUG chat_flow] _start_chat: hide bubble RuntimeError", file=sys.stderr, flush=True)
                    pass

        self._chat_input = ChatInputBox(None)
        if getattr(self, "_awaiting_reply", False):
            self._chat_input.set_busy(True, status_language.thinking_busy())
            print(f"[DEBUG chat_flow] _start_chat: set chat_input busy", file=sys.stderr, flush=True)

        input_x = self.pos().x() + (self.width() - self._chat_input.width()) // 2
        input_y = self.pos().y() - self._chat_input.height() - 20
        if input_y < 30:
            input_y = self.pos().y() + self.height() + 20
        self._chat_input.move(max(0, input_x), max(0, input_y))
        self._chat_input.text_submitted.connect(self._on_input_submit)
        self._chat_input.show()
        print(f"[DEBUG chat_flow] _start_chat: shown chat input at ({input_x},{input_y})", file=sys.stderr, flush=True)

    def _on_input_submit(self, text: str):
        print(f"[DEBUG chat_flow] _on_input_submit called: text_len={len(text)}", file=sys.stderr, flush=True)
        if getattr(self, "_awaiting_reply", False):
            log.warning("[chat] 对话被拒绝：正在等待回复中")
            self._show_bubble(status_language.thinking_busy(), 2500)
            self._position_bubble()
            print(f"[DEBUG chat_flow] _on_input_submit: rejected due to awaiting reply", file=sys.stderr, flush=True)
            return
        self._record_interaction()
        _log_private_text("[input] 收到用户输入", text)
        log.info("[input] 提交消息，准备回复")
        self._show_bubble("……？", 1500)
        self._position_bubble()
        QTimer.singleShot(1200, lambda: self._do_chat(text))
        print(f"[DEBUG chat_flow] _on_input_submit: scheduled _do_chat in 1200ms", file=sys.stderr, flush=True)

    def _is_agent_mode(self) -> bool:
        llm = (getattr(self, "config", {}) or {}).get("llm") or {}
        result = str(llm.get("mode") or "direct").strip().lower() == "agent"
        print(f"[DEBUG chat_flow] _is_agent_mode returning: {result}", file=sys.stderr, flush=True)
        return result

    def _build_agent_frontend_context(self) -> dict:
        print(f"[DEBUG chat_flow] _build_agent_frontend_context called", file=sys.stderr, flush=True)
        from meapet.desktop.renderer import MOOD_TO_EXPRESSION

        tts = getattr(self, "tts", None)
        tts_enabled = bool(tts is not None and getattr(tts, "enabled", False))
        configured_tts = (getattr(self, "config", {}) or {}).get("tts") or {}
        languages = ()
        if tts is not None and hasattr(tts, "supported_languages"):
            try:
                languages = tuple(tts.supported_languages())
                print(f"[DEBUG chat_flow] _build_agent_frontend_context: got {len(languages)} languages from tts", file=sys.stderr, flush=True)
            except Exception as exc:
                log.warning(f"[agent] 读取 TTS 语言能力失败: {type(exc).__name__}")
        if not languages:
            voice_language = normalize_voice_language(
                getattr(tts, "voice_lang", "")
                or configured_tts.get("voice_lang")
                or ""
            )
            languages = (voice_language,) if voice_language else ()
            print(f"[DEBUG chat_flow] _build_agent_frontend_context: fell back to single language {voice_language!r}", file=sys.stderr, flush=True)

        affection_level = ""
        memory = getattr(self, "memory", None)
        if memory is not None and hasattr(memory, "get_affection_tier"):
            try:
                tier = memory.get_affection_tier()
                if isinstance(tier, (tuple, list)) and len(tier) > 1:
                    affection_level = str(tier[1] or "")
                    print(f"[DEBUG chat_flow] _build_agent_frontend_context: affection_level={affection_level!r}", file=sys.stderr, flush=True)
            except Exception as exc:
                log.warning(f"[agent] 读取好感度摘要失败: {type(exc).__name__}")

        renderer = getattr(self, "renderer", None)
        current_mood = getattr(renderer, "_current_mood", "neutral")
        capabilities = FrontendCapabilities(
            renderer="live2d" if getattr(self, "_use_live2d", False) else "png",
            supported_moods=tuple(MOOD_TO_EXPRESSION),
            supported_motions=(),
            tts_enabled=tts_enabled,
            tts_languages=languages,
            streaming_text=True,
            multi_segment=True,
        )
        state = CompanionState(
            affection_level=affection_level,
            character_state=(
                "standby" if getattr(self, "_standby", False) else "active"
            ),
            current_mood=current_mood,
            busy=bool(getattr(self, "_awaiting_reply", False)),
        )
        result = build_agent_frontend_context(capabilities, state)
        print(f"[DEBUG chat_flow] _build_agent_frontend_context returning: keys={list(result.keys())}", file=sys.stderr, flush=True)
        return result

    def _make_chat_worker(self, message: str):
        print(f"[DEBUG chat_flow] _make_chat_worker called: message_len={len(message)}", file=sys.stderr, flush=True)
        if getattr(self, "_conversation_key", None) is None:
            self._refresh_conversation_key()
            print(f"[DEBUG chat_flow] _make_chat_worker: refreshed conversation key", file=sys.stderr, flush=True)
        agent_mode = self._is_agent_mode()
        print(f"[DEBUG chat_flow] _make_chat_worker: agent_mode={agent_mode}", file=sys.stderr, flush=True)
        if agent_mode:
            adapter = getattr(self, "agent_adapter", None)
            if adapter is None:
                raise RuntimeError("Agent 后端尚未初始化")
            history = tuple(getattr(self, "_agent_history", ()) or ())
            print(f"[DEBUG chat_flow] _make_chat_worker: agent mode, history_len={len(history)}", file=sys.stderr, flush=True)
        else:
            adapter = getattr(self, "chat_engine", None)
            if adapter is None or not callable(getattr(adapter, "stream_turn", None)):
                raise RuntimeError("直连模型后端尚未初始化")
            history = ()
            print(f"[DEBUG chat_flow] _make_chat_worker: direct mode", file=sys.stderr, flush=True)

        turn_id = f"meapet-{uuid.uuid4().hex}"
        orchestrator = getattr(self, "_conversation_orchestrator", None)
        if orchestrator is None:
            self._refresh_conversation_key()
            orchestrator = self._conversation_orchestrator
            print(f"[DEBUG chat_flow] _make_chat_worker: created orchestrator", file=sys.stderr, flush=True)
        turn_context = orchestrator.begin_turn(turn_id)
        self._active_turn_context = turn_context
        self._active_timeline_turn_id = turn_id
        tts = getattr(self, "tts", None)
        tts_enabled = bool(tts is not None and getattr(tts, "enabled", False))
        bubble_config = (getattr(self, "config", {}) or {}).get(
            "bubble_duration_ms"
        ) or {}
        self._active_agent_turn_id = turn_id
        self._agent_turn_result = None
        self._agent_bubbles = {}
        self._agent_tts_workers = {}
        self._timeline_segment_texts = {}
        from meapet.desktop.renderer import MOOD_TO_EXPRESSION

        self._agent_presentation = AgentTurnPresentation(
            tts_enabled=tts_enabled,
            reply_min_duration_ms=int(bubble_config.get("reply", 3000)),
            supported_moods=tuple(MOOD_TO_EXPRESSION),
        )
        request = AgentTurnRequest(
            turn_id=turn_id,
            user_text=message,
            history=history,
            frontend_context=self._build_agent_frontend_context(),
            tts_enabled=tts_enabled,
            conversation_key=turn_context.conversation_key,
            generation_id=turn_context.generation_id,
        )
        self._timeline_start_turn(
            turn_id,
            source="user_reply",
            user_text=message,
            context=turn_context,
        )
        worker = AgentChatWorker(adapter, request)
        worker.turn_context = turn_context
        print(f"[DEBUG chat_flow] _make_chat_worker: created worker, turn_id={turn_id[:24]!r}", file=sys.stderr, flush=True)
        return worker

    def _do_chat(self, message: str):
        print(f"[DEBUG chat_flow] _do_chat called: message_len={len(message)}", file=sys.stderr, flush=True)
        if self._awaiting_reply:
            log.warning("[chat] 对话被拒绝：正在等待回复中")
            self._show_bubble(status_language.thinking_busy(), 2500)
            self._position_bubble()
            print(f"[DEBUG chat_flow] _do_chat: rejected due to awaiting reply", file=sys.stderr, flush=True)
            return
        interrupt_control = getattr(self, "_interrupt_control_say", None)
        if callable(interrupt_control):
            interrupt_control()
            print(f"[DEBUG chat_flow] _do_chat: interrupted control say", file=sys.stderr, flush=True)
        set_awaiting_reply_state(
            self,
            True,
            status_language.thinking_busy(),
        )
        self._safe_set_mood("talking")
        self._last_user_msg = message
        _log_private_text("[chat] 发送给 LLM", message)

        self._show_bubble(
            status_language.thinking(),
            self.config["bubble_duration_ms"]["thinking"],
        )
        self._position_bubble()

        if hasattr(self, '_chat_worker') and self._chat_worker is not None:
            if self._chat_worker.isRunning():
                self._chat_worker.terminate()
                self._chat_worker.wait(1000)
                print(f"[DEBUG chat_flow] _do_chat: terminated old worker", file=sys.stderr, flush=True)
            self._chat_worker.deleteLater()
        if hasattr(self, '_chat_poll'):
            self._chat_poll.stop()
            print(f"[DEBUG chat_flow] _do_chat: stopped old poll", file=sys.stderr, flush=True)

        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
        self._chat_timeout = QTimer(self)
        self._chat_timeout.setSingleShot(True)
        self._chat_timeout.timeout.connect(self._on_chat_timeout)
        self._chat_timeout.start(130000)
        print(f"[DEBUG chat_flow] _do_chat: started timeout timer 130s", file=sys.stderr, flush=True)

        try:
            self._chat_worker = self._make_chat_worker(message)
            self._chat_worker.start()
            print(f"[DEBUG chat_flow] _do_chat: worker started", file=sys.stderr, flush=True)
        except Exception as exc:
            log.error(f"[chat] worker 启动失败: {type(exc).__name__}: {exc}")
            print(f"[DEBUG chat_flow] _do_chat: worker start exception: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            self._chat_worker = None
            if self._is_agent_mode():
                self._fail_agent_turn("Agent 启动失败，请检查配置。")
            else:
                self._on_chat_error(f"{type(exc).__name__}: {exc}")
            return
        self._chat_poll = QTimer(self)
        self._chat_poll.timeout.connect(self._poll_chat)
        self._chat_poll.start(100)
        worker_name = type(self._chat_worker).__name__
        log.info(f"[chat] {worker_name} 已启动")
        print(f"[DEBUG chat_flow] _do_chat: poll timer started", file=sys.stderr, flush=True)

    def _poll_chat(self):
        print(f"[DEBUG chat_flow] _poll_chat called", file=sys.stderr, flush=True)
        if not hasattr(self, '_chat_worker') or self._chat_worker is None:
            if hasattr(self, '_chat_poll') and self._chat_poll:
                self._chat_poll.stop()
                print(f"[DEBUG chat_flow] _poll_chat: no worker, stopped poll", file=sys.stderr, flush=True)
            return
        worker = self._chat_worker
        context = getattr(worker, "turn_context", None)
        if context is not None and not self._turn_context_is_current(context):
            take_events = getattr(worker, "take_events", None)
            if callable(take_events):
                take_events()
                print(f"[DEBUG chat_flow] _poll_chat: took stale events", file=sys.stderr, flush=True)
            if getattr(worker, "done", False):
                worker.deleteLater()
                if getattr(self, "_chat_worker", None) is worker:
                    self._chat_worker = None
                    print(f"[DEBUG chat_flow] _poll_chat: cleaned stale worker", file=sys.stderr, flush=True)
            log.info("[chat] 已丢弃非活动会话的迟到事件")
            print(f"[DEBUG chat_flow] _poll_chat: discarded stale events", file=sys.stderr, flush=True)
            return
        if callable(getattr(worker, "take_events", None)):
            print(f"[DEBUG chat_flow] _poll_chat: delegating to _poll_agent_chat", file=sys.stderr, flush=True)
            self._poll_agent_chat(worker)
            return
        if not worker.done:
            print(f"[DEBUG chat_flow] _poll_chat: worker not done yet", file=sys.stderr, flush=True)
            return
        print(f"[DEBUG chat_flow] _poll_chat: worker done", file=sys.stderr, flush=True)
        if hasattr(self, '_chat_poll') and self._chat_poll:
            self._chat_poll.stop()
            print(f"[DEBUG chat_flow] _poll_chat: stopped poll", file=sys.stderr, flush=True)
        result, error = worker.get_result()
        worker.deleteLater()
        if error:
            print(f"[DEBUG chat_flow] _poll_chat: error={error[:100]!r}", file=sys.stderr, flush=True)
            self._on_chat_error(error)
        elif result:
            reply, mood = result
            print(f"[DEBUG chat_flow] _poll_chat: result reply_len={len(reply)}, mood={mood!r}", file=sys.stderr, flush=True)
            self._on_chat_done(reply, mood)
        else:
            log.warning("[chat] _poll_chat: 空结果，释放对话锁")
            print(f"[DEBUG chat_flow] _poll_chat: empty result, releasing lock", file=sys.stderr, flush=True)
            set_awaiting_reply_state(self, False)
            if hasattr(self, '_chat_timeout') and self._chat_timeout:
                self._chat_timeout.stop()
                print(f"[DEBUG chat_flow] _poll_chat: stopped timeout", file=sys.stderr, flush=True)
    def _poll_agent_chat(self, worker) -> None:
        print(f"[DEBUG chat_flow] _poll_agent_chat called: worker type={type(worker).__name__}, done={worker.done}", file=sys.stderr, flush=True)
        """把后台 Agent 事件转成交给 Qt 主线程执行的呈现动作。"""
        context = getattr(worker, "turn_context", None)
        print(f"[DEBUG chat_flow] _poll_agent_chat: context={context is not None}, turn_context_current={self._turn_context_is_current(context) if context else 'N/A'}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            worker.take_events()
            if worker.done:
                worker.deleteLater()
                if getattr(self, "_chat_worker", None) is worker:
                    self._chat_worker = None
                    print(f"[DEBUG chat_flow] _poll_agent_chat: cleaned stale worker", file=sys.stderr, flush=True)
            print(f"[DEBUG chat_flow] _poll_agent_chat: stale context, returning", file=sys.stderr, flush=True)
            return
        events = worker.take_events()
        print(f"[DEBUG chat_flow] _poll_agent_chat: took {len(events)} events", file=sys.stderr, flush=True)
        presentation = getattr(self, "_agent_presentation", None)
        for event in events:
            print(f"[DEBUG chat_flow] _poll_agent_chat: processing event type={type(event).__name__}", file=sys.stderr, flush=True)
            self._record_agent_timeline_event(event, context)
            if isinstance(event, TurnCompleted):
                self._agent_turn_result = event.result
                print(f"[DEBUG chat_flow] _poll_agent_chat: TurnCompleted, result segments={len(event.result.segments)}", file=sys.stderr, flush=True)
            if presentation is None:
                print(f"[DEBUG chat_flow] _poll_agent_chat: no presentation, skipping consume", file=sys.stderr, flush=True)
                continue
            for action in presentation.consume(event):
                print(f"[DEBUG chat_flow] _poll_agent_chat: applying action type={type(action).__name__}", file=sys.stderr, flush=True)
                self._apply_agent_action(action, context=context)

        if not worker.done:
            print(f"[DEBUG chat_flow] _poll_agent_chat: worker not done, returning", file=sys.stderr, flush=True)
            return

        print(f"[DEBUG chat_flow] _poll_agent_chat: worker done, stopping poll and timeout", file=sys.stderr, flush=True)
        if hasattr(self, '_chat_poll') and self._chat_poll:
            self._chat_poll.stop()
            print(f"[DEBUG chat_flow] _poll_agent_chat: poll stopped", file=sys.stderr, flush=True)
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
            print(f"[DEBUG chat_flow] _poll_agent_chat: timeout stopped", file=sys.stderr, flush=True)

        error = getattr(worker, "error", None)
        worker.deleteLater()
        if getattr(self, "_chat_worker", None) is worker:
            self._chat_worker = None
            print(f"[DEBUG chat_flow] _poll_agent_chat: cleared _chat_worker", file=sys.stderr, flush=True)

        if error and getattr(self, "_awaiting_reply", False):
            log.error("[chat] 事件流异常，已转为安全系统错误")
            print(f"[DEBUG chat_flow] _poll_agent_chat: error={error[:200]!r}", file=sys.stderr, flush=True)
            backend_name = "Agent" if self._is_agent_mode() else "模型服务"
            self._fail_agent_turn(
                f"{backend_name}连接意外中断，请稍后再试。",
                context=context,
            )
            print(f"[DEBUG chat_flow] _poll_agent_chat: called _fail_agent_turn", file=sys.stderr, flush=True)
            return

        # 正常适配器总会发出完成、失败或取消事件。若流静默结束，不能永久锁住输入。
        if (
            getattr(self, "_awaiting_reply", False)
            and getattr(self, "_agent_turn_result", None) is None
            and not (getattr(self, "_agent_tts_workers", {}) or {})
        ):
            print(f"[DEBUG chat_flow] _poll_agent_chat: silent stream end detected", file=sys.stderr, flush=True)
            # --- Ollama 后端：跳过异常检测，视为正常完成 ---
            llm_cfg = (getattr(self, "config", {}) or {}).get("llm") or {}
            backend = str(llm_cfg.get("backend") or "").strip().lower()
            print(f"[DEBUG chat_flow] _poll_agent_chat: backend={backend!r}", file=sys.stderr, flush=True)
            if backend == "ollama":
                log.info("[agent] Ollama 后端：事件流未产生 TurnCompleted，视为正常完成")
                print(f"[DEBUG chat_flow] _poll_agent_chat: Ollama bypass, constructing empty ParseResult", file=sys.stderr, flush=True)
                from meapet.conversation.output_protocol import ParseResult
                self._agent_turn_result = ParseResult((), (), True, "ollama")
                self._finish_agent_turn(
                    str(getattr(self, "_active_agent_turn_id", "") or ""),
                    context=context,
                )
                print(f"[DEBUG chat_flow] _poll_agent_chat: called _finish_agent_turn for Ollama", file=sys.stderr, flush=True)
                return
            backend_name = "Agent" if self._is_agent_mode() else "模型服务"
            self._fail_agent_turn(
                f"{backend_name}未返回可用回复。",
                context=context,
            )
            print(f"[DEBUG chat_flow] _poll_agent_chat: called _fail_agent_turn for silent end", file=sys.stderr, flush=True)

    def _agent_bubble(
        self,
        index: int,
        *,
        text: str = "",
        mood=None,
        context=None,
    ):
        print(f"[DEBUG chat_flow] _agent_bubble called: index={index}, text_len={len(text)}, mood={mood!r}", file=sys.stderr, flush=True)
        bubbles = getattr(self, "_agent_bubbles", None)
        if bubbles is None:
            bubbles = {}
            self._agent_bubbles = bubbles
            print(f"[DEBUG chat_flow] _agent_bubble: created new bubbles dict", file=sys.stderr, flush=True)
        bubble = bubbles.get(index)
        if bubble is not None:
            print(f"[DEBUG chat_flow] _agent_bubble: found existing bubble at index {index}", file=sys.stderr, flush=True)
            return bubble
        stack = getattr(self, "_bubble_stack", None)
        if stack is None:
            print(f"[DEBUG chat_flow] _agent_bubble: no bubble stack, returning None", file=sys.stderr, flush=True)
            return None
        bubble = stack.begin_message(text, mood=mood)
        bubbles[index] = bubble
        print(f"[DEBUG chat_flow] _agent_bubble: created new bubble at index {index}", file=sys.stderr, flush=True)
        self._bind_bubble_to_timeline(
            bubble,
            str(
                getattr(context, "turn_id", "")
                or getattr(self, "_active_agent_turn_id", "")
                or ""
            ),
        )
        self._position_bubble()
        print(f"[DEBUG chat_flow] _agent_bubble: bound to timeline and positioned", file=sys.stderr, flush=True)
        return bubble

    def _apply_agent_actions(self, actions, *, context=None) -> None:
        print(f"[DEBUG chat_flow] _apply_agent_actions called: actions count={len(actions)}", file=sys.stderr, flush=True)
        for action in actions:
            self._apply_agent_action(action, context=context)
        print(f"[DEBUG chat_flow] _apply_agent_actions done", file=sys.stderr, flush=True)

    def _apply_agent_action(self, action: object, *, context=None) -> None:
        print(f"[DEBUG chat_flow] _apply_agent_action called: action type={type(action).__name__}", file=sys.stderr, flush=True)
        """执行纯状态机动作；系统状态不进入角色历史、TTS 或情绪。"""
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _apply_agent_action: context stale, returning", file=sys.stderr, flush=True)
            return
        stack = getattr(self, "_bubble_stack", None)
        if isinstance(action, BeginBubble):
            print(f"[DEBUG chat_flow] _apply_agent_action: BeginBubble index={action.index}", file=sys.stderr, flush=True)
            self._agent_bubble(action.index, context=context)
            return
        if isinstance(action, UpdateBubble):
            print(f"[DEBUG chat_flow] _apply_agent_action: UpdateBubble index={action.index}, text_len={len(action.text)}", file=sys.stderr, flush=True)
            bubble = self._agent_bubble(action.index, context=context)
            if bubble is not None and stack is not None:
                stack.update_message(bubble, action.text, mood=None)
                self._position_bubble()
                print(f"[DEBUG chat_flow] _apply_agent_action: updated bubble", file=sys.stderr, flush=True)
            return
        if isinstance(action, FinalizeBubble):
            segment = action.segment
            print(f"[DEBUG chat_flow] _apply_agent_action: FinalizeBubble index={segment.index}, display_len={len(segment.display_text)}, mood={segment.mood}", file=sys.stderr, flush=True)
            bubble = self._agent_bubble(
                segment.index,
                text=segment.display_text,
                mood=segment.mood,
                context=context,
            )
            if bubble is not None and stack is not None:
                stack.finalize_message(
                    bubble,
                    segment.display_text,
                    duration_ms=action.duration_ms,
                    mood=segment.mood,
                )
                self._safe_set_mood(segment.mood)
                self._position_bubble()
                print(f"[DEBUG chat_flow] _apply_agent_action: finalized bubble", file=sys.stderr, flush=True)
            return
        if isinstance(action, SubmitTTS):
            print(f"[DEBUG chat_flow] _apply_agent_action: SubmitTTS index={action.segment.index}", file=sys.stderr, flush=True)
            self._submit_agent_tts(action.segment, context=context)
            return
        if isinstance(action, PlayAudio):
            print(f"[DEBUG chat_flow] _apply_agent_action: PlayAudio wav_path={action.wav_path!r}, duration_ms={action.duration_ms}", file=sys.stderr, flush=True)
            self._play_audio(action.wav_path)
            QTimer.singleShot(
                max(0, int(action.duration_ms)),
                lambda index=action.index, current=context: (
                    self._on_agent_audio_finished(index, context=current)
                ),
            )
            print(f"[DEBUG chat_flow] _apply_agent_action: scheduled audio finish callback", file=sys.stderr, flush=True)
            return
        if isinstance(action, ShowStatus):
            safe_text = str(action.safe_text or "").strip() or {
                "started": "正在处理",
                "running": "仍在处理",
                "succeeded": "处理完成",
                "failed": "处理失败",
            }.get(str(action.state or "").lower(), "状态已更新")
            print(f"[DEBUG chat_flow] _apply_agent_action: ShowStatus state={action.state}, safe_text={safe_text!r}", file=sys.stderr, flush=True)
            self._show_bubble(safe_text, 4500, mood=None)
            self._position_bubble()
            return
        if isinstance(action, RequestFormatRepair):
            self._agent_format_repair_pending = True
            log.warning("[agent] 回复协议字段不完整，等待格式修复")
            print(f"[DEBUG chat_flow] _apply_agent_action: RequestFormatRepair set pending", file=sys.stderr, flush=True)
            return
        if isinstance(action, FinishTurn):
            print(f"[DEBUG chat_flow] _apply_agent_action: FinishTurn turn_id={action.turn_id[:24]!r}", file=sys.stderr, flush=True)
            self._finish_agent_turn(action.turn_id, context=context)
            return
        if isinstance(action, FailTurn):
            print(f"[DEBUG chat_flow] _apply_agent_action: FailTurn safe_message_len={len(action.safe_message)}", file=sys.stderr, flush=True)
            self._fail_agent_turn(action.safe_message, context=context)
            return
        if isinstance(action, CancelTurn):
            print(f"[DEBUG chat_flow] _apply_agent_action: CancelTurn", file=sys.stderr, flush=True)
            self._cancel_agent_turn(context=context)

    def _submit_agent_tts(self, segment, *, context=None) -> None:
        print(f"[DEBUG chat_flow] _submit_agent_tts called: segment index={segment.index}, voice_len={len(segment.voice_text)}, language={segment.voice_language!r}", file=sys.stderr, flush=True)
        workers = getattr(self, "_agent_tts_workers", None)
        if workers is None:
            workers = {}
            self._agent_tts_workers = workers
            print(f"[DEBUG chat_flow] _submit_agent_tts: created workers dict", file=sys.stderr, flush=True)
        try:
            worker = TTSWorker(
                self.tts,
                segment.voice_text,
                mood=segment.mood,
                style=segment.tts_style,
                language=segment.voice_language,
            )
            worker.turn_context = context
            workers[segment.index] = worker
            worker.start()
            print(f"[DEBUG chat_flow] _submit_agent_tts: started TTSWorker for index {segment.index}", file=sys.stderr, flush=True)
            try:
                self._ensure_tts_poll()
                print(f"[DEBUG chat_flow] _submit_agent_tts: ensured TTS poll", file=sys.stderr, flush=True)
            except (RuntimeError, TypeError) as exc:
                log.debug(f"[agent] TTS timer 暂未创建: {type(exc).__name__}")
                print(f"[DEBUG chat_flow] _submit_agent_tts: TTS poll exception (non-critical): {type(exc).__name__}", file=sys.stderr, flush=True)
        except Exception as exc:
            workers.pop(segment.index, None)
            log.error(
                f"[agent] 第 {segment.index + 1} 段 TTS 启动失败，回退文字: "
                f"{type(exc).__name__}"
            )
            print(f"[DEBUG chat_flow] _submit_agent_tts: exception starting TTS: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            presentation = getattr(self, "_agent_presentation", None)
            if presentation is not None:
                self._apply_agent_actions(
                    presentation.tts_ready(
                        segment.index,
                        "",
                        audio_duration_ms=0,
                    ),
                    context=context,
                )
                print(f"[DEBUG chat_flow] _submit_agent_tts: applied fallback tts_ready", file=sys.stderr, flush=True)

    def _cleanup_after_turn(self, turn_id: str, context=None) -> None:
        print(f"[DEBUG chat_flow] _cleanup_after_turn called: turn_id={turn_id[:24]!r}, context={context is not None}", file=sys.stderr, flush=True)
        """清理本轮对话状态，不执行记忆操作。"""
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        print(f"[DEBUG chat_flow] _cleanup_after_turn: timeline={timeline is not None}, key={key is not None}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None:
            timeline.finish_turn(key, turn_id)
            print(f"[DEBUG chat_flow] _cleanup_after_turn: finished turn in timeline", file=sys.stderr, flush=True)
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
            print(f"[DEBUG chat_flow] _cleanup_after_turn: stopped timeout", file=sys.stderr, flush=True)
        set_awaiting_reply_state(self, False)
        print(f"[DEBUG chat_flow] _cleanup_after_turn: set awaiting reply False", file=sys.stderr, flush=True)
        self._complete_turn_context(context)
        log.info(f"[chat] 本轮呈现完成 (Ollama 空回复): turn={turn_id[:24]}")
        print(f"[DEBUG chat_flow] _cleanup_after_turn done", file=sys.stderr, flush=True)
    def _finish_agent_turn(self, turn_id: str, *, context=None) -> None:
        print(f"[DEBUG chat_flow] _finish_agent_turn called: turn_id={turn_id[:24]!r}, context={context is not None}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _finish_agent_turn: context stale, returning", file=sys.stderr, flush=True)
            return
        result = getattr(self, "_agent_turn_result", None)
        segments = tuple(getattr(result, "segments", ()) or ())
        reply = "\n\n".join(
            segment.display_text
            for segment in sorted(segments, key=lambda item: item.index)
            if segment.display_text
        ).strip()
        print(f"[DEBUG chat_flow] _finish_agent_turn: result={result is not None}, segments_count={len(segments)}, reply_len={len(reply)}", file=sys.stderr, flush=True)
        # --- Ollama 后端：如果 result 为空或 segments 为空，跳过记忆操作 ---
        llm_cfg = (getattr(self, "config", {}) or {}).get("llm") or {}
        backend = str(llm_cfg.get("backend") or "").strip().lower()
        print(f"[DEBUG chat_flow] _finish_agent_turn: backend={backend!r}, reply_empty={not reply}", file=sys.stderr, flush=True)
        if backend == "ollama" and not reply:
            # 直接清理状态，不执行记忆操作
            print(f"[DEBUG chat_flow] _finish_agent_turn: Ollama empty reply, calling _cleanup_after_turn", file=sys.stderr, flush=True)
            self._cleanup_after_turn(turn_id, context)
            return
        user_text = str(getattr(self, "_last_user_msg", "") or "").strip()
        print(f"[DEBUG chat_flow] _finish_agent_turn: user_text_len={len(user_text)}, is_agent_mode={self._is_agent_mode()}", file=sys.stderr, flush=True)
        if self._is_agent_mode() and user_text and reply:
            history = list(getattr(self, "_agent_history", ()) or ())
            history.extend(
                (
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": reply},
                )
            )
            agent_config = (
                ((getattr(self, "config", {}) or {}).get("llm") or {}).get("agent")
                or {}
            )
            try:
                history_turns = max(0, min(int(agent_config.get("history_turns", 5)), 50))
            except (TypeError, ValueError):
                history_turns = 5
            self._agent_history = history[-history_turns * 2:] if history_turns else []
            print(f"[DEBUG chat_flow] _finish_agent_turn: updated agent history to {len(self._agent_history)} items", file=sys.stderr, flush=True)
        elif user_text and reply:
            mood = segments[0].mood if segments else "neutral"
            print(f"[DEBUG chat_flow] _finish_agent_turn: scheduling memory ops with mood={mood!r}", file=sys.stderr, flush=True)
            QTimer.singleShot(
                0,
                lambda current_reply=reply, current_mood=mood, user=user_text: (
                    self._do_memory_ops(current_reply, current_mood, user)
                ),
            )

        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        print(f"[DEBUG chat_flow] _finish_agent_turn: timeline={timeline is not None}, key={key is not None}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None:
            timeline.finish_turn(key, turn_id)
            print(f"[DEBUG chat_flow] _finish_agent_turn: finished turn in timeline", file=sys.stderr, flush=True)
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
            print(f"[DEBUG chat_flow] _finish_agent_turn: stopped timeout", file=sys.stderr, flush=True)
        set_awaiting_reply_state(self, False)
        print(f"[DEBUG chat_flow] _finish_agent_turn: set awaiting reply False", file=sys.stderr, flush=True)
        self._complete_turn_context(context)
        log.info(f"[chat] 本轮呈现完成: turn={turn_id[:24]}")
        print(f"[DEBUG chat_flow] _finish_agent_turn done", file=sys.stderr, flush=True)

    def _fail_agent_turn(self, safe_message: str, *, context=None) -> None:
        print(f"[DEBUG chat_flow] _fail_agent_turn called: safe_message_len={len(safe_message)}, context={context is not None}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _fail_agent_turn: context stale, returning", file=sys.stderr, flush=True)
            return
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_agent_turn_id", "")
            or ""
        )
        print(f"[DEBUG chat_flow] _fail_agent_turn: turn_id={turn_id[:24]!r}, timeline={timeline is not None}, key={key is not None}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None and turn_id:
            timeline.fail_turn(key, turn_id, safe_message)
            print(f"[DEBUG chat_flow] _fail_agent_turn: failed turn in timeline", file=sys.stderr, flush=True)
        self._agent_tts_workers = {}
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
            print(f"[DEBUG chat_flow] _fail_agent_turn: stopped timeout", file=sys.stderr, flush=True)
        self._show_bubble(str(safe_message or "回复请求失败。"), 10000, mood=None)
        self._position_bubble()
        print(f"[DEBUG chat_flow] _fail_agent_turn: shown error bubble", file=sys.stderr, flush=True)
        set_awaiting_reply_state(self, False)
        self._complete_turn_context(context)
        print(f"[DEBUG chat_flow] _fail_agent_turn done", file=sys.stderr, flush=True)

    def _cancel_agent_turn(self, *, context=None) -> None:
        print(f"[DEBUG chat_flow] _cancel_agent_turn called: context={context is not None}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _cancel_agent_turn: context stale, returning", file=sys.stderr, flush=True)
            return
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_agent_turn_id", "")
            or ""
        )
        print(f"[DEBUG chat_flow] _cancel_agent_turn: turn_id={turn_id[:24]!r}, timeline={timeline is not None}, key={key is not None}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None and turn_id:
            timeline.cancel_turn(key, turn_id)
            print(f"[DEBUG chat_flow] _cancel_agent_turn: cancelled turn in timeline", file=sys.stderr, flush=True)
        self._agent_tts_workers = {}
        self._active_agent_turn_id = ""
        self._agent_format_repair_pending = False
        if hasattr(self, '_chat_timeout') and self._chat_timeout:
            self._chat_timeout.stop()
            print(f"[DEBUG chat_flow] _cancel_agent_turn: stopped timeout", file=sys.stderr, flush=True)
        set_awaiting_reply_state(self, False)
        self._complete_turn_context(context)
        print(f"[DEBUG chat_flow] _cancel_agent_turn done", file=sys.stderr, flush=True)

    def _do_memory_ops(self, reply: str, mood: str, user_msg: str = ""):
        print(f"[DEBUG chat_flow] _do_memory_ops called: reply_len={len(reply)}, mood={mood!r}, user_msg_len={len(user_msg)}", file=sys.stderr, flush=True)
        t = threading.Thread(target=self._do_memory_ops_sync, args=(reply, mood, user_msg), daemon=True)
        t.start()
        print(f"[DEBUG chat_flow] _do_memory_ops: started background thread", file=sys.stderr, flush=True)

    def _do_memory_ops_sync(self, reply: str, mood: str, user_msg: str):
        print(f"[DEBUG chat_flow] _do_memory_ops_sync called: reply_len={len(reply)}, mood={mood!r}, user_msg_len={len(user_msg)}", file=sys.stderr, flush=True)
        if not user_msg:
            print(f"[DEBUG chat_flow] _do_memory_ops_sync: no user_msg, returning", file=sys.stderr, flush=True)
            return
        with _memory_op_lock:
            print(f"[DEBUG chat_flow] _do_memory_ops_sync: acquired lock", file=sys.stderr, flush=True)
            try:
                engine = self.chat_engine
                if not engine or not engine.memory:
                    print(f"[DEBUG chat_flow] _do_memory_ops_sync: no engine or memory, returning", file=sys.stderr, flush=True)
                    return
                # 非 Ollama 后端才重置 system prompt
                llm_cfg = (getattr(self, "config", {}) or {}).get("llm") or {}
                backend = str(llm_cfg.get("backend") or "").strip().lower()
                print(f"[DEBUG chat_flow] _do_memory_ops_sync: backend={backend!r}", file=sys.stderr, flush=True)
                if backend != "ollama":
                    engine.history[0] = {"role": "system", "content": SYSTEM_PROMPT}
                    print(f"[DEBUG chat_flow] _do_memory_ops_sync: reset system prompt", file=sys.stderr, flush=True)

                engine.memory.add_chat("user", user_msg)
                engine.memory.add_chat("mea", reply, mood)
                print(f"[DEBUG chat_flow] _do_memory_ops_sync: added chat entries", file=sys.stderr, flush=True)
                n = len(user_msg or "")
                if n < 10:
                    delta = 1
                elif n < 50:
                    delta = 2
                else:
                    delta = 3
                print(f"[DEBUG chat_flow] _do_memory_ops_sync: user_msg_len={n}, affection_delta={delta}", file=sys.stderr, flush=True)
                upgrade_msg = engine.memory.add_affection(delta)
                full_system = SYSTEM_PROMPT + "\n\n" + engine.memory.build_context_prompt(current_query=user_msg)
                if upgrade_msg:
                    full_system += f"\n\n[内部：好感度升至{engine.memory.get_affection_tier()[1]}。请用稍暖的语气回应。]"
                    print(f"[DEBUG chat_flow] _do_memory_ops_sync: affection upgrade: {upgrade_msg}", file=sys.stderr, flush=True)
                engine.history[0] = {"role": "system", "content": full_system}
                print(f"[DEBUG chat_flow] _do_memory_ops_sync: updated system prompt with memory context", file=sys.stderr, flush=True)
                engine.memory.mark_today_chatted()
                engine.memory.increment_message_counter()
                engine._extract_memories(user_msg, reply)
                engine._summarize_if_needed()
                engine.memory.store_chat_exchange(user_msg, reply)
                print(f"[DEBUG chat_flow] _do_memory_ops_sync: memory operations completed", file=sys.stderr, flush=True)
            except Exception as e:
                log.error(f"[memory] 操作失败: {e}")
                print(f"[DEBUG chat_flow] _do_memory_ops_sync: exception: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        print(f"[DEBUG chat_flow] _do_memory_ops_sync: released lock, done", file=sys.stderr, flush=True)

    def _on_chat_done(self, reply: str, mood: str):
        print(f"[DEBUG chat_flow] _on_chat_done called: reply_len={len(reply)}, mood={mood!r}", file=sys.stderr, flush=True)
        context = getattr(self, "_active_turn_context", None)
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _on_chat_done: context stale, returning", file=sys.stderr, flush=True)
            return
        _log_private_text("[reply] LLM 回复", reply, suffix=f"mood={mood}")
        log.info(f"[reply] 收到回复，mood={mood}")
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
            print(f"[DEBUG chat_flow] _on_chat_done: stopped timeout", file=sys.stderr, flush=True)
        eng = getattr(self, "chat_engine", None)
        known_moods = getattr(eng, "_MOOD_TAGS", ())
        detected = mood if mood in known_moods else self._detect_mood(reply)
        print(f"[DEBUG chat_flow] _on_chat_done: detected mood={detected!r}, known_moods={known_moods}", file=sys.stderr, flush=True)
        voice_text = reply
        tts_style = ""
        try:
            if eng is not None and hasattr(eng, "take_voice_text"):
                jp = (eng.take_voice_text() or "").strip()
                if jp:
                    voice_text = jp
                    print(f"[DEBUG chat_flow] _on_chat_done: took voice_text from engine, len={len(jp)}", file=sys.stderr, flush=True)
                    _log_private_text("[tts] TTS 使用模型日语行", jp)
        except Exception as e:
            log.error(f"[tts] 取日语行失败: {e}")
            print(f"[DEBUG chat_flow] _on_chat_done: exception taking voice_text: {type(e).__name__}", file=sys.stderr, flush=True)
        try:
            if eng is not None and hasattr(eng, "take_tts_style"):
                tts_style = (eng.take_tts_style() or "").strip()
                print(f"[DEBUG chat_flow] _on_chat_done: took tts_style={tts_style!r}", file=sys.stderr, flush=True)
        except Exception as e:
            log.error(f"[tts] 取 TTS 风格失败: {e}")
            print(f"[DEBUG chat_flow] _on_chat_done: exception taking tts_style: {type(e).__name__}", file=sys.stderr, flush=True)
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        print(f"[DEBUG chat_flow] _on_chat_done: timeline={timeline is not None}, key={key is not None}, turn_id={turn_id[:24]!r}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None and turn_id:
            tts = getattr(self, "tts", None)
            voice_language = normalize_voice_language(
                getattr(tts, "voice_lang", "")
                or ((getattr(self, "config", {}) or {}).get("tts") or {}).get(
                    "voice_lang",
                    "",
                )
            ) or "zh"
            print(f"[DEBUG chat_flow] _on_chat_done: voice_language={voice_language!r}", file=sys.stderr, flush=True)
            timeline.complete_segment(
                key,
                turn_id,
                ReplySegment(
                    index=0,
                    display_text=reply,
                    voice_text=voice_text,
                    voice_language=voice_language,
                    mood=detected,
                    tts_style=tts_style,
                ),
            )
            print(f"[DEBUG chat_flow] _on_chat_done: completed segment in timeline", file=sys.stderr, flush=True)
        user_msg = getattr(self, '_last_user_msg', '') or ''
        print(f"[DEBUG chat_flow] _on_chat_done: scheduling memory ops with user_msg_len={len(user_msg)}", file=sys.stderr, flush=True)
        QTimer.singleShot(
            0,
            lambda: self._do_memory_ops(reply, detected, user_msg),
        )

        self._pending_chat_reply = (reply, detected)
        self._pending_chat_context = context
        print(f"[DEBUG chat_flow] _on_chat_done: set pending_chat_reply", file=sys.stderr, flush=True)
        tts = getattr(self, "tts", None)
        tts_enabled = tts is not None and bool(getattr(tts, "enabled", True))
        print(f"[DEBUG chat_flow] _on_chat_done: tts={tts is not None}, tts_enabled={tts_enabled}", file=sys.stderr, flush=True)
        if tts is None or not tts_enabled:
            print(f"[DEBUG chat_flow] _on_chat_done: TTS disabled, completing pending reply immediately", file=sys.stderr, flush=True)
            self._complete_pending_chat_reply()
            return

        set_awaiting_reply_state(
            self,
            True,
            status_language.thinking_busy(),
        )
        print(f"[DEBUG chat_flow] _on_chat_done: set awaiting reply busy for TTS", file=sys.stderr, flush=True)
        try:
            self._tts_worker = TTSWorker(
                tts,
                voice_text,
                mood=detected,
                style=tts_style,
            )
            self._tts_worker.turn_context = context
            self._tts_worker.start()
            print(f"[DEBUG chat_flow] _on_chat_done: started TTSWorker", file=sys.stderr, flush=True)
            self._ensure_tts_poll()
            print(f"[DEBUG chat_flow] _on_chat_done: ensured TTS poll", file=sys.stderr, flush=True)
        except Exception as e:
            log.error(f"[tts] 语音合成启动失败，回退文字: {type(e).__name__}: {e}")
            print(f"[DEBUG chat_flow] _on_chat_done: exception starting TTS: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            self._tts_worker = None
            self._complete_pending_chat_reply()

    def _ensure_tts_poll(self):
        print(f"[DEBUG chat_flow] _ensure_tts_poll called", file=sys.stderr, flush=True)
        if not hasattr(self, '_tts_poll') or not self._tts_poll:
            self._tts_poll = QTimer(self)
            self._tts_poll.timeout.connect(self._poll_tts)
            self._tts_poll.start(100)
            print(f"[DEBUG chat_flow] _ensure_tts_poll: created and started new timer", file=sys.stderr, flush=True)
        else:
            print(f"[DEBUG chat_flow] _ensure_tts_poll: timer already exists", file=sys.stderr, flush=True)

    def _poll_tts(self):
        print(f"[DEBUG chat_flow] _poll_tts called", file=sys.stderr, flush=True)
        if hasattr(self, '_tts_worker') and self._tts_worker and self._tts_worker.done:
            print(f"[DEBUG chat_flow] _poll_tts: _tts_worker done", file=sys.stderr, flush=True)
            context = getattr(self._tts_worker, "turn_context", None)
            if context is not None and not self._turn_context_is_current(context):
                print(f"[DEBUG chat_flow] _poll_tts: _tts_worker context stale, clearing", file=sys.stderr, flush=True)
                self._tts_worker = None
                self._pending_chat_reply = None
                self._pending_chat_context = None
                context = None
                result = None
            else:
                try:
                    result = self._tts_worker.get_result()
                    print(f"[DEBUG chat_flow] _poll_tts: _tts_worker result={result is not None}", file=sys.stderr, flush=True)
                except Exception as e:
                    log.error(f"[tts] 读取合成结果失败: {type(e).__name__}: {e}")
                    print(f"[DEBUG chat_flow] _poll_tts: exception getting result: {type(e).__name__}", file=sys.stderr, flush=True)
                    result = None
                self._tts_worker = None
                self._on_tts_audio(result)
        if hasattr(self, '_speak_worker') and self._speak_worker and self._speak_worker.done:
            print(f"[DEBUG chat_flow] _poll_tts: _speak_worker done", file=sys.stderr, flush=True)
            result = self._speak_worker.get_result()
            self._speak_worker = None
            if result:
                self._on_speak_audio_ready(result)
                print(f"[DEBUG chat_flow] _poll_tts: called _on_speak_audio_ready", file=sys.stderr, flush=True)
        if hasattr(self, '_watch_tts_worker') and self._watch_tts_worker and self._watch_tts_worker.done:
            print(f"[DEBUG chat_flow] _poll_tts: _watch_tts_worker done", file=sys.stderr, flush=True)
            result = self._watch_tts_worker.get_result()
            self._watch_tts_worker = None
            pending = getattr(self, '_pending_reply', None)
            if pending:
                reply, mood = pending
                if hasattr(self, '_pending_reply'):
                    del self._pending_reply
                print(f"[DEBUG chat_flow] _poll_tts: calling _on_watch_tts_and_show with pending reply", file=sys.stderr, flush=True)
                self._on_watch_tts_and_show(result, reply, mood)
            else:
                print(f"[DEBUG chat_flow] _poll_tts: calling _on_watch_tts_and_show without pending reply", file=sys.stderr, flush=True)
                self._on_watch_tts_and_show(result, None, None)

        agent_workers = getattr(self, "_agent_tts_workers", None)
        if agent_workers:
            print(f"[DEBUG chat_flow] _poll_tts: checking {len(agent_workers)} agent workers", file=sys.stderr, flush=True)
            for index, worker in tuple(agent_workers.items()):
                if not worker.done:
                    continue
                print(f"[DEBUG chat_flow] _poll_tts: agent worker index={index} done", file=sys.stderr, flush=True)
                context = getattr(worker, "turn_context", None)
                if context is not None and not self._turn_context_is_current(context):
                    agent_workers.pop(index, None)
                    print(f"[DEBUG chat_flow] _poll_tts: agent worker context stale, removed", file=sys.stderr, flush=True)
                    continue
                try:
                    raw = worker.get_result()
                except Exception as exc:
                    log.error(
                        f"[agent] 第 {index + 1} 段 TTS 结果读取失败: "
                        f"{type(exc).__name__}"
                    )
                    raw = None
                    print(f"[DEBUG chat_flow] _poll_tts: exception getting agent worker result: {type(exc).__name__}", file=sys.stderr, flush=True)
                agent_workers.pop(index, None)
                value = str(raw or "")
                wav_path = value.rsplit("|", 1)[0] if "|" in value else value
                if not wav_path or not os.path.exists(wav_path):
                    wav_path = ""
                duration_ms = (
                    self._get_wav_duration_ms(wav_path) if wav_path else 0
                )
                print(f"[DEBUG chat_flow] _poll_tts: agent worker wav_path={wav_path!r}, duration_ms={duration_ms}", file=sys.stderr, flush=True)
                presentation = getattr(self, "_agent_presentation", None)
                if presentation is not None:
                    self._apply_agent_actions(
                        presentation.tts_ready(
                            index,
                            wav_path,
                            audio_duration_ms=duration_ms,
                        ),
                        context=context,
                    )
                    print(f"[DEBUG chat_flow] _poll_tts: applied tts_ready action", file=sys.stderr, flush=True)

        # 没有待处理的 worker 就停止
        any_workers = any([
            getattr(self, '_tts_worker', None),
            getattr(self, '_speak_worker', None),
            getattr(self, '_watch_tts_worker', None),
            getattr(self, '_agent_tts_workers', None),
        ])
        print(f"[DEBUG chat_flow] _poll_tts: any workers remaining={any_workers}", file=sys.stderr, flush=True)
        if not any_workers:
            if hasattr(self, '_tts_poll') and self._tts_poll:
                self._tts_poll.stop()
                self._tts_poll.deleteLater()
                self._tts_poll = None
                print(f"[DEBUG chat_flow] _poll_tts: stopped and deleted poll timer", file=sys.stderr, flush=True)

    def _on_agent_audio_finished(self, index: int, *, context=None) -> None:
        print(f"[DEBUG chat_flow] _on_agent_audio_finished called: index={index}, context={context is not None}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _on_agent_audio_finished: context stale, returning", file=sys.stderr, flush=True)
            return
        presentation = getattr(self, "_agent_presentation", None)
        if presentation is None:
            print(f"[DEBUG chat_flow] _on_agent_audio_finished: no presentation, returning", file=sys.stderr, flush=True)
            return
        self._apply_agent_actions(
            presentation.audio_finished(index),
            context=context,
        )
        print(f"[DEBUG chat_flow] _on_agent_audio_finished: applied audio_finished action", file=sys.stderr, flush=True)

    def _detect_mood(self, text: str) -> str:
        print(f"[DEBUG chat_flow] _detect_mood called: text_len={len(text)}", file=sys.stderr, flush=True)
        t = text.lower()
        if any(k in t for k in ["嘿嘿","好吃","开心","高兴","棒","哈哈","喜欢"]):
            print(f"[DEBUG chat_flow] _detect_mood: detected happy", file=sys.stderr, flush=True)
            return "happy"
        if any(k in t for k in ["烦","无聊","没兴趣","别吵","哼","切"]):
            print(f"[DEBUG chat_flow] _detect_mood: detected annoyed", file=sys.stderr, flush=True)
            return "annoyed"
        if any(k in t for k in ["哦？","咦","诶","真的？","意外"]):
            print(f"[DEBUG chat_flow] _detect_mood: detected surprised", file=sys.stderr, flush=True)
            return "surprised"
        if any(k in t for k in ["有意思","有趣","让我看看","好奇"]):
            print(f"[DEBUG chat_flow] _detect_mood: detected curious", file=sys.stderr, flush=True)
            return "curious"
        if any(k in t for k in ["唉","难过","伤心","可惜"]):
            print(f"[DEBUG chat_flow] _detect_mood: detected sad", file=sys.stderr, flush=True)
            return "sad"
        if any(k in t for k in ["又没在","随便","……","脸红","害羞"]):
            print(f"[DEBUG chat_flow] _detect_mood: detected shy", file=sys.stderr, flush=True)
            return "shy"
        print(f"[DEBUG chat_flow] _detect_mood: default neutral", file=sys.stderr, flush=True)
        return "neutral"

    def _complete_pending_chat_reply(self, wav_path: str = "") -> None:
        print(f"[DEBUG chat_flow] _complete_pending_chat_reply called: wav_path={wav_path!r}", file=sys.stderr, flush=True)
        pending = getattr(self, "_pending_chat_reply", None)
        context = getattr(self, "_pending_chat_context", None)
        print(f"[DEBUG chat_flow] _complete_pending_chat_reply: pending={pending is not None}, context={context is not None}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            self._pending_chat_reply = None
            self._pending_chat_context = None
            print(f"[DEBUG chat_flow] _complete_pending_chat_reply: context stale, cleared pending", file=sys.stderr, flush=True)
            return
        if pending is None:
            # 兼容旧调用：没有等待文字时，仍允许单独播放有效音频。
            if wav_path:
                self._play_audio(wav_path)
                print(f"[DEBUG chat_flow] _complete_pending_chat_reply: played audio without pending reply", file=sys.stderr, flush=True)
            print(f"[DEBUG chat_flow] _complete_pending_chat_reply: no pending reply, returning", file=sys.stderr, flush=True)
            return

        try:
            del self._pending_chat_reply
        except AttributeError:
            pass
        self._pending_chat_context = None

        reply, mood = pending
        duration_ms = None
        config = getattr(self, "config", {}) or {}
        tts_config = config.get("tts") or {}
        bubble_config = config.get("bubble_duration_ms") or {}
        print(f"[DEBUG chat_flow] _complete_pending_chat_reply: reply_len={len(reply)}, mood={mood!r}, wav_path_exists={os.path.exists(wav_path) if wav_path else False}", file=sys.stderr, flush=True)
        if wav_path and tts_config.get("sync_with_audio"):
            audio_ms = self._get_wav_duration_ms(wav_path)
            print(f"[DEBUG chat_flow] _complete_pending_chat_reply: audio_ms={audio_ms}", file=sys.stderr, flush=True)
            if audio_ms > 0:
                duration_ms = max(
                    audio_ms + 500,
                    int(bubble_config.get("reply", 3000)),
                )
                print(f"[DEBUG chat_flow] _complete_pending_chat_reply: computed duration_ms={duration_ms}", file=sys.stderr, flush=True)

        try:
            if duration_ms is None:
                self.show_reply(reply, mood)
                print(f"[DEBUG chat_flow] _complete_pending_chat_reply: showed reply without custom duration", file=sys.stderr, flush=True)
            else:
                self.show_reply(reply, mood, duration_ms=duration_ms)
                print(f"[DEBUG chat_flow] _complete_pending_chat_reply: showed reply with duration_ms={duration_ms}", file=sys.stderr, flush=True)
        except Exception as e:
            log.error(f"[chat] 显示等待回复失败: {type(e).__name__}: {e}")
            print(f"[DEBUG chat_flow] _complete_pending_chat_reply: exception showing reply: {type(e).__name__}", file=sys.stderr, flush=True)
        finally:
            set_awaiting_reply_state(self, False)
            print(f"[DEBUG chat_flow] _complete_pending_chat_reply: set awaiting reply False", file=sys.stderr, flush=True)

        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        print(f"[DEBUG chat_flow] _complete_pending_chat_reply: timeline={timeline is not None}, key={key is not None}, turn_id={turn_id[:24]!r}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None and turn_id:
            timeline.finish_turn(key, turn_id)
            print(f"[DEBUG chat_flow] _complete_pending_chat_reply: finished turn in timeline", file=sys.stderr, flush=True)
        self._active_timeline_turn_id = ""
        self._complete_turn_context(context)
        print(f"[DEBUG chat_flow] _complete_pending_chat_reply: completed turn context", file=sys.stderr, flush=True)

        if wav_path:
            self._play_audio(wav_path)
            print(f"[DEBUG chat_flow] _complete_pending_chat_reply: played audio", file=sys.stderr, flush=True)
        print(f"[DEBUG chat_flow] _complete_pending_chat_reply done", file=sys.stderr, flush=True)
    def _on_tts_audio(self, raw: str | None):
        """TTS 完成后再显示最终气泡；失败时显示无声文字兜底。"""
        print(f"[DEBUG chat_flow] _on_tts_audio called: raw={raw is not None}, raw_len={len(str(raw)) if raw else 0}", file=sys.stderr, flush=True)
        value = str(raw or "")
        wav_path = value.rsplit("|", 1)[0] if "|" in value else value
        print(f"[DEBUG chat_flow] _on_tts_audio: wav_path={wav_path!r}, exists={os.path.exists(wav_path) if wav_path else False}", file=sys.stderr, flush=True)
        if not wav_path or not os.path.exists(wav_path):
            log.warning(f"[audio] TTS 未生成有效文件，回退文字: chars={len(value)}")
            if debug_enabled():
                log.debug(f"[audio] 无效 TTS 返回: {raw!r}")
            print(f"[DEBUG chat_flow] _on_tts_audio: invalid wav, calling _complete_pending_chat_reply without path", file=sys.stderr, flush=True)
            self._complete_pending_chat_reply()
            return
        print(f"[DEBUG chat_flow] _on_tts_audio: valid wav, calling _complete_pending_chat_reply with path", file=sys.stderr, flush=True)
        self._complete_pending_chat_reply(wav_path)

    def _on_chat_error(self, err: str):
        print(f"[DEBUG chat_flow] _on_chat_error called: err_len={len(err)}", file=sys.stderr, flush=True)
        context = getattr(self, "_active_turn_context", None)
        print(f"[DEBUG chat_flow] _on_chat_error: context={context is not None}, turn_context_current={self._turn_context_is_current(context) if context else 'N/A'}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _on_chat_error: context stale, returning", file=sys.stderr, flush=True)
            return
        _log_private_text("[chat] 错误", err)
        error_summary = (
            redact_text(err)
            if debug_enabled()
            else f"error_chars={len(err or '')}"
        )
        log.error(f"[chat] 对话错误: {error_summary}")
        print(f"[DEBUG chat_flow] _on_chat_error: error_summary={error_summary}", file=sys.stderr, flush=True)
        if hasattr(self, '_chat_timeout'):
            self._chat_timeout.stop()
            print(f"[DEBUG chat_flow] _on_chat_error: stopped timeout", file=sys.stderr, flush=True)
        log_error(
            "pet_chat",
            error_summary,
        )
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        print(f"[DEBUG chat_flow] _on_chat_error: timeline={timeline is not None}, key={key is not None}, turn_id={turn_id[:24]!r}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None and turn_id:
            timeline.fail_turn(key, turn_id, "对话请求失败")
            print(f"[DEBUG chat_flow] _on_chat_error: failed turn in timeline", file=sys.stderr, flush=True)
        self._active_timeline_turn_id = ""
        self._show_bubble(
            status_language.model_service_error(),
            10000,
            mood=None,
        )
        self._position_bubble()
        print(f"[DEBUG chat_flow] _on_chat_error: shown error bubble", file=sys.stderr, flush=True)
        set_awaiting_reply_state(self, False)
        print(f"[DEBUG chat_flow] _on_chat_error: set awaiting reply False", file=sys.stderr, flush=True)
        self._complete_turn_context(context)
        print(f"[DEBUG chat_flow] _on_chat_error: completed turn context", file=sys.stderr, flush=True)

    def _on_chat_timeout(self):
        """ChatWorker 超时 — 强制终止线程并释放锁"""
        print(f"[DEBUG chat_flow] _on_chat_timeout called", file=sys.stderr, flush=True)
        context = getattr(self, "_active_turn_context", None)
        print(f"[DEBUG chat_flow] _on_chat_timeout: context={context is not None}, turn_context_current={self._turn_context_is_current(context) if context else 'N/A'}", file=sys.stderr, flush=True)
        if context is not None and not self._turn_context_is_current(context):
            print(f"[DEBUG chat_flow] _on_chat_timeout: context stale, returning", file=sys.stderr, flush=True)
            return
        log.warning("[chat] ChatWorker 超时，释放锁")
        print(f"[DEBUG chat_flow] _on_chat_timeout: timeout triggered", file=sys.stderr, flush=True)
        set_awaiting_reply_state(self, False)
        print(f"[DEBUG chat_flow] _on_chat_timeout: set awaiting reply False", file=sys.stderr, flush=True)
        self._show_bubble(status_language.chat_timeout(), 3000)
        self._position_bubble()
        print(f"[DEBUG chat_flow] _on_chat_timeout: shown timeout bubble", file=sys.stderr, flush=True)
        timeline = getattr(self, "_conversation_timeline", None)
        key = (
            getattr(context, "conversation_key", None)
            or getattr(self, "_conversation_key", None)
        )
        turn_id = str(
            getattr(context, "turn_id", "")
            or getattr(self, "_active_timeline_turn_id", "")
            or ""
        )
        print(f"[DEBUG chat_flow] _on_chat_timeout: timeline={timeline is not None}, key={key is not None}, turn_id={turn_id[:24]!r}", file=sys.stderr, flush=True)
        if timeline is not None and key is not None and turn_id:
            timeline.fail_turn(key, turn_id, status_language.chat_timeout())
            print(f"[DEBUG chat_flow] _on_chat_timeout: failed turn in timeline", file=sys.stderr, flush=True)
        self._active_timeline_turn_id = ""
        print(f"[DEBUG chat_flow] _on_chat_timeout: cleaning worker", file=sys.stderr, flush=True)
        if hasattr(self, '_chat_worker') and self._chat_worker:
            if self._chat_worker.isRunning():
                print(f"[DEBUG chat_flow] _on_chat_timeout: terminating worker", file=sys.stderr, flush=True)
                self._chat_worker.terminate()
                if not self._chat_worker.wait(2000):
                    log.warning("[chat] ChatWorker 无法终止")
                    print(f"[DEBUG chat_flow] _on_chat_timeout: worker terminate timed out", file=sys.stderr, flush=True)
            self._chat_worker.deleteLater()
            self._chat_worker = None
            print(f"[DEBUG chat_flow] _on_chat_timeout: worker cleaned", file=sys.stderr, flush=True)
        self._complete_turn_context(context)
        print(f"[DEBUG chat_flow] _on_chat_timeout: completed turn context", file=sys.stderr, flush=True)

    def _speak_and_show(self, text: str, duration_ms: int, mood: str = "neutral"):
        """显示文字 + 后台合成语音播放（异常不抛出）"""
        print(f"[DEBUG chat_flow] _speak_and_show called: text_len={len(text)}, duration_ms={duration_ms}, mood={mood!r}", file=sys.stderr, flush=True)
        try:
            self.show_reply(text, mood)
            print(f"[DEBUG chat_flow] _speak_and_show: showed reply", file=sys.stderr, flush=True)
        except Exception as e:
            log.error(f"[speak] 显示文字失败: {type(e).__name__}: {e}")
            print(f"[DEBUG chat_flow] _speak_and_show: exception showing reply: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        try:
            tts = getattr(self, "tts", None)
            tts_enabled = tts is not None and getattr(tts, "enabled", False)
            print(f"[DEBUG chat_flow] _speak_and_show: tts={tts is not None}, tts_enabled={tts_enabled}, text_len_ge2={len((text or '').strip()) >= 2}", file=sys.stderr, flush=True)
            if tts and tts_enabled and len((text or "").strip()) >= 2:
                self._current_speaking_text = text
                cached = None
                try:
                    cached = tts.get_cached(text)
                    print(f"[DEBUG chat_flow] _speak_and_show: cache hit={cached is not None}", file=sys.stderr, flush=True)
                except Exception as e:
                    log.error(f"[speak] 缓存查询失败: {type(e).__name__}: {e}")
                    print(f"[DEBUG chat_flow] _speak_and_show: cache lookup exception: {type(e).__name__}", file=sys.stderr, flush=True)
                if cached:
                    self._play_audio(cached)
                    print(f"[DEBUG chat_flow] _speak_and_show: played cached audio", file=sys.stderr, flush=True)
                    return
                self._speak_worker = TTSWorker(tts, text, mood=mood)
                self._speak_worker.start()
                print(f"[DEBUG chat_flow] _speak_and_show: started TTSWorker", file=sys.stderr, flush=True)
                self._ensure_tts_poll()
                print(f"[DEBUG chat_flow] _speak_and_show: ensured TTS poll", file=sys.stderr, flush=True)
        except Exception as e:
            log.error(f"[speak] 语音合成启动失败: {type(e).__name__}: {e}")
            print(f"[DEBUG chat_flow] _speak_and_show: exception starting TTS: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    def _on_speak_audio_ready(self, raw: str):
        """后台语音合成完成，播放并缓存"""
        print(f"[DEBUG chat_flow] _on_speak_audio_ready called: raw_len={len(raw)}", file=sys.stderr, flush=True)
        wav_path = raw
        tts_lang = ""
        if "|" in raw:
            parts = raw.rsplit("|", 1)
            wav_path = parts[0]
            tts_lang = parts[1]
            print(f"[DEBUG chat_flow] _on_speak_audio_ready: split wav_path={wav_path!r}, tts_lang={tts_lang!r}", file=sys.stderr, flush=True)
        print(f"[DEBUG chat_flow] _on_speak_audio_ready: wav_path exists={os.path.exists(wav_path) if wav_path else False}", file=sys.stderr, flush=True)
        if wav_path and os.path.exists(wav_path):
            # 缓存：用语言前缀统一命名
            if tts_lang:
                safe = self._safe_name(
                    self._current_speaking_text
                    if hasattr(self, "_current_speaking_text") else ""
                )
                print(f"[DEBUG chat_flow] _on_speak_audio_ready: safe_name={safe!r}", file=sys.stderr, flush=True)
                if safe:
                    from meapet.paths import project_path
                    cache_dir = project_path("voice_cache")
                    os.makedirs(cache_dir, exist_ok=True)
                    cache_path = os.path.join(cache_dir, f"{tts_lang}_{safe}.wav")
                    try:
                        shutil.copy2(wav_path, cache_path)
                        print(f"[DEBUG chat_flow] _on_speak_audio_ready: cached to {cache_path!r}", file=sys.stderr, flush=True)
                    except Exception as exc:
                        log.warning(f"[speak] 缓存写入失败: {type(exc).__name__}")
                        print(f"[DEBUG chat_flow] _on_speak_audio_ready: cache write exception: {type(exc).__name__}", file=sys.stderr, flush=True)
            self._play_audio(wav_path)
            print(f"[DEBUG chat_flow] _on_speak_audio_ready: played audio", file=sys.stderr, flush=True)
        else:
            print(f"[DEBUG chat_flow] _on_speak_audio_ready: wav_path not valid, skipping play", file=sys.stderr, flush=True)

    def show_reply(self, text: str, mood: str = "neutral", duration_ms: int = None):
        print(f"[DEBUG chat_flow] show_reply called: text_len={len(text)}, mood={mood!r}, duration_ms={duration_ms}", file=sys.stderr, flush=True)
        if duration_ms is None:
            duration_ms = self.config["bubble_duration_ms"]["reply"]
            print(f"[DEBUG chat_flow] show_reply: using default duration_ms={duration_ms}", file=sys.stderr, flush=True)
        self._safe_set_mood(mood)
        print(f"[DEBUG chat_flow] show_reply: set mood to {mood!r}", file=sys.stderr, flush=True)
        self._show_bubble(text, max(duration_ms, 3000), mood=mood)
        print(f"[DEBUG chat_flow] show_reply: showed bubble with duration={max(duration_ms, 3000)}", file=sys.stderr, flush=True)
        self._bind_bubble_to_timeline(
            getattr(self, "bubble", None),
            str(getattr(self, "_active_timeline_turn_id", "") or ""),
        )
        print(f"[DEBUG chat_flow] show_reply: bound bubble to timeline", file=sys.stderr, flush=True)
        self._position_bubble()
        print(f"[DEBUG chat_flow] show_reply: positioned bubble", file=sys.stderr, flush=True)

