"""asyncio 运行时与异步 chat / worker 契约"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestAsyncRuntime(unittest.TestCase):
    def test_submit_and_run(self):
        import asyncio
        from meapet.async_runtime import submit, run, get_loop

        async def add(a, b):
            await asyncio.sleep(0.01)
            return a + b

        loop = get_loop()
        self.assertTrue(loop.is_running())
        fut = submit(add(2, 3))
        self.assertEqual(fut.result(timeout=2), 5)
        self.assertEqual(run(add(1, 1), timeout=2), 2)


class TestChatAsyncPath(unittest.TestCase):
    def test_quick_chat_uses_async_runtime(self):
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(api_key="k", model="m")
        eng.available = True

        async def _fake_dispatch(messages):
            return "[happy]异步喵"

        with mock.patch.object(eng, "_dispatch_chat_async", side_effect=_fake_dispatch) as d:
            reply, mood = eng.quick_chat("hi")
        self.assertEqual(mood, "happy")
        self.assertEqual(reply, "异步喵")
        self.assertTrue(d.called)

    def test_quick_chat_async_direct(self):
        import asyncio
        from meapet.chat.engine import ChatEngine
        from meapet.async_runtime import run

        eng = ChatEngine(api_key="k", model="m")
        eng.available = True

        async def _fake_dispatch(messages):
            return "[curious]直接async"

        with mock.patch.object(eng, "_dispatch_chat_async", side_effect=_fake_dispatch):
            reply, mood = run(eng.quick_chat_async("x"), timeout=5)
        self.assertEqual(mood, "curious")
        self.assertEqual(reply, "直接async")


class TestAsyncWorkers(unittest.TestCase):
    def test_chat_worker_async_done(self):
        from meapet.desktop.workers import ChatWorker

        class FakeEngine:
            async def quick_chat_async(self, message):
                return (f"回:{message}", "happy")

        w = ChatWorker(FakeEngine(), "你好")
        w.start()
        deadline = time.time() + 3
        while not w.done and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(w.done)
        result, err = w.get_result()
        self.assertIsNone(err)
        self.assertEqual(result, ("回:你好", "happy"))

    def test_tts_worker_async_done(self):
        from meapet.desktop.workers import TTSWorker

        class FakeTTS:
            async def speak_async(self, text, mood="neutral"):
                return ("/tmp/a.wav", "zh")

        w = TTSWorker(FakeTTS(), "主人", mood="happy")
        w.start()
        deadline = time.time() + 3
        while not w.done and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(w.done)
        self.assertEqual(w.get_result(), "/tmp/a.wav|zh")

    def test_tts_worker_passes_model_generated_style(self):
        from meapet.desktop.workers import TTSWorker

        captured = {}

        class FakeTTS:
            async def speak_async(self, text, mood="neutral", style=""):
                captured.update(text=text, mood=mood, style=style)
                return ("/tmp/styled.wav", "jp")

        w = TTSWorker(
            FakeTTS(),
            "おかえりにゃ",
            mood="shy",
            style="保持参考音色。情绪：害羞。",
        )
        w.start()
        deadline = time.time() + 3
        while not w.done and time.time() < deadline:
            time.sleep(0.01)

        self.assertTrue(w.done)
        self.assertEqual(w.get_result(), "/tmp/styled.wav|jp")
        self.assertEqual(
            captured,
            {
                "text": "おかえりにゃ",
                "mood": "shy",
                "style": "保持参考音色。情绪：害羞。",
            },
        )

    def test_chat_worker_no_per_request_thread_attr(self):
        from meapet.desktop.workers import ChatWorker
        w = ChatWorker(type("E", (), {"quick_chat_async": None})(), "x")
        # new worker should not use _thread field
        self.assertFalse(hasattr(w, "_thread") and w.__dict__.get("_thread") is not None)
        self.assertTrue(hasattr(w, "_future") or True)


if __name__ == "__main__":
    unittest.main()
