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
    EXPORTS_DIR,
    PetDevOptionsMixin,
    dev_unlock_hint,
    version_label_text,
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


class FakeEngine:
    def __init__(self, running=False):
        self.running = running
        self.toggles = 0

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


class VoiceTestTests(unittest.TestCase):
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
        devices = [(3, "麦克风", 1, True)]
        cfg = {"enabled": False, "input_device_index": 3, "precision": "int8"}
        host = DevHost(config={"voice_input": cfg})
        with mock.patch(
            "meapet.voice.engine.list_input_devices", return_value=devices
        ), mock.patch("meapet.desktop.dev_options.QTimer", FakeQTimer):
            FakeQTimer.calls = []
            host._dev_voice_input_test()
        self.assertEqual(host.started_voice_cfg, [cfg])
        self.assertEqual(host.config["voice_input"]["enabled"], False)
        self.assertEqual(host._voice_engine.toggles, 1, "建完就该开始录")
        self.assertEqual(FakeQTimer.calls[0][0], 4000, "录满 4 秒要排上停")

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
    def test_the_two_buttons_fork_to_the_two_engines(self) -> None:
        """人工 2026-10-08：朗读测试按引擎拆两个键——共用一键就只测到配置选中的那一路。"""
        host = DevHost()
        host.tts = FakeTTS()
        menu = host._dev_build_menu()
        labels = [action.text() for action in menu.actions()]
        speech_actions = [
            action for action in menu.actions() if action.text().startswith("朗读测试")
        ]
        self.assertEqual(
            labels[1:3],
            ["朗读测试（GPT-SoVITS）", "朗读测试（VITS）"],
            f"两个朗读测试键该按这个顺序排在第 2、3 位，实际菜单是 {labels}",
        )
        self.assertEqual(len(speech_actions), 2)
        for action in speech_actions:
            action.trigger()
        self.assertEqual(host.speech_forks, ["gpt_sovits", "vits"])

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

    def test_the_sentence_follows_the_configured_voice_language(self) -> None:
        """写死中文句配 jp 声线会被 speak() 里的语言闸门跳过 ⇒ 测试词跟着 voice_lang 走。"""
        from meapet.desktop.dev_options import dev_test_sentence

        self.assertIn("開発者", dev_test_sentence("jp"))
        self.assertIn("开发者选项测试", dev_test_sentence("zh"))
        self.assertIn("Developer", dev_test_sentence("en"))
        # 认不出的语言（含空/`auto`）回到中文那句，而不是抛
        self.assertIn("开发者选项测试", dev_test_sentence("auto"))
        self.assertIn("开发者选项测试", dev_test_sentence(""))

    def test_every_test_sentence_passes_the_language_gate(self) -> None:
        """闸门与测试词是两处代码，这里钉住它们不打架。"""
        from meapet.desktop.dev_options import DEV_TEST_SENTENCES
        from meapet.tts.language_policy import should_skip_tts_due_to_language_mismatch

        for lang, sentence in DEV_TEST_SENTENCES.items():
            with self.subTest(lang=lang):
                self.assertFalse(
                    should_skip_tts_due_to_language_mismatch(sentence, lang),
                    f"{lang} 的测试词被自己的语言闸门跳过了",
                )

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
