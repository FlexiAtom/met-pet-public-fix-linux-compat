"""开发者选项 —— 解锁手感、五项入口的接线，与「只写本地、不带密钥」这两条出口红线。

人工 2026-10-06 点的口径（逐字见 `~/.Athena/projects/meapet/working/menu-settings-consolidation-and-dev-options.md`）：
解锁靠「10 秒内点 5 次版本号」，**不跨启动、不落配置键**；五项全本地；导出配置须过
`scrub_secrets`；日志导出打包成 zip 落 `exports/`。

这里全部用假 host（`QWidget` + mixin，与 `MeaPet` 同构），不碰真麦克风、不等真合成：
`QTimer`、`list_input_devices`、数据目录都遮掉——要钉的是接线与红线，不是外设在场。
"""
import json
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

from meapet.desktop.dev_options import (  # noqa: E402
    DATA_LOG_FILES,
    DEV_MENU_LABELS,
    DEV_UNLOCK_CLICKS,
    DEV_UNLOCK_WINDOW_S,
    DEV_UNLOCKED_HINT,
    DEV_VOICE_LOAD_WAIT_S,
    DEV_VOICE_RESULT_WAIT_S,
    DEV_VOICE_SECONDS,
    EXPORTS_DIR,
    PetDevOptionsMixin,
    dev_unlock_hint,
    version_label_text,
)
from meapet.tts.diagnostics import (  # noqa: E402
    SPEECH_TEST_LANGUAGES,
    SPEECH_TEST_SENTENCES,
)

_app = QApplication.instance() or QApplication(sys.argv)


class DevHost(QWidget, PetDevOptionsMixin):
    """只带本 mixin 的假宿主：产品码直取的方法都在这儿实录。"""

    def __init__(self, config=None):
        super().__init__()
        self.config = {"voice_input": {"enabled": False}} if config is None else config
        self.bubbles = []
        self.saved = []
        self.spoken = []
        self.started_voice_cfg = []
        self.speech_forks = []

    def _show_bubble(self, text, mood=None, **_kw):
        self.bubbles.append(text)

    def _save_config(self, *_a, **_k):
        self.saved.append(True)

    def _speak_and_show(self, text, duration_ms, mood="neutral", tts=None):
        self.spoken.append((text, duration_ms, mood, tts))

    def _dev_tts_for_engine(self, engine):
        """真 MeaTTS 的构造不在这里验：把这一个边界顶成假对象，只看接线。"""
        self.speech_forks.append(engine)
        return getattr(self, "tts", None)

    def _start_voice_engine(self, cfg=None):
        self.started_voice_cfg.append(cfg)
        self._voice_engine = FakeEngine()


class FakeSignal:
    """`pyqtSignal` 的最小形状：这里只验接线与读数，不验 Qt 的线程投递。"""

    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, *args):
        for slot in self.slots:
            slot(*args)


class FakeEngine:
    def __init__(self, running=False):
        self.running = running
        self.toggles = 0
        self.recording_started = FakeSignal()
        self.recording_stopped = FakeSignal()
        self.result_ready = FakeSignal()
        self.error = FakeSignal()

    def isRunning(self):
        return self.running

    def toggle(self):
        self.toggles += 1
        self.running = not self.running


class FakeQTimer:
    """`QTimer.singleShot` 遮掉：不真等 4 秒，只看有没有把停录排上。"""

    calls = []

    @staticmethod
    def singleShot(ms, slot):
        FakeQTimer.calls.append((ms, slot))


class UnlockTests(unittest.TestCase):
    def test_five_clicks_inside_the_window_unlock(self) -> None:
        host = DevHost()
        for _ in range(DEV_UNLOCK_CLICKS - 1):
            hint = host._dev_note_version_click()
        self.assertFalse(host._dev_unlocked)
        self.assertEqual(hint, dev_unlock_hint(1))

        self.assertEqual(host._dev_note_version_click(), DEV_UNLOCKED_HINT)
        self.assertTrue(host._dev_unlocked)

    def test_stale_clicks_do_not_count_toward_the_five(self) -> None:
        """窗口外的点击各算各的：4 次陈旧 + 1 次现在 ≠ 解锁。"""
        host = DevHost()
        stale = tuple(
            time.monotonic() - DEV_UNLOCK_WINDOW_S - 1.0 for _ in range(DEV_UNLOCK_CLICKS - 1)
        )
        host._dev_click_times = stale
        hint = host._dev_note_version_click()
        self.assertFalse(host._dev_unlocked, "跨窗口的连点不该解锁")
        self.assertEqual(hint, dev_unlock_hint(DEV_UNLOCK_CLICKS - 1))
        self.assertEqual(len(host._dev_click_times), 1, "窗口外的计数该丢掉，只留这一发")

    def test_unlock_stays_out_of_the_config(self) -> None:
        """彩蛋是本次运行的状态：既不写盘，也不新增配置键。"""
        host = DevHost(config={})
        before = sorted(host.config.keys())
        for _ in range(DEV_UNLOCK_CLICKS):
            host._dev_note_version_click()
        self.assertTrue(host._dev_unlocked)
        self.assertEqual(host.saved, [], "解锁不该触发保存")
        self.assertEqual(sorted(host.config.keys()), before)
        self.assertEqual(host._dev_click_times, (), "解锁后计数清零")

    def test_extra_clicks_after_unlock_are_inert(self) -> None:
        host = DevHost()
        for _ in range(DEV_UNLOCK_CLICKS):
            host._dev_note_version_click()
        self.assertEqual(host._dev_note_version_click(), DEV_UNLOCKED_HINT)

    def test_menu_lists_the_six_entries(self) -> None:
        host = DevHost()
        menu = host._dev_build_menu()
        self.assertEqual(menu.objectName(), "DevOptionsMenu")
        self.assertEqual(
            [action.text() for action in menu.actions()], list(DEV_MENU_LABELS)
        )


def _scheduled(ms: int) -> list:
    """取出按这个时长排上的回调（本文件用假 QTimer，回调由测试自己点火）。"""
    return [slot for recorded_ms, slot in FakeQTimer.calls if recorded_ms == ms]


def _printed(captured) -> str:
    return "\n".join(captured.output)


class VoiceTestTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeQTimer.calls = []
        # QTimer 遮整个测试而不只遮那一下点击：开录之后的两发排期也得落到假表里，
        # 否则测试会把真定时器排进 Qt 事件循环，读到的却是空表。
        patcher = mock.patch("meapet.desktop.dev_options.QTimer", FakeQTimer)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.devices = [(3, "麦克风", 1, True)]
        self.cfg = {"enabled": False, "input_device_index": 3, "precision": "int8"}
        self.host = DevHost(config={"voice_input": dict(self.cfg)})

    def _click(self, host=None) -> DevHost:
        """点一次「语音输入测试」：设备枚举遮掉，只留接线。"""
        host = self.host if host is None else host
        with mock.patch(
            "meapet.voice.engine.list_input_devices", return_value=self.devices
        ):
            host._dev_voice_input_test()
        return host

    def test_no_devices_says_so_and_starts_nothing(self) -> None:
        host = DevHost()
        with mock.patch(
            "meapet.voice.engine.list_input_devices", return_value=[]
        ) as listed:
            host._dev_voice_input_test()
        self.assertEqual(listed.call_count, 1)
        self.assertIn("没枚举到录音设备", host.bubbles[-1])
        self.assertEqual(host.started_voice_cfg, [], "没有设备就不该把引擎建出来")

    def test_it_builds_the_engine_from_config_without_flipping_the_switch(self) -> None:
        """调试入口只借链路：`voice_input.enabled` 是产品开关，不该被它顺手改掉。"""
        host = DevHost(config={"voice_input": self.cfg})
        self._click(host)
        self.assertEqual(host.started_voice_cfg, [self.cfg])
        self.assertEqual(host.config["voice_input"]["enabled"], False)
        self.assertEqual(host._voice_engine.toggles, 1, "建完就该开始录")
        self.assertEqual(host.bubbles[-1], f"开始录音 {DEV_VOICE_SECONDS} 秒，请说话")

    def test_the_four_seconds_start_counting_when_the_mic_really_opens(self) -> None:
        """模型加载跑在录音线程里：倒计时按点击排＝加载多久少录多久，甚至只录到空。

        这一格是 2026-10-14「判不出成功与否」那次的第二个成因，读数里也得看见它。
        """
        host = self._click()
        self.assertEqual(
            _scheduled(DEV_VOICE_SECONDS * 1000), [], "点击那一下不该排 4 秒"
        )
        self.assertEqual(len(_scheduled(int(DEV_VOICE_LOAD_WAIT_S * 1000))), 1)

        host._voice_engine.recording_started.emit()
        self.assertEqual(len(_scheduled(DEV_VOICE_SECONDS * 1000)), 1, "开录才排 4 秒")

    def test_recognition_result_is_printed_to_the_terminal(self) -> None:
        """他要的是终端里能读出「成了、认出了什么」，不是只有气泡那一句。"""
        host = self._click()
        host._voice_engine.recording_started.emit()
        with self.assertLogs("dev", level="INFO") as captured:
            _scheduled(DEV_VOICE_SECONDS * 1000)[0]()
            host._voice_engine.result_ready.emit("今天天气不错")
        printed = _printed(captured)
        self.assertIn("识别到 6 字", printed)
        self.assertIn("「今天天气不错」", printed)
        self.assertIsNone(host._dev_voice_phase, "收了回执就收口，别让超时再补一句")

    def test_silence_after_stop_is_reported_as_no_receipt(self) -> None:
        """产品那条链在空转写时一个信号都不发（`if text: emit`）——只能由超时说出口。"""
        host = self._click()
        host._voice_engine.recording_started.emit()
        _scheduled(DEV_VOICE_SECONDS * 1000)[0]()
        with self.assertLogs("dev", level="INFO") as captured:
            _scheduled(int(DEV_VOICE_RESULT_WAIT_S * 1000))[0]()
        self.assertIn("没有任何回执", _printed(captured))
        self.assertIn("空串", _printed(captured))

    def test_a_receipt_arrived_makes_the_timeout_silent(self) -> None:
        """回执已经印过之后再点一次超时点火：不能追一句"没回执"，那是假读数。"""
        host = self._click()
        host._voice_engine.recording_started.emit()
        _scheduled(DEV_VOICE_SECONDS * 1000)[0]()
        host._voice_engine.result_ready.emit("好")
        with self.assertNoLogs("dev", level="INFO"):
            _scheduled(int(DEV_VOICE_RESULT_WAIT_S * 1000))[0]()

    def test_load_timeout_says_the_recording_never_started(self) -> None:
        host = self._click()
        with self.assertLogs("dev", level="INFO") as captured:
            _scheduled(int(DEV_VOICE_LOAD_WAIT_S * 1000))[0]()
        self.assertIn("内没开始录音", _printed(captured))
        self.assertIsNone(host._dev_voice_phase)

    def test_a_late_start_after_the_load_timeout_does_not_restart_the_window(self) -> None:
        """收口之后模型才加载完：印一句"这会儿才开始"，但不重排 4 秒（那会变成第二次测试）。"""
        host = self._click()
        _scheduled(int(DEV_VOICE_LOAD_WAIT_S * 1000))[0]()
        with self.assertLogs("dev", level="INFO") as captured:
            host._voice_engine.recording_started.emit()
        self.assertIn("这会儿才开始", _printed(captured))
        self.assertEqual(_scheduled(DEV_VOICE_SECONDS * 1000), [])

    def test_engine_error_is_printed_not_only_bubbled(self) -> None:
        host = self._click()
        with self.assertLogs("dev", level="INFO") as captured:
            host._voice_engine.error.emit("sherpa-onnx 未安装，请在配置页下载依赖")
        self.assertIn("链路报错", _printed(captured))
        self.assertIn("sherpa-onnx 未安装", _printed(captured))

    def test_clicking_twice_on_the_same_engine_wires_the_reading_once(self) -> None:
        """复用同一台引擎时重复连接会把每行印两遍——读数一旦翻倍，"几字"这种数就没意义了。"""
        host = self._click()
        engine = host._voice_engine
        self._click(host)
        self.assertEqual(len(engine.result_ready.slots), 1)
        self.assertEqual(len(engine.error.slots), 1)

    def test_long_transcript_is_clipped_but_counted_in_full(self) -> None:
        host = self._click()
        host._voice_engine.recording_started.emit()
        _scheduled(DEV_VOICE_SECONDS * 1000)[0]()
        text = "啊" * 260
        with self.assertLogs("dev", level="INFO") as captured:
            host._voice_engine.result_ready.emit(text)
        self.assertIn("识别到 260 字", _printed(captured))
        self.assertNotIn("啊" * 201, _printed(captured))

    def test_stop_only_toggles_a_running_engine(self) -> None:
        host = DevHost()
        running = FakeEngine(running=True)
        host._voice_engine = running
        host._dev_stop_voice_test()
        self.assertEqual(running.toggles, 1)
        idle = FakeEngine(running=False)
        host._voice_engine = idle
        host._dev_stop_voice_test()
        self.assertEqual(idle.toggles, 0, "没在录就别再 toggle 一次")


class FakeRoute:
    def describe(self):
        return "mode=inprocess reason=configured python=python"


class FakeTTS:
    """真 MeaTTS 的那几个读数字段：本 mixin 只问它们，不问合成本身。"""

    def __init__(
        self, *, enabled=True, healthy=True, voice_lang="jp", engine="gpt_sovits"
    ):
        self.enabled = enabled
        self.engine = engine
        self.voice_lang = voice_lang
        self.python_exe = "/home/x/GPT-SoVITS-v2pro/runtime/bin/python"
        self.output_dir = "/tmp/cache"
        self.gpt_path = "models/GPT_weights/a.ckpt"
        self.sovits_path = "models/SoVITS_weights/a.pth"
        self._healthy = healthy

    def health_check(self):
        return self._healthy

    def _normalize_voice_lang(self, lang=""):
        return lang or self.voice_lang

    def _vits_route(self):
        return FakeRoute()

    def _vits_knobs(self):
        return ("vits_models/G_latest.pth", "vits_models/finetune_speaker.json", "mea")


class SpeechTestTests(unittest.TestCase):
    def test_the_two_speech_entries_are_per_engine_with_three_languages(self) -> None:
        """人工 2026-10-08 按引擎拆两键，2026-10-12 每键再按语言开三档。

        顶层那两个 action 现在只带子菜单，trigger 它不会走到合成——所以断言分两层：
        形状（引擎 × 语言）与接线（点某一档就把活交给那一个引擎）。
        """
        host = DevHost()
        host.tts = FakeTTS()
        menu = host._dev_build_menu()
        labels = [action.text() for action in menu.actions()]
        self.assertEqual(
            labels[1:3],
            ["朗读测试（GPT-SoVITS）", "朗读测试（VITS）"],
            f"两条朗读测试该按这个顺序排在第 2、3 位，实际菜单是 {labels}",
        )
        for speech_action, engine in zip(menu.actions()[1:3], ("gpt_sovits", "vits")):
            submenu = speech_action.menu()
            self.assertEqual(submenu.objectName(), f"DevSpeechTestMenu::{engine}")
            self.assertEqual(
                [item.text() for item in submenu.actions()],
                [text for text, _code in SPEECH_TEST_LANGUAGES],
                "档位与顺序只认 `SPEECH_TEST_LANGUAGES`，不在菜单侧另立一份",
            )
        host.speech_forks.clear()
        menu.actions()[1].menu().actions()[0].trigger()
        menu.actions()[2].menu().actions()[2].trigger()
        self.assertEqual(
            host.speech_forks,
            ["gpt_sovits", "vits"],
            "子菜单里点下去的那一档，得交给它所属的那个引擎",
        )

    def test_every_language_tier_hands_over_that_languages_sentence(self) -> None:
        """中文/英文两档此前**没有入口**：取句跟着配置 `tts.voice_lang` 走（他这台是 jp）。

        人工 2026-10-12T15:34:53「需要在开发者选项加中文和英文的合成（GSV和VITS都需要，
        目前都是日语）」——两引擎 × 三档，逐格钉"点哪档念哪门语言"。
        """
        for engine in ("gpt_sovits", "vits"):
            for _label, code in SPEECH_TEST_LANGUAGES:
                with self.subTest(engine=engine, code=code):
                    host = DevHost()
                    host.tts = FakeTTS(voice_lang="jp")
                    host._dev_speech_test(engine, code)
                    self.assertEqual(
                        host.spoken[0][0],
                        SPEECH_TEST_SENTENCES[code],
                        f"{engine} 点 {code} 那档，交给链路的该是那门语言的句子",
                    )
                    self.assertIs(host.spoken[0][3], host.tts)

    def test_the_bubble_says_which_language_it_is_about_to_speak(self) -> None:
        """他要确认的是"中文这档真念中文"，所以气泡里得有语言，不能只有引擎名。"""
        host = DevHost()
        host.tts = FakeTTS()
        host._dev_speech_test("vits", "en")
        self.assertIn("英文", " ".join(host.bubbles))

    def test_no_language_still_follows_the_configured_voice_lang(self) -> None:
        """不传语言 = 改前那格：回落到 `speak()` 自己算出的目标语言，行为逐字不变。"""
        host = DevHost()
        host.tts = FakeTTS(voice_lang="jp")
        host._dev_speech_test("gpt_sovits")
        self.assertEqual(host.spoken[0][0], SPEECH_TEST_SENTENCES["jp"])

        other = DevHost()
        other.tts = FakeTTS(voice_lang="zh")
        other._dev_speech_test("vits")
        self.assertEqual(other.spoken[0][0], SPEECH_TEST_SENTENCES["zh"])

    def test_it_reuses_the_real_speak_chain(self) -> None:
        host = DevHost()
        host.tts = FakeTTS(voice_lang="jp")
        host._dev_speech_test("gpt_sovits")
        text, duration, _mood, spoken_tts = host.spoken[0]
        self.assertIn("テスト", text, "jp 声线该拿到 jp 那句")
        self.assertGreaterEqual(duration, 2000)
        self.assertIs(spoken_tts, host.tts, "该把测试那份对象交给既有链路")

    def test_product_tts_object_is_not_the_one_used(self) -> None:
        """红线：测试不许顺手改产品那格——交给链路的是 fork 出来的对象。"""
        host = DevHost()
        product = FakeTTS(engine="gpt_sovits")
        forked = FakeTTS(engine="vits")
        host.tts = product
        host._dev_tts_for_engine = lambda engine: forked
        host._dev_speech_test("vits")
        self.assertIs(host.spoken[0][3], forked)
        self.assertEqual(product.engine, "gpt_sovits", "产品那格不该被改动")

    def test_the_fork_pins_the_engine_and_clears_the_legacy_vits_flag(self) -> None:
        """旧键 `tts.vits_mode` 单独也能把朗读领去 VITS ⇒ 测 GSV 那个键必须把它一起钉掉。"""
        from meapet.desktop.dev_options import PetDevOptionsMixin

        host = DevHost(
            config={
                "tts": {
                    "enabled": True,
                    "engine": "vits",
                    "vits_mode": True,
                    "voice_lang": "jp",
                }
            }
        )
        with mock.patch("meapet.tts.service.MeaTTS") as built:
            PetDevOptionsMixin._dev_tts_for_engine(host, "gpt_sovits")
        cfg = built.call_args[0][0]
        self.assertEqual(cfg["tts"]["engine"], "gpt_sovits")
        self.assertNotIn("vits_mode", cfg["tts"], "旧键得一起清掉")
        self.assertEqual(cfg["tts"]["voice_lang"], "jp", "其余配置该原样带过去")

    def test_broken_prerequisites_say_which_one_instead_of_looking_like_success(self) -> None:
        """前置不齐时那条链回退成文字气泡，与"成功但没配音"同形——必须把判据写进气泡。"""
        host = DevHost()
        host.tts = FakeTTS(healthy=False)
        host._dev_speech_test("gpt_sovits")
        self.assertEqual(host.spoken, [], "前置不齐不该把活交给注定回退的那条链")
        bubble = " ".join(host.bubbles)
        self.assertIn("朗读不会出声", bubble)
        self.assertIn("engine=gpt_sovits", bubble)
        self.assertIn("python=python", bubble)
        self.assertIn("GPT=models/GPT_weights/a.ckpt", bubble)
        self.assertIn("前置=不齐", bubble)

    def test_vits_button_reports_the_vits_prerequisites_not_gsv_ones(self) -> None:
        """测 VITS 时打 GPT/SoVITS 那两条权重路径是把人往错的闸门上引——各自的判据各自写。"""
        host = DevHost()
        host.tts = FakeTTS(healthy=False, engine="vits")
        host._dev_speech_test("vits")
        bubble = " ".join(host.bubbles)
        self.assertIn("mode=inprocess", bubble)
        self.assertIn("model=vits_models/G_latest.pth", bubble)
        self.assertNotIn("GPT=models/GPT_weights", bubble)
        self.assertNotIn("SoVITS=", bubble)

    def test_a_pointer_weight_is_named_as_a_pointer(self) -> None:
        """"前置=不齐"分不出**没水化**和**文件坏了**——两种处置不同，判据要写出来。"""
        from meapet.tts.diagnostics import artifact_label

        with tempfile.TemporaryDirectory() as td:
            pointer = Path(td) / "G_latest.pth"
            pointer.write_bytes(
                b"version https://git-lfs.github.com/spec/v1\n"
                b"oid sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
                b"size 123\n"
            )
            self.assertIn("Git LFS 指针，未水化", artifact_label(str(pointer)))
            real = Path(td) / "ok.pth"
            real.write_bytes(b"not a pointer at all")
            self.assertEqual(artifact_label(str(real)), str(real))
            self.assertEqual(artifact_label(""), "", "空路径不该被写成指针")

    def test_disabled_tts_also_reports_instead_of_falling_back(self) -> None:
        host = DevHost()
        host.tts = FakeTTS(enabled=False)
        host._dev_speech_test("gpt_sovits")
        self.assertEqual(host.spoken, [])
        self.assertIn("enabled=False", " ".join(host.bubbles))

    def test_a_tts_that_cannot_be_built_says_so_instead_of_raising(self) -> None:
        """建不出测试对象也是一种坏法：写出来，别让人对着一个没反应的菜单项猜。"""
        host = DevHost()

        def boom(_engine):
            raise RuntimeError("配置坏在这格")

        host._dev_tts_for_engine = boom
        host._dev_speech_test("gpt_sovits")
        self.assertEqual(host.spoken, [])
        bubble = " ".join(host.bubbles)
        self.assertIn("朗读不会出声", bubble)
        self.assertIn("RuntimeError", bubble)


class ScreenInfoTests(unittest.TestCase):
    def test_it_never_raises_when_geometry_is_unreachable(self) -> None:
        """假 host 没有 `_layer_geometry`，那一段该被兜住并说明取不到，而不是炸应用。"""
        host = DevHost(config={"fidus": {"enabled": False}})
        host._dev_print_screen_info()
        self.assertIn("fidus 屏幕信息已打到日志", host.bubbles[-1])


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / "logs"
        self.logs.mkdir()

    def _patch_dirs(self):
        return (
            mock.patch("meapet.paths.get_data_dir", return_value=str(self.root)),
            mock.patch("meapet.log.LOG_DIR", str(self.logs)),
        )

    def test_config_export_has_no_secrets(self) -> None:
        config = {
            "llm": {
                "api_key": "sk-LLM-SECRET",
                "backend": "ollama",
                "direct": {"api_key": "sk-DIRECT-SECRET"},
            },
            "tts": {"api_key": "sk-TTS-SECRET", "engine": "gpt-sovits"},
            "vision": {"api_key": "sk-VISION-SECRET"},
            "agent_control": {"auth_token": "TOKEN-SECRET"},
            "voice_input": {"api_key": "sk-WHISPER-SECRET", "enabled": False},
        }
        host = DevHost(config=config)
        p1, p2 = self._patch_dirs()
        with p1, p2:
            host._dev_export_config()

        exports = list((self.root / EXPORTS_DIR).glob("meapet-config-*.json"))
        self.assertEqual(len(exports), 1, f"导出应落在数据目录的 exports/：{exports}")
        raw = exports[0].read_text(encoding="utf-8")
        for secret in ("sk-LLM-SECRET", "sk-DIRECT-SECRET", "sk-TTS-SECRET",
                       "sk-VISION-SECRET", "TOKEN-SECRET", "sk-WHISPER-SECRET"):
            self.assertNotIn(secret, raw, f"{secret} 不该出现在导出里")
        written = json.loads(raw)
        self.assertEqual(written["llm"]["api_key"], "")
        self.assertNotIn("api_key", written["voice_input"], "whisper 那键整键去掉")
        # 非密字段照常保留，导出才有调试价值
        self.assertEqual(written["llm"]["backend"], "ollama")
        self.assertEqual(written["tts"]["engine"], "gpt-sovits")
        self.assertEqual(host.saved, [], "导出不该写回配置")
        self.assertEqual(config["llm"]["api_key"], "sk-LLM-SECRET", "内存里的配置不受影响")

    def test_log_export_zips_with_summary_and_no_paths_or_secrets(self) -> None:
        (self.logs / "app.log").write_text("一行日志\n", encoding="utf-8")
        (self.logs / "子目录").mkdir()
        (self.root / DATA_LOG_FILES[0]).write_text("聊天错误\n", encoding="utf-8")
        host = DevHost(config={"llm": {"api_key": "sk-LLM-SECRET"}, "fidus": {"enabled": True}})
        p1, p2 = self._patch_dirs()
        with p1, p2:
            host._dev_export_logs()

        zips = list((self.root / EXPORTS_DIR).glob("meapet-logs-*.zip"))
        self.assertEqual(len(zips), 1)
        with zipfile.ZipFile(zips[0]) as archive:
            names = archive.namelist()
            self.assertIn("SUMMARY.txt", names)
            self.assertIn("app.log", names)
            self.assertIn(DATA_LOG_FILES[0], names, "数据目录旁那几个日志也该进包")
            self.assertNotIn("子目录", names, "目录不入包")
            self.assertTrue(
                all(not name.startswith("/") and ".." not in name for name in names),
                f"包内只该是相对 basename：{names}",
            )
            summary = archive.read("SUMMARY.txt").decode("utf-8")
        self.assertNotIn("sk-LLM-SECRET", summary, "摘要头只取非密开关")
        self.assertIn("fidus", summary)

    def test_empty_log_dir_says_so_and_writes_nothing(self) -> None:
        host = DevHost()
        p1, p2 = self._patch_dirs()
        with p1, p2:
            host._dev_export_logs()
        self.assertIn("没有日志可导出", host.bubbles[-1])
        self.assertFalse((self.root / EXPORTS_DIR).exists())


class AboutDialogVersionClickTests(unittest.TestCase):
    """彩蛋入口在「关于」里那一行版本号上（人工 2026-10-06 矫正的位置：版本号本来就在那儿）。"""

    def _host_dialog(self):
        from meapet.desktop.dialogs import AboutDialog

        host = DevHost()
        dialog = AboutDialog(host)
        # 对话框是 host 的子级：只留 dialog 的引用的话，host 先被 GC 会把它一起删掉。
        self.host = host
        self.addCleanup(dialog.deleteLater)
        return host, dialog

    def test_the_version_line_is_the_unchanged_one_line_of_text(self) -> None:
        _host, dialog = self._host_dialog()
        self.assertEqual(dialog.version_label.text(), version_label_text())
        self.assertEqual(dialog.dev_hint_label.text(), "", "没点之前不该露出彩蛋的痕迹")

    def test_five_clicks_on_the_version_line_unlock_and_report_in_the_dialog(self) -> None:
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        host, dialog = self._host_dialog()
        for _ in range(DEV_UNLOCK_CLICKS - 1):
            QTest.mouseClick(dialog.version_label, Qt.LeftButton)
        self.assertFalse(host._dev_unlocked)
        self.assertEqual(dialog.dev_hint_label.text(), dev_unlock_hint(1))

        QTest.mouseClick(dialog.version_label, Qt.LeftButton)
        self.assertTrue(host._dev_unlocked)
        self.assertEqual(dialog.dev_hint_label.text(), DEV_UNLOCKED_HINT)

    def test_clicking_the_credits_links_does_not_count_as_a_version_click(self) -> None:
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        host, dialog = self._host_dialog()
        for _ in range(DEV_UNLOCK_CLICKS):
            QTest.mouseClick(dialog.close_button, Qt.LeftButton)
        self.assertFalse(host._dev_unlocked, "只有那一行版本号算数")


if __name__ == "__main__":
    unittest.main()
