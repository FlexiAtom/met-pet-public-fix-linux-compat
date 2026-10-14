"""PNG 立绘的解码兜底 —— 人工 2026-10-15T02:35 现报「目前PNG模式无法显示立绘」。

本机实测的根因不是选路、不是挂载，而是**解码器本身缺件**：立绘资产 450 枚全是 `.webp`，
而这台 Arch 上 Qt 的 `supportedImageFormats()` 里没有 `webp`，于是
`QPixmap('sprites/mea01A_001.webp').isNull() == True`，`_update_sprite()` 拿到空帧直接 return，
窗口停在默认尺寸（启动日志当时报的是 `size=100x30`）。

这一族钉两面：Qt 解不出时 Pillow 兜底**真把帧接住了**（反证臂），以及 Qt 能解时**不为兜底多付一次拷贝**。
"""

import os

import pytest
from PIL import Image
from PyQt5.QtGui import QPixmap

from meapet.desktop import renderer as renderer_mod
from meapet.desktop.renderer import SpriteRenderer, decode_via_pillow

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPRITE = os.path.join(REPO, "sprites", "mea01A_001.webp")

requires_sprite = pytest.mark.skipif(
    not os.path.exists(SPRITE), reason="立绘资产未在水化状态"
)


def _stub_qt_without_webp(monkeypatch):
    """把模块里的 QPixmap 换成"按路径构造就给一张空的"，等价于本机 Qt 缺 webp 插件那档。"""
    real = QPixmap

    def fake(*args, **kwargs):
        if args and isinstance(args[0], str):
            return real()
        return real(*args, **kwargs)

    fake.fromImage = staticmethod(real.fromImage)
    monkeypatch.setattr(renderer_mod, "QPixmap", fake)
    return real


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        return QPixmap()


def test_pillow_decodes_the_real_sprite():
    pixmap = decode_via_pillow(SPRITE)
    with Image.open(SPRITE) as image:
        width, height = image.size
    assert not pixmap.isNull(), "Pillow 解不出真立绘 ⇒ 兜底这条路本身是断的"
    assert (pixmap.width(), pixmap.height()) == (width, height)


@requires_sprite
def test_fallback_carries_the_frame_when_qt_says_no(monkeypatch, capsys):
    _stub_qt_without_webp(monkeypatch)
    renderer = SpriteRenderer(os.path.join(REPO, "sprites"), "01", "A")

    pixmap = renderer.get_current_pixmap()
    assert not pixmap.isNull(), "Qt 说解不出时应由 Pillow 接住，而不是让屏上什么也没有"
    assert pixmap.hasAlpha(), "兜底那一路要把透明边保住，否则整窗变成一块不透明矩形"

    err = capsys.readouterr().err
    assert err.count("[png] Qt 解不出这张立绘") == 1, "兜底要出声，且只出一次"

    renderer._pixmap_cache.clear()
    renderer.get_current_pixmap()
    assert capsys.readouterr().err.count("[png] Qt 解不出这张立绘") == 0, (
        "清缓存重解码也不该把同一句再喊一遍"
    )


def test_qt_native_path_does_not_pay_for_the_fallback(monkeypatch, tmp_path, capsys):
    png = tmp_path / "mea01A_001.webp"
    Image.new("RGBA", (8, 6), (255, 0, 0, 255)).save(png, format="PNG")

    recorder = _Recorder()
    monkeypatch.setattr(renderer_mod, "decode_via_pillow", recorder)

    renderer = SpriteRenderer(str(tmp_path), "01", "A")
    pixmap = renderer.get_current_pixmap()

    assert not pixmap.isNull(), "Qt 自己能解的那一档，画面照常要有"
    assert recorder.calls == [], "Qt 解得动时不该再走一遍 Pillow（多一次全帧拷贝）"
    assert "[png]" not in capsys.readouterr().err


def test_both_decoders_failing_shouts_once_and_does_not_raise(monkeypatch, tmp_path, capsys):
    broken = tmp_path / "mea01A_001.webp"
    broken.write_bytes(b"not an image at all")
    monkeypatch.chdir(tmp_path)

    renderer = SpriteRenderer(str(tmp_path), "01", "A")
    assert renderer.get_current_pixmap().isNull()
    renderer._pixmap_cache.clear()
    assert renderer.get_current_pixmap().isNull()

    err = capsys.readouterr().err
    assert err.count("都解不出立绘") == 1, "彻底解不出要响亮说一次，但不能每次眨眼喊一遍"
