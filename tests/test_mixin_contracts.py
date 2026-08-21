"""Mixin 组合契约：方法绑定、跨 mixin 调用链、交互异常不抛出（OpenAI 兼容版）。"""

from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestMixinBinding(unittest.TestCase):
    def test_no_staticmethod_with_self_param(self):
        from meapet.desktop.audio import PetAudioMixin
        from meapet.desktop.watch_ctrl import PetWatcherMixin
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.interaction import PetInteractionMixin

        for cls in (PetAudioMixin, PetWatcherMixin, PetChatFlowMixin, PetInteractionMixin):
            for name, obj in cls.__dict__.items():
                if not isinstance(obj, staticmethod):
                    continue
                params = list(inspect.signature(obj.__func__).parameters)
                self.assertNotEqual(
                    params[:1],
                    ["self"],
                    msg=f"{cls.__name__}.{name} is staticmethod but first param is self",
                )

    def test_speak_and_show_is_instance_method(self):
        from meapet.desktop.chat_flow import PetChatFlowMixin

        self.assertFalse(
            isinstance(
                inspect.getattr_static(PetChatFlowMixin, "_speak_and_show"),
                staticmethod,
            )
        )
        params = list(inspect.signature(PetChatFlowMixin._speak_and_show).parameters)
        self.assertEqual(params[0], "self")

    def test_get_wav_duration_is_staticmethod_without_self(self):
        from meapet.desktop.audio import PetAudioMixin

        obj = inspect.getattr_static(PetAudioMixin, "_get_wav_duration_ms")
        self.assertTrue(isinstance(obj, staticmethod))
        params = list(inspect.signature(obj.__func__).parameters)
        self.assertNotEqual(params[:1], ["self"])

    def test_interaction_methods_are_instance_methods(self):
        """interaction.py 中的方法是实例方法，且调用链正确。"""
        from meapet.desktop.interaction import PetInteractionMixin

        # _on_zone_triggered 是实例方法
        params = list(inspect.signature(PetInteractionMixin._on_zone_triggered).parameters)
        self.assertEqual(params[0], "self")

        # _on_head_patted 是实例方法
        params = list(inspect.signature(PetInteractionMixin._on_head_patted).parameters)
        self.assertEqual(params[0], "self")

        # _show_bubble 是实例方法
        params = list(inspect.signature(PetInteractionMixin._show_bubble).parameters)
        self.assertEqual(params[0], "self")

        # _record_interaction 是实例方法
        params = list(inspect.signature(PetInteractionMixin._record_interaction).parameters)
        self.assertEqual(params[0], "self")

    def test_interaction_calls_chat_flow_methods(self):
        """interaction 的 _on_zone_triggered 调用 show_reply（来自 chat_flow）。"""
        from meapet.desktop.interaction import PetInteractionMixin

        src = inspect.getsource(PetInteractionMixin._on_zone_triggered)
        # 调用链：_on_zone_triggered → show_reply + _play_audio + _safe_set_mood
        self.assertIn("show_reply", src)
        self.assertIn("_play_audio", src)
        self.assertIn("_safe_set_mood", src)
        self.assertIn("_record_interaction", src)


class _FakeBubble:
    def __init__(self):
        self.texts = []

    def show_text(self, text, duration_ms=0, mood=None, **kwargs):
        self.texts.append((text, duration_ms, mood))


class _FakeTTS:
    enabled = True

    def get_cached(self, text):
        return None


class _Composite:
    """手动拼出与 MeaPet 相同的跨 mixin 能力（不启 Qt 主窗）"""

    def __init__(self):
        from meapet.desktop.audio import PetAudioMixin
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.interaction import PetInteractionMixin

        self._mix_audio = PetAudioMixin
        self._mix_chat = PetChatFlowMixin
        self._mix_inter = PetInteractionMixin

        self.config = {
            "bubble_duration_ms": {
                "default": 1000,
                "reply": 1000,
                "interaction": 1000,
                "watch": 1000,
                "thinking": 0,
            },
            "tts": {"sync_with_audio": False},
        }
        self.tts = _FakeTTS()
        self.bubble = _FakeBubble()
        self._last_interaction_time = 0
        self._safe_moods = []
        self._played = []
        self._workers = []

    # --- wire methods like MRO would ---
    def _safe_set_mood(self, mood):
        self._safe_moods.append(mood)

    def _play_audio(self, path, audio_type="sfx", **kwargs):
        self._played.append(path)

    def _position_bubble(self):
        pass

    def _bind_bubble_to_timeline(self, *_args, **_kwargs):
        pass

    def _ensure_tts_poll(self):
        pass

    def _get_wav_duration_ms(self, _path):
        return 500

    def show_reply(self, text, mood="neutral", duration_ms=None):
        from meapet.desktop.chat_flow import PetChatFlowMixin
        return PetChatFlowMixin.show_reply(self, text, mood, duration_ms)

    def _show_bubble(self, text, duration_ms=None, mood=None):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._show_bubble(self, text, duration_ms, mood=mood)

    def _speak_and_show(self, text, duration_ms, mood="neutral"):
        from meapet.desktop.chat_flow import PetChatFlowMixin
        return PetChatFlowMixin._speak_and_show(self, text, duration_ms, mood)

    def _on_head_patted(self):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._on_head_patted(self)

    def _on_zone_triggered(self, zone):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._on_zone_triggered(self, zone)

    def _pick_zone_audio(self, zone):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._pick_zone_audio(self, zone)

    def _interaction_speak(self, text, duration_ms, mood):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._interaction_speak(self, text, duration_ms, mood)

    def _get_cached_interaction(self, text, lang="jp"):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._get_cached_interaction(self, text, lang)

    def _record_interaction(self):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._record_interaction(self)

    def _safe_name(self, text):
        from meapet.desktop.interaction import PetInteractionMixin
        return PetInteractionMixin._safe_name(self, text)


class TestCrossMixinCallChain(unittest.TestCase):
    def test_head_patted_calls_zone_triggered_with_upper(self):
        """_on_head_patted 应优先调用 _on_zone_triggered('upper')。"""
        c = _Composite()
        triggered = []

        def fake_zone(self, zone):
            triggered.append(zone)
            return True

        with mock.patch.object(_Composite, "_on_zone_triggered", fake_zone):
            c._on_head_patted()
        self.assertEqual(triggered, ["upper"])

    def test_zone_triggered_calls_show_reply_and_play_audio(self):
        """_on_zone_triggered 应调用 show_reply + _play_audio + _safe_set_mood。"""
        c = _Composite()
        # 让 _pick_zone_audio 返回一个固定结果
        c._pick_zone_audio = lambda zone: ("/fake/path.wav", "别摸了")
        c._get_wav_duration_ms = lambda path: 500

        self.assertTrue(c._on_zone_triggered("upper"))

        self.assertTrue(c.bubble.texts)
        self.assertEqual(c.bubble.texts[-1][0], "别摸了")
        self.assertEqual(c._played, ["/fake/path.wav"])
        self.assertIn("neutral", c._safe_moods)

    def test_head_patted_plays_zone_audio_without_tts(self):
        """有 upper 预制文件时，应直接播分区 WAV，不走 _interaction_speak。"""
        c = _Composite()
        c._pick_zone_audio = lambda zone: ("/fake/upper.wav", "别摸我头发。")
        spoke = []

        def track_speak(self, text, duration_ms, mood):
            spoke.append((text, duration_ms, mood))

        with mock.patch.object(_Composite, "_interaction_speak", track_speak):
            c._on_head_patted()

        self.assertEqual(spoke, [])
        self.assertEqual(c._played, ["/fake/upper.wav"])
        self.assertTrue(c.bubble.texts)
        self.assertEqual(c.bubble.texts[-1][0], "别摸我头发。")

    def test_head_patted_falls_back_to_interaction_speak_when_zone_empty(self):
        """upper 分区无文件时，回退到文案 + _interaction_speak。"""
        c = _Composite()
        c._pick_zone_audio = lambda zone: None
        spoke = []

        def track_speak(self, text, duration_ms, mood):
            spoke.append((text, duration_ms, mood))

        with mock.patch.object(_Composite, "_interaction_speak", track_speak):
            c._on_head_patted()

        self.assertEqual(len(spoke), 1)
        self.assertIsInstance(spoke[0][0], str)
        self.assertTrue(spoke[0][0])

    def test_get_cached_interaction_works_without_tts(self):
        """本地扁平缓存查找不依赖 self.tts。"""
        import meapet.paths as paths_mod
        from meapet.desktop.interaction import PetInteractionMixin
        from meapet.utils import audio_cache_key

        c = _Composite()
        c.tts = None
        text = "别摸了……"
        key = audio_cache_key(text)
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / f"jp_{key}.wav"
            wav.write_bytes(b"RIFF" + b"\x00" * 40)
            with mock.patch.object(paths_mod, "project_path", return_value=td):
                found = PetInteractionMixin._get_cached_interaction(c, text, "jp")
            self.assertEqual(found, str(wav))

    def test_head_patted_does_not_raise_when_zone_pipeline_breaks(self):
        """_on_head_patted 异常不应抛出，应 fallback 到 _show_bubble。"""
        c = _Composite()

        def boom(self, zone):
            raise RuntimeError("zone pipeline broken")

        with mock.patch.object(_Composite, "_on_zone_triggered", boom):
            # should not raise
            c._on_head_patted()
        # fallback bubble shown
        self.assertTrue(c.bubble.texts)

    def test_lower_left_and_right_patted_call_correct_zones(self):
        c = _Composite()
        triggered = []

        def fake_zone(self, zone):
            triggered.append(zone)

        with mock.patch.object(_Composite, "_on_zone_triggered", fake_zone):
            from meapet.desktop.interaction import PetInteractionMixin
            PetInteractionMixin._on_lower_left_patted(c)
            PetInteractionMixin._on_lower_right_patted(c)
        self.assertEqual(triggered, ["lower_left", "lower_right"])

    def test_speak_and_show_tolerates_missing_tts(self):
        c = _Composite()
        c.tts = None
        c._speak_and_show("你好喵", 1000, "happy")
        self.assertTrue(c.bubble.texts)

    def test_generated_interaction_waits_for_audio_and_outlives_it(self):
        import meapet.desktop.chat_flow as chat_flow

        class FakeWorker:
            done = False

            def __init__(self, *_args, **_kwargs):
                pass

            def start(self):
                pass

        c = _Composite()
        with mock.patch.object(chat_flow, "TTSWorker", FakeWorker):
            c._speak_and_show("稍等一下喵", 1000, "neutral")

        self.assertEqual(c.bubble.texts, [])

        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "generated.wav"
            wav_path.write_bytes(b"RIFF" + b"\x00" * 40)
            c._get_wav_duration_ms = lambda _path: 8_000
            chat_flow.PetChatFlowMixin._on_speak_audio_ready(c, str(wav_path))

        self.assertEqual(c.bubble.texts[-1][1], 8_500)
        self.assertEqual(c._played, [str(wav_path)])

    def test_speak_and_show_rejects_string_self_pattern(self):
        """文档化错误形态：若误标 staticmethod，self 会变成 str。"""
        from meapet.desktop.chat_flow import PetChatFlowMixin

        c = _Composite()
        self.assertTrue(hasattr(c, "tts"))
        self.assertFalse(isinstance(c, str))
        params = list(inspect.signature(PetChatFlowMixin._speak_and_show).parameters)
        self.assertEqual(params[0], "self")


class TestFormattedChatToTtsFlow(unittest.TestCase):
    def test_chat_reply_waits_for_audio_before_showing_bubble(self):
        from PyQt5.QtCore import QTimer

        import meapet.desktop.chat_flow as chat_flow

        captured = {"events": []}

        class Engine:
            _MOOD_TAGS = {"neutral", "shy"}

            @staticmethod
            def take_voice_text():
                return "べ、別に待ってないにゃ"

            @staticmethod
            def take_tts_style():
                return "保持参考音色。情绪：害羞。"

        class FakeWorker:
            def __init__(self, tts, text, mood="neutral", style=""):
                captured.update(tts=tts, text=text, mood=mood, style=style)
                self.done = False

            def start(self):
                captured["started"] = True

        class FakeTTS:
            enabled = True

        class Host(chat_flow.PetChatFlowMixin):
            chat_engine = Engine()
            tts = FakeTTS()
            _awaiting_reply = True
            config = {
                "bubble_duration_ms": {"reply": 1000},
                "tts": {"sync_with_audio": True},
            }

            @staticmethod
            def _detect_mood(_text):
                raise AssertionError("模型 mood 有效时不应重新猜测")

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                captured.update(
                    display=text,
                    display_mood=mood,
                    display_duration_ms=duration_ms,
                )
                captured["events"].append("bubble")

            @staticmethod
            def _get_wav_duration_ms(_path):
                return 1200

            @staticmethod
            def _play_audio(path, audio_type="sfx", **kwargs):
                captured["played"] = path
                captured["events"].append("audio")

            @staticmethod
            def _ensure_tts_poll():
                captured["polling"] = True

            @staticmethod
            def _do_memory_ops(_reply, _mood):
                pass

        host = Host()
        with (
            mock.patch.object(chat_flow, "TTSWorker", FakeWorker),
            mock.patch.object(QTimer, "singleShot"),
        ):
            chat_flow.PetChatFlowMixin._on_chat_done(
                host,
                "才没有等你回来喵",
                "shy",
            )

        self.assertNotIn("display", captured)
        self.assertTrue(host._awaiting_reply)
        self.assertEqual(captured["text"], "べ、別に待ってないにゃ")
        self.assertEqual(captured["mood"], "shy")
        self.assertEqual(captured["style"], "保持参考音色。情绪：害羞。")
        self.assertTrue(captured["started"])
        self.assertTrue(captured["polling"])

        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "reply.wav"
            wav_path.write_bytes(b"RIFF" + b"\x00" * 40)
            chat_flow.PetChatFlowMixin._on_tts_audio(
                host,
                f"{wav_path}|jp",
            )

        self.assertEqual(captured["display"], "才没有等你回来喵")
        self.assertEqual(captured["display_mood"], "shy")
        self.assertEqual(captured["display_duration_ms"], 1700)
        self.assertEqual(captured["events"], ["bubble", "audio"])
        self.assertFalse(host._awaiting_reply)

    def test_chat_bubble_outlives_audio_even_when_legacy_sync_flag_is_false(self):
        import meapet.desktop.chat_flow as chat_flow

        captured = {"events": []}

        class Host(chat_flow.PetChatFlowMixin):
            _awaiting_reply = True
            _pending_chat_reply = ("这是一段很长的语音喵", "neutral")
            _pending_chat_context = None
            config = {
                "bubble_duration_ms": {"reply": 3000},
                "tts": {"sync_with_audio": False},
            }

            @staticmethod
            def _get_wav_duration_ms(_path):
                return 12_000

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                captured["bubble"] = (text, mood, duration_ms)
                captured["events"].append("bubble")

            @staticmethod
            def _play_audio(path, audio_type="sfx", **kwargs):
                captured["audio"] = path
                captured["events"].append("audio")

        host = Host()
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "reply.wav"
            wav_path.write_bytes(b"RIFF" + b"\x00" * 40)
            host._complete_pending_chat_reply(str(wav_path))

        self.assertEqual(captured["bubble"][2], 12_500)
        self.assertEqual(captured["events"], ["bubble", "audio"])

    def test_watcher_bubble_outlives_audio_even_when_legacy_flag_is_false(self):
        from meapet.desktop.watch_ctrl import PetWatcherMixin

        captured = {}

        class Host(PetWatcherMixin):
            _awaiting_reply = True
            config = {
                "bubble_duration_ms": {"watch": 3000},
                "tts": {"sync_with_audio": False},
            }

            @staticmethod
            def _get_wav_duration_ms(_path):
                return 12_000

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                captured["bubble"] = (text, mood, duration_ms)

            @staticmethod
            def _play_audio(path, audio_type="sfx", **kwargs):
                captured["audio"] = path

            @staticmethod
            def _start_watcher_timer():
                pass

        host = Host()
        with tempfile.TemporaryDirectory() as td:
            wav_path = Path(td) / "watch.wav"
            wav_path.write_bytes(b"RIFF" + b"\x00" * 40)
            host._on_watch_tts_and_show(
                f"{wav_path}|jp",
                "屏幕回复喵",
                "neutral",
            )

        self.assertEqual(captured["bubble"][2], 12_500)
        self.assertEqual(captured["audio"], str(wav_path))

    def test_audio_synchronization_is_mandatory_in_normalized_config(self):
        from meapet.config.store import normalize_config

        config = normalize_config({"tts": {"sync_with_audio": False}})

        self.assertTrue(config["tts"]["sync_with_audio"])

    def test_chat_reply_falls_back_to_text_when_tts_returns_no_audio(self):
        import meapet.desktop.chat_flow as chat_flow

        displayed = []

        class DoneWorker:
            done = True

            @staticmethod
            def get_result():
                return None

        class Host(chat_flow.PetChatFlowMixin):
            _awaiting_reply = True
            _pending_chat_reply = ("语音失败也要显示喵", "neutral")
            _tts_worker = DoneWorker()
            config = {
                "bubble_duration_ms": {"reply": 3000},
                "tts": {"sync_with_audio": False},
            }

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                displayed.append((text, mood, duration_ms))

        host = Host()
        chat_flow.PetChatFlowMixin._poll_tts(host)

        self.assertEqual(displayed, [("语音失败也要显示喵", "neutral", None)])
        self.assertFalse(host._awaiting_reply)

    def test_chat_reply_shows_immediately_when_tts_is_disabled(self):
        from PyQt5.QtCore import QTimer

        import meapet.desktop.chat_flow as chat_flow

        displayed = []

        class Engine:
            _MOOD_TAGS = {"neutral"}

            @staticmethod
            def take_voice_text():
                return ""

            @staticmethod
            def take_tts_style():
                return ""

        class DisabledTTS:
            enabled = False

        class Host(chat_flow.PetChatFlowMixin):
            chat_engine = Engine()
            tts = DisabledTTS()
            _awaiting_reply = True

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                displayed.append((text, mood, duration_ms))

            @staticmethod
            def _detect_mood(_text):
                return "neutral"

            @staticmethod
            def _do_memory_ops(_reply, _mood):
                pass

        host = Host()
        with (
            mock.patch.object(
                chat_flow,
                "TTSWorker",
                side_effect=AssertionError("TTS 关闭时不应创建 worker"),
            ),
            mock.patch.object(QTimer, "singleShot"),
        ):
            chat_flow.PetChatFlowMixin._on_chat_done(
                host,
                "这次只显示文字喵",
                "neutral",
            )

        self.assertEqual(displayed, [("这次只显示文字喵", "neutral", None)])
        self.assertFalse(host._awaiting_reply)

    def test_chat_reply_falls_back_when_tts_worker_cannot_start(self):
        from PyQt5.QtCore import QTimer

        import meapet.desktop.chat_flow as chat_flow

        displayed = []

        class Engine:
            _MOOD_TAGS = {"neutral"}

            @staticmethod
            def take_voice_text():
                return ""

            @staticmethod
            def take_tts_style():
                return ""

        class EnabledTTS:
            enabled = True

        class BrokenWorker:
            def __init__(self, *_args, **_kwargs):
                pass

            @staticmethod
            def start():
                raise RuntimeError("worker start failed")

        class Host(chat_flow.PetChatFlowMixin):
            chat_engine = Engine()
            tts = EnabledTTS()
            _awaiting_reply = True

            @staticmethod
            def show_reply(text, mood, duration_ms=None):
                displayed.append((text, mood, duration_ms))

            @staticmethod
            def _detect_mood(_text):
                return "neutral"

            @staticmethod
            def _ensure_tts_poll():
                raise AssertionError("启动失败时不应轮询")

            @staticmethod
            def _do_memory_ops(_reply, _mood):
                pass

        host = Host()
        with (
            mock.patch.object(chat_flow, "TTSWorker", BrokenWorker),
            mock.patch.object(QTimer, "singleShot"),
        ):
            chat_flow.PetChatFlowMixin._on_chat_done(
                host,
                "启动失败也要显示喵",
                "neutral",
            )

        self.assertEqual(displayed, [("启动失败也要显示喵", "neutral", None)])
        self.assertFalse(host._awaiting_reply)


class TestRequiredSurfaceOnMeaPetSource(unittest.TestCase):
    def test_meapet_inherits_all_mixins(self):
        text = (ROOT / "meapet" / "desktop" / "app.py").read_text(encoding="utf-8")
        self.assertIn("PetAudioMixin", text)
        self.assertIn("PetWatcherMixin", text)
        self.assertIn("PetChatFlowMixin", text)
        self.assertIn("PetInteractionMixin", text)
        self.assertIn("PetWindowChromeMixin", text)
        self.assertIn("PetRenderHostMixin", text)
        self.assertIn("PetConfigBridgeMixin", text)
        self.assertIn("class MeaPet(", text)

    def test_interaction_depends_on_chat_flow_show_reply(self):
        """interaction 的 _on_zone_triggered 调用 show_reply（来自 chat_flow）。"""
        from meapet.desktop.chat_flow import PetChatFlowMixin
        from meapet.desktop.interaction import PetInteractionMixin

        # show_reply 必须由 chat_flow 提供且非 static
        self.assertTrue(hasattr(PetChatFlowMixin, "show_reply"))
        src_interaction = inspect.getsource(PetInteractionMixin._on_zone_triggered)
        # interaction 通过 self.show_reply 调用 chat_flow 的方法
        self.assertIn("show_reply", src_interaction)

    def test_interaction_zone_triggered_calls_record_interaction(self):
        """_on_zone_triggered 调用 _record_interaction。"""
        from meapet.desktop.interaction import PetInteractionMixin

        src = inspect.getsource(PetInteractionMixin._on_zone_triggered)
        self.assertIn("_record_interaction", src)


if __name__ == "__main__":
    unittest.main()

