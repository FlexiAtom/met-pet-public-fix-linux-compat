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
    EXPORTS_DIR,
    VERSION_ACTION_NAME,
    PetDevOptionsMixin,
    version_action_text,
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

    def _show_bubble(self, text, mood=None, **_kw):
        self.bubbles.append(text)

    def _save_config(self, *_a, **_k):
        self.saved.append(True)

    def _speak_and_show(self, text, duration_ms, mood="neutral"):
        self.spoken.append((text, duration_ms, mood))

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
            host._dev_note_version_click()
        self.assertFalse(host._dev_unlocked)
        self.assertEqual(host.bubbles[-1], "再点 1 次解锁开发者选项")

        host._dev_note_version_click()
        self.assertTrue(host._dev_unlocked)
        self.assertIn("已解锁", host.bubbles[-1])

    def test_stale_clicks_do_not_count_toward_the_five(self) -> None:
        """窗口外的点击各算各的：4 次陈旧 + 1 次现在 ≠ 解锁。"""
        host = DevHost()
        stale = tuple(
            time.monotonic() - DEV_UNLOCK_WINDOW_S - 1.0 for _ in range(DEV_UNLOCK_CLICKS - 1)
        )
        host._dev_click_times = stale
        host._dev_note_version_click()
        self.assertFalse(host._dev_unlocked, "跨窗口的连点不该解锁")
        self.assertEqual(host.bubbles[-1], f"再点 {DEV_UNLOCK_CLICKS - 1} 次解锁开发者选项")
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
        host.bubbles.clear()
        host._dev_note_version_click()
        self.assertEqual(host.bubbles, [], "已解锁之后不该再报『再点 N 次』")

    def test_version_action_carries_the_name_the_window_looks_for(self) -> None:
        host = DevHost()
        action = host._dev_build_version_action()
        self.assertEqual(action.objectName(), VERSION_ACTION_NAME)
        self.assertEqual(action.text(), version_action_text())
        self.assertIn("连点", action.toolTip())

    def test_menu_lists_the_five_entries(self) -> None:
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


class SpeechTestTests(unittest.TestCase):
    def test_it_reuses_the_real_speak_chain(self) -> None:
        host = DevHost()
        host._dev_speech_test()
        text, duration, _mood = host.spoken[0]
        self.assertIn("开发者选项测试", text)
        self.assertGreaterEqual(duration, 2000)


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


class MenuWindowVersionTests(unittest.TestCase):
    def test_version_action_does_not_close_the_menu_window(self) -> None:
        from PyQt5.QtWidgets import QMenu

        from meapet.desktop.menu_window import PetMenuWindow

        class CountingWindow(PetMenuWindow):
            def __init__(self, menu):
                super().__init__(menu)
                self.closes = 0

            def close(self):
                self.closes += 1
                return True

        menu = QMenu("根")
        fired = []
        version = menu.addAction(version_action_text())
        version.setObjectName(VERSION_ACTION_NAME)
        version.triggered.connect(lambda: fired.append("version"))
        other = menu.addAction("退出")
        other.triggered.connect(lambda: fired.append("quit"))

        window = CountingWindow(menu)
        self.addCleanup(window.deleteLater)
        window._activate(version)
        self.assertEqual(window.closes, 0, "版本号点了不该关窗——连点 5 次要能在同一轮里做")
        self.assertEqual(fired, ["version"], "版本号的触发是同步的，不排到下一轮")

        window._activate(other)
        self.assertEqual(window.closes, 1, "普通项仍是先关窗")


if __name__ == "__main__":
    unittest.main()
