"""torchaudio 的 sox 后端在 sox_ng 机器上是 SIGSEGV，两处调用点都得绕开它。

出处：Athena 件 gsv-linux-runtime-support 的真机验收。读数——
``gdb`` 抓到崩溃帧全在 ``torchaudio/lib/libtorchaudio_sox.so``
(``load_audio_file → apply_effects_file → SoxEffectsChain::addOutputBuffer``)，
而 ``ldd`` 把它解析到 ``/usr/lib/libsox.so`` → ``readlink -f`` = ``libsox_ng.so.3.0.0``
（Arch 的 ``sox`` 包已是 sox_ng 的分身，14.8；wheel 是按 14.4.2 的 ABI 编的）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audio_io = _load_module(ROOT / "vits_core" / "audio_io.py", "audio_io_under_test")
gsv_infer = _load_module(ROOT / "meapet" / "tools" / "gsv_infer.py", "gsv_infer_under_test")


class _RecordingTorchaudio:
    """假 torchaudio：记下每次 load 的 kwargs，便于断言回退顺序。"""

    def __init__(self, raise_on_backend=None):
        self.calls = []
        self.raise_on_backend = raise_on_backend

    def load(self, path, **kwargs):
        self.calls.append(kwargs)
        if "backend" in kwargs and self.raise_on_backend is not None:
            raise self.raise_on_backend
        return ("waveform", path)

    def info(self, path, **kwargs):  # pragma: no cover - 只为形状完整
        return ("info", path)

    def save(self, path, *args, **kwargs):  # pragma: no cover
        self.calls.append(kwargs)


def test_load_audio_names_the_soundfile_backend():
    ta = _RecordingTorchaudio()
    got = audio_io.load_audio(
        ta, "a.wav", frame_offset=0, num_frames=-1, normalize=True, channels_first=True
    )
    assert got == ("waveform", "a.wav")
    assert ta.calls == [
        {"backend": "soundfile", "frame_offset": 0, "num_frames": -1,
         "normalize": True, "channels_first": True}
    ]


@pytest.mark.parametrize("exc", [TypeError, ValueError], ids=["old-torchaudio", "no-soundfile"])
def test_load_audio_falls_back_without_the_backend_kwarg(exc):
    ta = _RecordingTorchaudio(raise_on_backend=exc("nope"))
    got = audio_io.load_audio(ta, "a.wav", normalize=True)
    assert got == ("waveform", "a.wav")
    # 第一次带 backend 失败后，回退那一次必须不带——否则原样再撞同一堵墙
    assert ta.calls == [{"backend": "soundfile", "normalize": True}, {"normalize": True}]


def _fake_torchaudio(monkeypatch, backends, *, with_api=True):
    """把假 torchaudio 的私有后端表塞进 sys.modules，返回 (模块, 表本体)。"""
    ta = types.ModuleType("torchaudio")
    ta.load = lambda *a, **k: "sox-load"
    ta.info = lambda *a, **k: "sox-info"
    ta.save = lambda *a, **k: "sox-save"
    package = types.ModuleType("torchaudio._backend")
    utils = types.ModuleType("torchaudio._backend.utils")
    if with_api:
        utils.get_available_backends = lambda: backends
        utils.get_load_func = lambda: (lambda *a, **k: "rebuilt-load")
        utils.get_info_func = lambda: (lambda *a, **k: "rebuilt-info")
        utils.get_save_func = lambda: (lambda *a, **k: "rebuilt-save")
    package.utils = utils
    monkeypatch.setitem(sys.modules, "torchaudio", ta)
    monkeypatch.setitem(sys.modules, "torchaudio._backend", package)
    monkeypatch.setitem(sys.modules, "torchaudio._backend.utils", utils)
    return ta


def _call(windows, backends, monkeypatch):
    ta = _fake_torchaudio(monkeypatch, backends)
    action = gsv_infer.prefer_non_sox_backend(windows=windows)
    return ta, action


def test_sox_stays_put_on_windows(monkeypatch):
    backends = {"sox": object(), "soundfile": object()}
    ta, action = _call(True, backends, monkeypatch)
    assert action == "windows-untouched"
    assert list(backends) == ["sox", "soundfile"]
    assert ta.load() == "sox-load"


def test_sox_is_dropped_and_the_dispatchers_are_rebuilt(monkeypatch):
    backends = {"sox": object(), "soundfile": object()}
    ta, action = _call(False, backends, monkeypatch)
    assert action == "sox-dropped"
    assert "sox" not in backends
    # 光从表里摘掉不够：后端表在 import 期就固化进闭包，必须重建 load/info/save
    assert ta.load() == "rebuilt-load"
    assert ta.info() == "rebuilt-info"
    assert ta.save() == "rebuilt-save"


def test_sox_survives_when_soundfile_is_absent(monkeypatch):
    backends = {"sox": object()}
    ta, action = _call(False, backends, monkeypatch)
    assert action == "no-soundfile"
    assert "sox" in backends
    assert ta.load() == "sox-load"


def test_nothing_to_do_when_sox_was_never_there(monkeypatch):
    backends = {"soundfile": object()}
    _, action = _call(False, backends, monkeypatch)
    assert action == "no-sox"


def test_unrecognised_torchaudio_shape_does_not_raise(monkeypatch):
    backends = {"sox": object(), "soundfile": object()}
    ta = _fake_torchaudio(monkeypatch, backends, with_api=False)
    action = gsv_infer.prefer_non_sox_backend(windows=False)
    assert action == "unknown-api"
    assert "sox" in backends and ta.load() == "sox-load"


def test_no_bare_torchaudio_load_left_in_our_own_code():
    """两处调用点必须经助手：裸 torchaudio.load 在 sox_ng 机器上是段错误。"""
    offenders = []
    for rel in ("vits_core/data_utils.py", "meapet/tools/gsv_infer.py",
                "meapet/tts/engines/vits.py", "meapet/tts/engines/vits_runtime.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if "torchaudio.load(" in line:
                offenders.append(f"{rel}:{line_no}: {line.strip()}")
    assert not offenders, "裸 torchaudio.load（应走 vits_core.audio_io.load_audio）:\n" + "\n".join(
        offenders
    )
