"""会话隔离时间线、完整本轮投影与 Agent 新会话行为。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _segment(index: int, text: str):
    from meapet.conversation.types import ReplySegment

    return ReplySegment(
        index=index,
        display_text=text,
        voice_text=text,
        voice_language="zh",
        mood="neutral",
        tts_style="",
    )


class TestConversationTimeline(unittest.TestCase):
    def test_lowering_limit_prunes_existing_turns_immediately(self):
        from meapet.conversation.timeline import ConversationKey, ConversationTimeline

        key = ConversationKey("direct", "ollama", "local")
        timeline = ConversationTimeline(max_turns=5)
        for index in range(4):
            turn_id = f"turn-{index}"
            timeline.start_turn(
                key,
                turn_id,
                source="user_reply",
                user_text=f"问题 {index}",
            )
            timeline.complete_segment(
                key,
                turn_id,
                _segment(0, f"回答 {index}"),
            )
            timeline.finish_turn(key, turn_id)

        timeline.set_max_turns(2)
        self.assertEqual(
            [turn.turn_id for turn in timeline.recent(key)],
            ["turn-2", "turn-3"],
        )

        timeline.set_max_turns(0)
        self.assertEqual(timeline.all_recent(), ())

    def test_turns_are_isolated_by_conversation_key_and_evicted_per_key(self):
        from meapet.conversation.timeline import ConversationKey, ConversationTimeline

        first = ConversationKey("agent", "hermes", "session-a")
        second = ConversationKey("agent", "hermes", "session-b")
        timeline = ConversationTimeline(max_turns=2)

        for number in range(3):
            turn_id = f"a-{number}"
            timeline.start_turn(
                first,
                turn_id,
                source="user_reply",
                user_text=f"问题 {number}",
            )
            timeline.complete_segment(first, turn_id, _segment(0, f"回答 {number}"))
            timeline.finish_turn(first, turn_id)
        timeline.start_turn(second, "b-0", source="user_reply", user_text="另一会话")
        timeline.complete_segment(second, "b-0", _segment(0, "另一回答"))
        timeline.finish_turn(second, "b-0")

        self.assertEqual(
            [turn.turn_id for turn in timeline.recent(first)],
            ["a-1", "a-2"],
        )
        self.assertEqual(
            [turn.turn_id for turn in timeline.recent(second)],
            ["b-0"],
        )
        self.assertEqual(len(timeline.all_recent()), 3)

    def test_streaming_text_multi_segments_and_all_safe_statuses_are_retained(self):
        from meapet.conversation.timeline import ConversationKey, ConversationTimeline

        key = ConversationKey("agent", "hermes", "session-a")
        timeline = ConversationTimeline(max_turns=5)
        timeline.start_turn(key, "turn", source="user_reply", user_text="开始")
        timeline.update_segment_text(key, "turn", 0, "持续")
        timeline.update_segment_text(key, "turn", 0, "持续增长")
        for state, text in (
            ("started", "正在处理"),
            ("succeeded", "处理完成"),
            ("failed", "处理失败"),
        ):
            timeline.add_status(key, "turn", state=state, safe_text=text)
        for index in range(5):
            timeline.complete_segment(key, "turn", _segment(index, f"第 {index + 1} 段"))
        timeline.finish_turn(key, "turn")

        turn = timeline.get(key, "turn")
        self.assertEqual(turn.status, "complete")
        self.assertEqual([part.display_text for part in turn.segments], [
            "第 1 段", "第 2 段", "第 3 段", "第 4 段", "第 5 段",
        ])
        self.assertEqual(
            [(item.state, item.safe_text) for item in turn.system_entries],
            [
                ("started", "正在处理"),
                ("succeeded", "处理完成"),
                ("failed", "处理失败"),
            ],
        )
        self.assertNotIn("tool_name", repr(turn))

    def test_late_updates_after_terminal_state_are_ignored(self):
        from meapet.conversation.timeline import ConversationKey, ConversationTimeline

        key = ConversationKey("direct", "ollama", "local")
        timeline = ConversationTimeline()
        timeline.start_turn(key, "turn", source="user_reply")
        timeline.fail_turn(key, "turn", "连接失败")
        timeline.complete_segment(key, "turn", _segment(0, "迟到回复"))

        turn = timeline.get(key, "turn")
        self.assertEqual(turn.status, "error")
        self.assertEqual(turn.segments, ())
        self.assertEqual(turn.error_text, "连接失败")

    def test_terminal_turns_roundtrip_through_sqlite_and_restore_history(self):
        from meapet.conversation.timeline import (
            ConversationKey,
            ConversationTimeline,
        )
        from meapet.memory import db as memory_db

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        old_path = memory_db.DB_PATH
        memory_db.DB_PATH = path
        memory = None
        try:
            memory = memory_db.MeaMemory()
            key = ConversationKey("agent", "hermes", "persisted-session")
            timeline = ConversationTimeline(
                max_turns=2,
                terminal_callback=lambda turn: memory.save_conversation_turn(
                    turn,
                    max_turns=2,
                ),
            )
            for number in range(3):
                turn_id = f"persisted-{number}"
                timeline.start_turn(
                    key,
                    turn_id,
                    source="user_reply",
                    user_text=f"问题 {number}",
                )
                timeline.complete_segment(
                    key,
                    turn_id,
                    _segment(0, f"回答 {number}"),
                )
                timeline.finish_turn(key, turn_id)

            restored = ConversationTimeline(max_turns=2)
            for turn in memory.load_conversation_turns():
                restored.restore(turn)

            self.assertEqual(
                [turn.turn_id for turn in restored.recent(key)],
                ["persisted-1", "persisted-2"],
            )
            self.assertEqual(
                restored.history(key, max_turns=2),
                (
                    {"role": "user", "content": "问题 1"},
                    {"role": "assistant", "content": "回答 1"},
                    {"role": "user", "content": "问题 2"},
                    {"role": "assistant", "content": "回答 2"},
                ),
            )
        finally:
            if memory is not None:
                memory.close()
            memory_db.DB_PATH = old_path
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except OSError:
                    pass


class TestConversationOrchestrator(unittest.TestCase):
    def test_switching_conversation_invalidates_old_generation(self):
        from meapet.conversation.orchestrator import ConversationOrchestrator
        from meapet.conversation.timeline import ConversationKey

        first = ConversationKey("agent", "hermes", "session-a")
        second = ConversationKey("agent", "hermes", "session-b")
        orchestrator = ConversationOrchestrator(first)
        old_turn = orchestrator.begin_turn("turn-a")

        generation = orchestrator.activate(second)
        new_turn = orchestrator.begin_turn("turn-b")

        self.assertGreater(generation, old_turn.generation_id)
        self.assertFalse(orchestrator.accepts(old_turn))
        self.assertTrue(orchestrator.accepts(new_turn))
        self.assertEqual(new_turn.conversation_key, second)

    def test_same_key_keeps_generation_but_new_turn_replaces_old_turn(self):
        from meapet.conversation.orchestrator import ConversationOrchestrator
        from meapet.conversation.timeline import ConversationKey

        key = ConversationKey("direct", "ollama", "local")
        orchestrator = ConversationOrchestrator(key)
        first = orchestrator.begin_turn("first")
        generation = orchestrator.activate(key)
        second = orchestrator.begin_turn("second")

        self.assertEqual(generation, first.generation_id)
        self.assertEqual(orchestrator.conversation_key, key)
        self.assertEqual(orchestrator.generation_id, generation)
        self.assertFalse(orchestrator.accepts(first))
        self.assertTrue(orchestrator.accepts(second))
        self.assertTrue(orchestrator.complete(second))
        self.assertFalse(orchestrator.accepts(second))
        self.assertFalse(orchestrator.complete(second))

    def test_invalidate_and_invalid_context_inputs_are_rejected(self):
        from meapet.conversation.orchestrator import (
            ConversationOrchestrator,
            TurnContext,
        )
        from meapet.conversation.timeline import ConversationKey

        key = ConversationKey("agent", "openclaw", "session")
        orchestrator = ConversationOrchestrator(key)
        context = orchestrator.begin_turn("turn")
        invalidated_generation = orchestrator.invalidate()

        self.assertGreater(invalidated_generation, context.generation_id)
        self.assertFalse(orchestrator.accepts(context))
        self.assertFalse(orchestrator.accepts(None))
        with self.assertRaises(TypeError):
            ConversationOrchestrator("not-a-key")
        with self.assertRaises(TypeError):
            orchestrator.activate("not-a-key")
        for values in (
            {"conversation_key": key, "turn_id": "", "generation_id": 1},
            {"conversation_key": key, "turn_id": "bad\nturn", "generation_id": 1},
            {"conversation_key": key, "turn_id": "turn", "generation_id": 0},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                TurnContext(**values)


class TestTimelineViewer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_full_turn_dialog_contains_every_segment_and_can_copy(self):
        from PyQt5.QtWidgets import QApplication
        from meapet.conversation.timeline import (
            ConversationKey,
            ConversationTimeline,
        )
        from meapet.desktop.timeline_viewer import TurnDetailDialog

        key = ConversationKey("agent", "hermes", "session-a")
        timeline = ConversationTimeline()
        timeline.start_turn(key, "turn", source="agent_proactive")
        for index in range(4):
            timeline.complete_segment(key, "turn", _segment(index, f"完整段落 {index}"))
        timeline.finish_turn(key, "turn")
        dialog = TurnDetailDialog(timeline.get(key, "turn"))
        self.addCleanup(dialog.deleteLater)

        rendered = dialog.content.toPlainText()
        for index in range(4):
            self.assertIn(f"完整段落 {index}", rendered)
        dialog._copy_all()
        self.assertEqual(QApplication.clipboard().text(), rendered)
        self.assertIn("Agent", dialog.meta.text())
        self.assertIn("hermes", dialog.meta.text())
        self.assertIn("session-a", dialog.meta.text())

    def test_dialogue_bubble_emits_activation_for_full_turn_view(self):
        from PyQt5.QtCore import QEvent, QPointF, Qt
        from PyQt5.QtGui import QMouseEvent
        from meapet.desktop.widgets import DialogueBox

        bubble = DialogueBox()
        self.addCleanup(bubble.deleteLater)
        activated = []
        bubble.activated.connect(lambda: activated.append(True))
        event = QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(10, 10),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )

        bubble.mouseReleaseEvent(event)

        self.assertEqual(activated, [True])


class TestAgentSessionReset(unittest.TestCase):
    def test_agent_reset_creates_new_session_without_deleting_local_memory(self):
        from PyQt5.QtWidgets import QMessageBox
        from meapet.desktop.window_chrome import PetWindowChromeMixin

        old_session = "old-session"
        old_key = "long-term-memory-key"

        class Memory:
            def __init__(self):
                self.reset_calls = 0

            def reset_all(self):
                self.reset_calls += 1

        class Host(PetWindowChromeMixin):
            config = {
                "llm": {
                    "mode": "agent",
                    "agent": {
                        "kind": "hermes",
                        "session_id": old_session,
                        "session_key": old_key,
                    },
                }
            }

            def __init__(self):
                self.memory = Memory()
                self._agent_history = [{"role": "user", "content": "旧对话"}]
                self.saved = 0
                self.bubbles = []

            def _save_config(self):
                self.saved += 1

            def _show_bubble(self, text, duration, mood=None):
                self.bubbles.append((text, duration, mood))

        host = Host()
        with (
            mock.patch(
                "meapet.desktop.window_chrome.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ) as question,
            mock.patch("meapet.agent.factory.create_agent_adapter_from_config", return_value="new-adapter"),
        ):
            host._reset_memory()

        new_session = host.config["llm"]["agent"]["session_id"]
        self.assertNotEqual(new_session, old_session)
        self.assertEqual(host.config["llm"]["agent"]["session_key"], old_key)
        self.assertEqual(host._agent_history, [])
        self.assertEqual(host.agent_adapter, "new-adapter")
        self.assertEqual(host.memory.reset_calls, 0)
        self.assertEqual(host.saved, 1)
        self.assertIn("不会删除", question.call_args.args[2])

    def test_direct_reset_also_clears_the_in_memory_timeline(self):
        from PyQt5.QtWidgets import QMessageBox
        from meapet.conversation.timeline import ConversationKey, ConversationTimeline
        from meapet.desktop.window_chrome import PetWindowChromeMixin

        key = ConversationKey("direct", "ollama", "local")

        class Memory:
            def __init__(self):
                self.reset_calls = 0

            def reset_all(self):
                self.reset_calls += 1

        class Host(PetWindowChromeMixin):
            config = {"llm": {"mode": "direct"}}

            def __init__(self):
                self.memory = Memory()
                self._conversation_timeline = ConversationTimeline()
                self._conversation_timeline.start_turn(
                    key,
                    "old-turn",
                    source="user_reply",
                    user_text="旧问题",
                )
                self._conversation_timeline.complete_segment(
                    key,
                    "old-turn",
                    _segment(0, "旧回答"),
                )
                self._conversation_timeline.finish_turn(key, "old-turn")

            def _show_bubble(self, *_args, **_kwargs):
                pass

        host = Host()
        with mock.patch(
            "meapet.desktop.window_chrome.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            host._reset_memory()

        self.assertEqual(host.memory.reset_calls, 1)
        self.assertEqual(host._conversation_timeline.all_recent(), ())


if __name__ == "__main__":
    unittest.main()
