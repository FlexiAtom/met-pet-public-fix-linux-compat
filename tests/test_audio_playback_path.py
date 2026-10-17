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

    def test_reports_the_working_binding(self) -> None:
        with mock.patch("importlib.import_module", return_value=types.ModuleType("x")):
            text = self._cell()
        self.assertIn("Qt 那一路可用", text)
        self.assertIn("音量档生效", text)


if __name__ == "__main__":
    unittest.main()
