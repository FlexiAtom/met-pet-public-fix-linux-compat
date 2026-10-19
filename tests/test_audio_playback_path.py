"""播放那条链的日志语义：「首选不可用」不许写成「播放失败」，且每进程只说一次。

人工 2026-10-10 在他机器上看到 `[audio] Qt 播放失败: libQt5Multimedia.so.5: 无法打开共享目标文件`
而耳朵听到的是正常播放（原话见 `~/.Athena/projects/meapet/voice.md`）。真相是首选那一路的**底层库被裁**、
回退那一路播得好好的——所以这两格必须长成两种样子，不然读日志的人拿到假信息。
"""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meapet.desktop import audio as audio_mod  # noqa: E402


def _fake_wav() -> str:
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    with open(path, "wb") as handle:
        handle.write(b"RIFF" + b"\x00" * 40)
    return path


class _Stub:
    """`_play_audio` 只用到 config 与 self（QMediaPlayer 的父对象位），不必造整个窗口。"""

    config = {"audio": {"tts_volume_percent": 30, "sfx_volume_percent": 20}}

    _play_audio = audio_mod.PetAudioMixin._play_audio
    _on_qt_state_changed = audio_mod.PetAudioMixin._on_qt_state_changed
    _verify_qt_playback = audio_mod.PetAudioMixin._verify_qt_playback
    _fallback_play = audio_mod.PetAudioMixin._fallback_play


class _Sig:
    """够用的 Qt 信号面：`connect` 收槽、`emit` 同步派发。"""

    def __init__(self) -> None:
        self._slots: list = []

    def connect(self, slot) -> None:
        self._slots.append(slot)

    def emit(self, value) -> None:
        for slot in self._slots:
            slot(value)


class _InstantTimer:
    """把 `QTimer.singleShot` 换成同步调用：单测里没有事件循环，真定时器永远不会响。"""

    @staticmethod
    def singleShot(_ms, fn) -> None:
        fn()


class _Base(unittest.TestCase):
    def _start(self, patcher):
        """`patch.dict` 只在 start() 时才真的改字典——忘了 start 就是**假通过**。"""
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher

    def setUp(self) -> None:
        audio_mod._QT_MEDIA_STATE = None
        audio_mod._QT_MEDIA_NOTICED = False
        self.addCleanup(setattr, audio_mod, "_QT_MEDIA_STATE", None)
        self.addCleanup(setattr, audio_mod, "_QT_MEDIA_NOTICED", False)
        self.wav = _fake_wav()
        self.addCleanup(os.unlink, self.wav)
        # 平台钉成 linux：否则在 Windows 上这条会去 os.startfile 真开一个文件
        self._start(mock.patch.object(audio_mod, "sys", types.SimpleNamespace(platform="linux")))

    def _block_qt_multimedia(self) -> None:
        """把绑定钉成"import 就炸"，与那台裁了 Qt5 库的机器同一形状。"""
        self._start(mock.patch.dict(sys.modules, {"PyQt5.QtMultimedia": None}))

    def _play_twice(self) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            _Stub()._play_audio(self.wav, audio_type="tts")
            _Stub()._play_audio(self.wav, audio_type="sfx")
        return buf.getvalue()


class QtUnavailableTest(_Base):
    """那台裁掉 libQt5Multimedia.so.5 的机器：import 必炸，但回退照常出声。"""

    def setUp(self) -> None:
        super().setUp()
        self._block_qt_multimedia()
        # 别真去 spawn 一个 aplay：这条测的是日志形状，不是出声
        self._start(mock.patch.object(subprocess, "Popen"))

    def test_unavailable_is_not_called_a_playback_failure(self) -> None:
        out = self._play_twice()
        self.assertIn("首选 Qt 播放不可用", out)
        self.assertNotIn("Qt 播放失败", out)
        self.assertNotIn("最终播放失败", out)
        self.assertIn("aplay 播放", out)

    def test_notice_said_once_not_per_play(self) -> None:
        out = self._play_twice()
        self.assertEqual(out.count("首选 Qt 播放不可用"), 1, "两次播放刷了两行就是噪音")
        self.assertEqual(out.count("aplay 播放"), 2)

    def test_fallback_says_the_volume_did_not_apply(self) -> None:
        out = self._play_twice()
        self.assertIn("音量档 30% 未生效", out)
        self.assertIn("音量档 20% 未生效", out)


class QtAvailableTest(_Base):
    """库齐的那台机器：走 Qt，音量档真喂进去，也不该出现任何"不可用/失败"。"""

    def setUp(self) -> None:
        super().setUp()
        calls: list[tuple] = []

        class _Player:
            def __init__(self, parent=None):
                calls.append(("init", parent))

            def stop(self):
                calls.append(("stop",))

            def setMedia(self, _content):
                calls.append(("setMedia",))

            def setVolume(self, value):
                calls.append(("setVolume", value))

            def play(self):
                calls.append(("play",))

        fake = types.ModuleType("PyQt5.QtMultimedia")
        fake.QMediaPlayer = _Player
        fake.QMediaContent = lambda _url: "content"
        self._start(mock.patch.dict(sys.modules, {"PyQt5.QtMultimedia": fake}))
        self.calls = calls

    def test_qt_path_reports_play_and_passes_volume(self) -> None:
        out = self._play_twice()
        self.assertIn("Qt 播放:", out)
        self.assertIn("volume=30%", out)
        self.assertNotIn("不可用", out)
        self.assertNotIn("失败", out)
        self.assertIn(("setVolume", 30), self.calls)
        self.assertIn(("setVolume", 20), self.calls)
        self.assertEqual(sum(1 for c in self.calls if c[0] == "play"), 2)

    def test_no_verify_noise_when_the_timer_never_fired(self) -> None:
        """这一档守的是"定时器没跑就别说没播成"：真 QTimer 在没事件循环的单测里不回调。"""
        out = self._play_twice()
        self.assertNotIn("未起播", out)
        self.assertNotIn("aplay", out)


class QtSilentFailureTest(_Base):
    """`play()` 不抛、`error` 信号也不发那一格——冻结态 GStreamer 注册表空就是这个形状。

    人工 2026-10-20 在他机器的打包版上看到 `Error: "" : "无组件“identity”"` 而日志写着
    「Qt 播放」；本机三臂实测（`~/.Athena/projects/meapet/working/frozen-gst-plugin-dir-relocated.md` §2）：
    坏那一路 0/120/250/500/1000/2000 ms 全程 `state=0`、一次 `stateChanged` 都不发，
    健康那一路 `play()` 返回时已是 Playing。所以「有没有进过 Playing」是唯一实读得到的判据。
    """

    def _install_player(self, *, emit_playing: bool, state: int = 0) -> None:
        class _Player:
            def __init__(self, parent=None):
                self.state_changed = None if not emit_playing else _Sig()

            @property
            def stateChanged(self):
                return self.state_changed

            def stop(self):
                pass

            def setMedia(self, _content):
                pass

            def setVolume(self, _value):
                pass

            def play(self):
                if self.state_changed is not None:
                    self.state_changed.emit(audio_mod.QT_PLAYING_STATE)

            def state(self):
                return state

        fake = types.ModuleType("PyQt5.QtMultimedia")
        fake.QMediaPlayer = _Player
        fake.QMediaContent = lambda _url: "content"
        self._start(mock.patch.dict(sys.modules, {"PyQt5.QtMultimedia": fake}))
        self._start(mock.patch.object(audio_mod, "QTimer", _InstantTimer))
        self._start(mock.patch.object(subprocess, "Popen"))

    def test_never_playing_falls_back_and_says_so(self) -> None:
        self._install_player(emit_playing=False)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            _Stub()._play_audio(self.wav, audio_type="tts")
        out = buf.getvalue()
        self.assertIn("Qt 未起播", out)
        self.assertIn("读数 state=0", out)
        self.assertIn("aplay 播放", out)
        self.assertIn("音量档 30% 未生效", out)
        self.assertNotIn("最终播放失败", out)

    def test_seen_playing_does_not_double_play(self) -> None:
        self._install_player(emit_playing=True)
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            _Stub()._play_audio(self.wav, audio_type="tts")
        out = buf.getvalue()
        self.assertIn("Qt 播放:", out)
        self.assertNotIn("未起播", out)
        self.assertNotIn("aplay", out)

    def test_stale_check_of_an_older_play_stays_quiet(self) -> None:
        """换发之后旧的那一发验收不得口，否则会打断正在播的那一发。"""
        self._install_player(emit_playing=False)
        stub = _Stub()
        stub._play_seq = 2
        stub._qt_saw_playing = False
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            stub._verify_qt_playback(1, self.wav, 30)
        self.assertEqual(buf.getvalue(), "")


class FrozenGstPluginPathTest(unittest.TestCase):
    """打包态那一格：GStreamer 用 dladdr 把插件目录算到 `_internal/` 上，得指回真那一个。"""

    def setUp(self) -> None:
        self._orig = os.environ.pop("GST_PLUGIN_SYSTEM_PATH", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        os.environ.pop("GST_PLUGIN_SYSTEM_PATH", None)
        if self._orig is not None:
            os.environ["GST_PLUGIN_SYSTEM_PATH"] = self._orig

    def _run(self, platform: str, frozen: bool, resolved: str) -> None:
        fake_sys = types.SimpleNamespace(platform=platform, frozen=frozen)
        with mock.patch.object(audio_mod, "sys", fake_sys), \
             mock.patch.object(audio_mod, "resolve_gst_plugin_dir", return_value=resolved):
            audio_mod._ensure_gst_plugin_path()

    def test_frozen_linux_points_at_the_real_directory(self) -> None:
        self._run("linux", True, "/usr/lib/gstreamer-1.0")
        self.assertEqual(os.environ.get("GST_PLUGIN_SYSTEM_PATH"), "/usr/lib/gstreamer-1.0")

    def test_source_mode_leaves_the_environment_alone(self) -> None:
        self._run("linux", False, "/usr/lib/gstreamer-1.0")
        self.assertNotIn("GST_PLUGIN_SYSTEM_PATH", os.environ)

    def test_user_set_value_is_not_overwritten(self) -> None:
        os.environ["GST_PLUGIN_SYSTEM_PATH"] = "/home/me/my-plugins"
        self._run("linux", True, "/usr/lib/gstreamer-1.0")
        self.assertEqual(os.environ["GST_PLUGIN_SYSTEM_PATH"], "/home/me/my-plugins")

    def test_other_platforms_are_untouched(self) -> None:
        for plat in ("win32", "darwin"):
            self._run(plat, True, "/usr/lib/gstreamer-1.0")
            self.assertNotIn("GST_PLUGIN_SYSTEM_PATH", os.environ)


class ResolveGstPluginDirTest(unittest.TestCase):
    def test_returns_first_existing_candidate(self) -> None:
        from meapet.utils import resolve_gst_plugin_dir

        listing = {
            "/usr/lib/gstreamer-1.0": [],
            "/usr/lib64/gstreamer-1.0": ["/usr/lib64/gstreamer-1.0"],
            "/usr/local/lib/gstreamer-1.0": [],
            "/usr/lib/*/gstreamer-1.0": [],
            "/lib/*/gstreamer-1.0": [],
        }
        with mock.patch("glob.glob", side_effect=lambda p: listing[p]), \
             mock.patch("os.path.isdir", return_value=True):
            self.assertEqual(resolve_gst_plugin_dir(), "/usr/lib64/gstreamer-1.0")

    def test_empty_when_nothing_exists(self) -> None:
        from meapet.utils import resolve_gst_plugin_dir

        with mock.patch("glob.glob", return_value=[]), \
             mock.patch("os.path.isdir", return_value=True):
            self.assertEqual(resolve_gst_plugin_dir(), "")


class DoctorPlaybackCellTest(unittest.TestCase):
    """诊断面这一格：读数要说清 Qt 起不起得来、回退是哪条、音量档归谁。"""

    def _cell(self) -> str:
        from meapet import doctor

        return doctor._audio_playback({"audio": {"tts_volume_percent": 61, "sfx_volume_percent": 60}})

    def test_reports_the_missing_underlying_library_as_unavailable_not_failure(self) -> None:
        with mock.patch("importlib.import_module", side_effect=ImportError("libQt5Multimedia.so.5: nope")):
            text = self._cell()
        self.assertIn("绑定=在场", text)
        self.assertIn("底层=加载失败", text)
        self.assertIn("音量档 tts=61%", text)

    def test_import_success_is_not_reported_as_playback_working(self) -> None:
        """2026-10-20 那格假证的正主：绑定 import 得逞 ≠ Qt 播得成，措辞不许再写「那一路可用」。"""
        with mock.patch("importlib.import_module", return_value=types.ModuleType("x")):
            text = self._cell()
        self.assertIn("绑定=在场 底层=可加载", text)
        self.assertNotIn("Qt 那一路可用", text)
        self.assertIn("本格不触发播放", text)

    def test_reports_the_gstreamer_plugin_dir_on_linux(self) -> None:
        """这一格单独一条读数：插件目录在哪、找不到就说「建不起管线」，不再由 import 代答。"""
        import meapet.utils as utils_mod

        with mock.patch.object(utils_mod, "resolve_gst_plugin_dir", return_value="/usr/lib/gstreamer-1.0"), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("importlib.util.find_spec", return_value=object()), \
             mock.patch("importlib.import_module", return_value=types.ModuleType("x")):
            text = self._cell()
        self.assertIn("GStreamer 插件目录=/usr/lib/gstreamer-1.0", text)

    def test_says_the_pipeline_cannot_build_when_the_dir_is_missing(self) -> None:
        import meapet.utils as utils_mod

        with mock.patch.object(utils_mod, "resolve_gst_plugin_dir", return_value=""), \
             mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("importlib.util.find_spec", return_value=object()), \
             mock.patch("importlib.import_module", return_value=types.ModuleType("x")):
            text = self._cell()
        self.assertIn("GStreamer 插件目录=找不到", text)


if __name__ == "__main__":
    unittest.main()
