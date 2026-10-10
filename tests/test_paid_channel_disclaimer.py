"""付费渠道免责句：两处界面各有一份，而且说的是**同一句逐字不变**的话。

人工 2026-10-10 的原话（逐字见 `~/.Athena/projects/meapet/voice.md`）要求加载弹窗与关于页都加那一句。
那一句是他的法律口径措辞，所以这里钉三件事：① 文案逐字等于原话（我不许自己润色）；② 两处都真的挂上了；
③ 挂上之后版位没坏——加载弹窗是 `setFixedSize(440, 300)` 的定尺卡片，关于页那句不设宽度约束时
会把弹窗从 360 px 撑到 573 px（实测），这两种崩法都得有断言挡着。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSize, Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from meapet.desktop.disclaimer import PAID_CHANNEL_NOTICE  # noqa: E402
from meapet.desktop.screen_geometry import resize_dialog_to_content  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

# 人工 2026-10-10 那句的逐字形态。改动这里等于改他的表态，不接受。
VERBATIM = (
    "如果您在除了官方Q群、软件维护者或Github获取此软件且是付费获取，"
    "我们将不会解决您关于此软件的问题及因为此软件导致的任何问题"
)


def _notice_labels(widget: QWidget) -> list[QLabel]:
    return [
        label
        for label in widget.findChildren(QLabel)
        if label.accessibleName() == "付费渠道免责提示"
    ]


class WordingTest(unittest.TestCase):
    def test_the_constant_is_the_humans_sentence_verbatim(self) -> None:
        self.assertEqual(PAID_CHANNEL_NOTICE, VERBATIM)

    def test_it_is_one_sentence_without_trailing_punctuation(self) -> None:
        """他原文没有句号，界面也不该替他加；出现第二个逗号说明有人改写了。"""
        self.assertFalse(PAID_CHANNEL_NOTICE.endswith(("。", ".", "！", "？")))
        self.assertEqual(PAID_CHANNEL_NOTICE.count("，"), 1)


class SplashTest(unittest.TestCase):
    """加载弹窗：440×300 定尺卡片，句子是 11 px 两行。"""

    def setUp(self) -> None:
        from meapet.desktop.splash import StartupSplash

        self.splash = StartupSplash()
        self.splash.setFixedSize(440, 300)
        self.splash.show()
        _app.processEvents()
        self.addCleanup(self.splash.deleteLater)

    def test_the_notice_is_on_the_loading_card(self) -> None:
        labels = _notice_labels(self.splash)
        self.assertEqual(len(labels), 1, "加载弹窗上这一句只该有一份")
        self.assertEqual(labels[0].text(), PAID_CHANNEL_NOTICE)

    def test_the_notice_fits_the_fixed_size_card(self) -> None:
        """定尺卡片不会自己长高：句子被挤出卡片时这条会红。"""
        notice = _notice_labels(self.splash)[0]
        card = self.splash.card
        bottom_in_card = notice.y() + card.y() + notice.height()
        self.assertLessEqual(bottom_in_card, card.height(), "卡片只有 300 px 高")
        self.assertGreater(notice.height(), 0)
        self.assertTrue(notice.wordWrap(), "不换行就会把卡片撑破")


class AboutDialogTest(unittest.TestCase):
    """关于页：走 `resize_dialog_to_content`，不设宽度约束就会被撑宽。"""

    def setUp(self) -> None:
        from meapet.desktop.dialogs import AboutDialog

        self.host = QWidget()          # dialog 是 host 的子级，host 得留到断言之后
        self.dialog = AboutDialog(self.host)
        self.dialog.show()
        _app.processEvents()
        self.addCleanup(self.dialog.deleteLater)

    def test_the_notice_is_on_the_about_page(self) -> None:
        labels = _notice_labels(self.dialog)
        self.assertEqual(len(labels), 1, "关于页上这一句只该有一份")
        self.assertEqual(labels[0].text(), PAID_CHANNEL_NOTICE)

    def test_the_notice_does_not_bloat_the_dialog(self) -> None:
        """单行 sizeHint 实测 1040 px；放任就会把关于页从 360 撑到 573。"""
        notice = _notice_labels(self.dialog)[0]
        self.assertTrue(notice.wordWrap())
        grown = resize_dialog_to_content(self.dialog, QSize(360, 220))
        self.assertLessEqual(grown.width(), 400, f"关于页被撑到 {grown.width()} px")
        self.assertGreater(notice.height(), 0)


class BothSurfacesShareOneConstantTest(unittest.TestCase):
    """两处各写一遍字面量，迟早会漂移成两句不同的话。"""

    def test_splash_and_about_render_the_identical_text(self) -> None:
        from meapet.desktop.dialogs import AboutDialog
        from meapet.desktop.splash import StartupSplash

        splash = StartupSplash()
        # dialog 是 host 的子级：不留住 host，它会先被 GC 把 dialog 一起删掉（实测 RuntimeError）
        self.host = QWidget()
        about = AboutDialog(self.host)
        self.addCleanup(splash.deleteLater)
        self.addCleanup(about.deleteLater)
        self.assertEqual(_notice_labels(splash)[0].text(), _notice_labels(about)[0].text())


if __name__ == "__main__":
    unittest.main()
