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

from meapet.desktop.disclaimer import ACQUISITION_CHANNELS, PAID_CHANNEL_NOTICE  # noqa: E402
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


class AcquisitionChannelsTest(unittest.TestCase):
    """关于页「获取渠道」那三条：人工 2026-10-10 点名的原话，一条不多一条不少。

    钉两件事：① 内容逐字（群号是人工 2026-10-10T15:34:59 点名的**审核群**那一个，更早流传的那个
    号他判为过期，四处落点的收敛由下面 `GroupNumberConsistencyTest` 单独钉）；② 版位没坏。把 URL 当可见文本时关于页需要 618 px 高，offscreen
    那块 600 px 的屏会把弹窗夹到 581 而使免责句与上一条**重叠 8 px**（实测），所以两条仓库
    链接按上方致谢的同构形态渲染——URL 在 href 里，不在字面上。
    """

    EXPECTED = (
        ("QQ群“审核群”", "1128485088"),
        ("主干仓库", "https://github.com/suan-11/mea-pet-public"),
        ("Linux、Wayland适配及其他测试功能", "https://github.com/FlexiAtom/met-pet-public-fix-linux-compat"),
    )

    def setUp(self) -> None:
        from meapet.desktop.dialogs import AboutDialog

        self.host = QWidget()
        self.dialog = AboutDialog(self.host)
        self.dialog.show()
        _app.processEvents()
        self.addCleanup(self.dialog.deleteLater)

    def _rows(self) -> list[QLabel]:
        rows = [
            label
            for label in self.dialog.findChildren(QLabel)
            if label.accessibleName().startswith("获取渠道 · ")
        ]
        return sorted(rows, key=lambda w: w.y())

    def test_constant_lists_exactly_the_three_named_channels(self) -> None:
        self.assertEqual(ACQUISITION_CHANNELS, self.EXPECTED)

    def test_every_channel_is_rendered_in_order(self) -> None:
        rows = self._rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row.accessibleName().removeprefix("获取渠道 · ") for row in rows],
            [label for label, _ in self.EXPECTED],
        )

    def test_group_number_is_visible_text_and_the_repos_are_links(self) -> None:
        group, trunk, linux = self._rows()
        self.assertEqual(group.text(), "QQ群“审核群”: 1128485088")
        self.assertTrue(group.textInteractionFlags() & Qt.TextSelectableByMouse, "群号得能选中复制")
        for row, (_, url) in zip((trunk, linux), self.EXPECTED[1:]):
            self.assertIn(f'href="{url}"', row.text())
            self.assertTrue(row.openExternalLinks())

    def test_no_row_is_clipped_or_overlapped(self) -> None:
        rows = self._rows() + [_notice_labels(self.dialog)[0]]
        previous_bottom = None
        for row in rows:
            self.assertGreaterEqual(
                row.height(), row.heightForWidth(row.width()), f"{row.accessibleName()} 高度被压"
            )
            if previous_bottom is not None:
                self.assertGreaterEqual(row.y(), previous_bottom, f"{row.accessibleName()} 与上一条重叠")
            previous_bottom = row.y() + row.height()


class GroupNumberConsistencyTest(unittest.TestCase):
    """群号在四个落点只能有一个号。

    人工 2026-10-10T15:34:59 裁「两个群号合并，1083503687过期」与「README加群说明：QQ/群
    1128485088（备注：来意+使用的系统）」，所以钉两面：新号四面都在、旧号四面都不在。旧号只写在
    这份测试里当反证——它扫的是那四个落点，不扫本文件。
    """

    CURRENT = "1128485088"
    EXPIRED = "1083503687"
    SURFACES = (
        "README.md",
        "README.en.md",
        "Q-A_常见问题解答[可以直接右键使用记事本打开喵].md",
        "meapet/desktop/disclaimer.py",
    )

    def _text(self, name: str) -> str:
        return (ROOT / name).read_text(encoding="utf-8")

    def test_the_current_number_is_the_one_the_interface_shows(self) -> None:
        self.assertEqual(ACQUISITION_CHANNELS[0][1], self.CURRENT)

    def test_the_current_number_is_on_all_four_surfaces(self) -> None:
        for name in self.SURFACES:
            self.assertIn(self.CURRENT, self._text(name), f"{name} 上没有当前群号")

    def test_the_expired_number_is_on_none_of_them(self) -> None:
        for name in self.SURFACES:
            self.assertNotIn(self.EXPIRED, self._text(name), f"{name} 上还挂着已过期群号")

    def test_both_readmes_state_what_to_write_in_the_join_request(self) -> None:
        """他点名的备注要求（来意 + 使用的系统）两份 README 都得在群号那一行里。"""
        for name, required in (
            ("README.md", ("来意", "使用的系统")),
            ("README.en.md", ("purpose", "OS")),
        ):
            line = next(line for line in self._text(name).splitlines() if self.CURRENT in line)
            for word in required:
                self.assertIn(word, line, f"{name} 那行缺 {word}")


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
