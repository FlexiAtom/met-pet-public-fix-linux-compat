"""付费渠道免责句与「获取渠道」那三条：界面上两份，措辞逐字不动。

人工 2026-10-10 的原话（逐字见 `~/.Athena/projects/meapet/voice.md`）：13:10:04 要「加载弹窗和关于页
加句"…"」，14:22:02 给关于页那三条渠道，15:57:18 补「加载弹窗在没有配置文件时延迟10秒消失（通过是否
存在配置文件来判断是否是第一次使用）」——那句免责只有首用停留 10 秒才有人读得到，两件事是同一格。
所以这里钉四件事：
① 文案逐字等于原话（我不许自己润色）；② 加载弹窗与关于页**都**挂着，且两处是同一句话；
③ 版位没坏——关于页那句不设宽度约束时会把弹窗从 360 px 撑到 573 px（实测），长 URL 按默认字号
铺成可见文本时又需要 618 px 高而被 600 px 的屏夹出 8 px 重叠（实测），两种崩法都得有断言挡着；
URL 现在仍要在字面上看得见，但走的是次级行 `<small>`＋去 scheme 那一档（实测 387×583）；
④ 那句话在源码里只有**一处**字面量，防止有人复制一份后各自漂移。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSize, Qt  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
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
    """加载弹窗：人工 13:10:04 点名的两处之一。"""

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

    def test_the_card_still_fits_with_it(self) -> None:
        """卡片是定尺 440×300。这句要占 33 px，装不下就得改尺寸或砍文案——先把数钉住。"""
        notice = _notice_labels(self.splash)[0]
        card = self.splash.card
        self.assertEqual(card.height(), 300 - 2 * 12)
        self.assertTrue(notice.wordWrap(), "不折行就会按单行 1040 px 去要宽度")
        self.assertLessEqual(
            notice.y() + card.y() + notice.height(), card.height(),
            f"免责句底部 {notice.y() + card.y() + notice.height()} 出卡片 {card.height()}",
        )


class FirstUseHoldTest(unittest.TestCase):
    """首用（没有自己的 config.json）时卡片停留满时长再关——不然那句照样没人读得到。

    人工 2026-10-10T15:57:18 给的原话是「加载弹窗在没有配置文件时延迟10秒消失（通过是否存在配置文件
    来判断是否是第一次使用）」；16:40 选定的计时口径是"无 config.json ⇒ 显示满 10 秒再关，有配置照旧
    立即关"。这里用 400 ms 代替 10 秒跑同一套时钟，测试不该为等待而慢 10 秒。
    """

    def _shown(self, hold_ms: int):
        from meapet.desktop.splash import StartupSplash

        splash = StartupSplash()
        splash.set_hold_ms(hold_ms)
        splash.show()
        _app.processEvents()
        self.addCleanup(splash.deleteLater)
        return splash

    def test_the_named_hold_is_ten_seconds(self) -> None:
        from meapet.desktop.splash import FIRST_RUN_HOLD_MS

        self.assertEqual(FIRST_RUN_HOLD_MS, 10_000)

    def test_dismiss_waits_out_the_hold(self) -> None:
        splash = self._shown(400)
        splash.dismiss()
        self.assertTrue(splash.isVisible(), "时长没到就关了")
        QTest.qWait(700)
        self.assertFalse(splash.isVisible(), "时长过了还不关")

    def test_dismiss_is_immediate_without_hold(self) -> None:
        splash = self._shown(0)
        splash.dismiss()
        self.assertFalse(splash.isVisible(), "有配置时不该多等——他只要首用那一次")

    def test_first_run_is_read_off_the_startup_config_path(self) -> None:
        """`main()` 那句判据本身：落到 example 才算首用。

        他给的是「通过是否存在配置文件来判断是否是第一次使用」，而启动路径已经只在没有
        config.json 时才回落到 example——这条把那层等价关系钉住，换人重写判据时会亮。
        """
        import tempfile

        from meapet.config.store import resolve_startup_config_path

        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(
                os.path.basename(resolve_startup_config_path(root)),
                "config.example.json",
                "没有 config.json 时不该报成「已经配过」",
            )
            Path(root, "config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(
                os.path.basename(resolve_startup_config_path(root)),
                "config.json",
                "有 config.json 还判成首用，每次启动都要多等 10 秒",
            )


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
        """免责句占多行、但不许撑宽——四条各挡一种崩法，都不写死绝对像素。

        旧判据是一条 `grown.width() <= 400`，它标定的是**本机字体**（Noto Sans CJK SC 12pt 实测
        387 px），runner 的回退字体量到 407 就当场红了（2026-10-17 CI）。同一份版式把
        `QT_FONT_DPI` 抬到 144 本机也是 541 ⇒ 那条红是字体误报，不是有人撑宽了弹窗。反过来拿
        实测矩阵量它挡不挡得住真回归：关掉换行（`setWordWrap(False)`）、撤掉 `dialogs.py:1259`
        那枚 `setMaximumWidth(300)` 各都**不红**——宽度那格现在本来就是被那枚上限夹住的，与换行无关。
        所以按判据拆成四条，每条各自有名有姓的对手（完整矩阵见该案文件）：

        - `wordWrap()` 为真：挡"换行被关"。
        - 换行后的高度 > 同一句关掉换行的高度：挡"换行没生效"（比值随字体同步缩放，换机器不翻）。
        - 摘掉免责句前后宽度相等：挡"撑宽"——实测只有**同时**关换行又撤上限才触发（387 → 784）。
        - 宽 >= 360：设计基准还在，前一条才不会因为两边一起塌掉而自证成立。
        """
        notice = _notice_labels(self.dialog)[0]
        self.assertTrue(notice.wordWrap(), "免责句的换行被关了：单行 sizeHint 实测 792 px")
        grown = resize_dialog_to_content(self.dialog, QSize(360, 220))
        self.assertGreaterEqual(grown.width(), 360, "设计基准宽度都没保住，差值断言会自证成立")

        wrapped_height = notice.height()
        # 免责句带一个"换行高度下限"（见 dialogs.py：Windows 字体度量下 QVBoxLayout 会
        # 按 heightForWidth − 1 分配，末行底被切 1 px）。量单行时必须先撤下限，否则两边
        # 都被钉成同一个下限，这条判据就自己失效了。
        floor = notice.minimumHeight()
        notice.setMinimumHeight(0)
        notice.setWordWrap(False)
        _app.processEvents()
        one_line_height = notice.height()
        notice.setWordWrap(True)
        notice.setMinimumHeight(floor)
        _app.processEvents()
        self.assertGreater(
            wrapped_height, one_line_height, f"换行没生效，仍占 {one_line_height} px 高"
        )

        notice.setVisible(False)
        _app.processEvents()
        bare = resize_dialog_to_content(self.dialog, QSize(360, 220))
        notice.setVisible(True)
        self.assertEqual(
            grown.width(),
            bare.width(),
            f"免责句把关于页撑宽了 {grown.width() - bare.width()} px（换行没生效？）",
        )


class AcquisitionChannelsTest(unittest.TestCase):
    """关于页「获取渠道」那三条：人工 2026-10-10 点名的原话，一条不多一条不少。

    钉两件事：① 内容逐字（群号是人工 2026-10-10T15:34:59 点名的**审核群**那一个，更早流传的那个
    号他判为过期，四处落点的收敛由下面 `GroupNumberConsistencyTest` 单独钉）；② 版位没坏。
    URL 要能在字面上读出来（只挂在 href 里时，人不知道这一点会进哪家仓库），但按默认字号把长 URL
    铺成可见文本时实测关于页需要 618 px 高，offscreen 那块 600 px 的屏会把弹窗夹到 581 而使免责句
    与上一条**重叠 8 px**。现在的形态是次级行 `<small>`＋去掉 `https://`：实测 387×583（改前
    360×581），行高仍压在 44 px 的触摸目标里——`test_url_is_visible_text_without_growing_the_dialog`
    钉这一格。
    """

    EXPECTED = (
        ("官方Q群（审核群）", "1128485088"),
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
        self.assertEqual(group.text(), "官方Q群（审核群）: 1128485088")
        self.assertTrue(group.textInteractionFlags() & Qt.TextSelectableByMouse, "群号得能选中复制")
        for row, (_, url) in zip((trunk, linux), self.EXPECTED[1:]):
            self.assertIn(f'href="{url}"', row.text())
            self.assertTrue(row.openExternalLinks())

    def test_url_is_visible_text_without_growing_the_dialog(self) -> None:
        """URL 整串在字面上读得到，同时关于页不被撑高（次级行那一档）。"""
        from meapet.desktop.dialogs import MIN_TARGET_SIZE

        rows = self._rows()
        for row, (_, url) in zip(rows[1:], self.EXPECTED[1:]):
            with self.subTest(channel=row.accessibleName()):
                self.assertIn(url.removeprefix("https://"), row.text())
                self.assertIn(f'href="{url}"', row.text())
                self.assertLessEqual(
                    row.sizeHint().height(),
                    MIN_TARGET_SIZE + 1,
                    f"{row.accessibleName()} 两行小字越过 {MIN_TARGET_SIZE} px 触摸目标，"
                    "关于页会长高、被那块屏夹出重叠",
                )
        self.assertLessEqual(
            self.dialog.sizeHint().height(),
            600,
            f"关于页 sizeHint 高 {self.dialog.sizeHint().height()} px，"
            "超过 offscreen 那块屏就会被夹",
        )

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


class OneSourceForTheSentenceTest(unittest.TestCase):
    """这句话只有**一处**字面量、两处消费者（加载弹窗＋关于页），且两处同一句。

    他 13:10:04 点的就是这两处。各写一遍字面量迟早漂移成两句不同的话，所以一面钉"只有一个字面量"，
    另一面钉"两处渲染出来的文本逐字相等"。
    """

    def test_only_the_constant_module_and_the_two_surfaces_reference_it(self) -> None:
        consumers = {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "meapet").rglob("*.py")
            if "PAID_CHANNEL_NOTICE" in path.read_text(encoding="utf-8")
        }
        self.assertEqual(
            consumers,
            {
                "meapet/desktop/disclaimer.py",
                "meapet/desktop/dialogs.py",
                "meapet/desktop/splash.py",
            },
            "引用这句话的地方变了——两处是他点名的，加第三处得先由他裁",
        )

    def test_the_two_surfaces_render_the_identical_text(self) -> None:
        from meapet.desktop.dialogs import AboutDialog
        from meapet.desktop.splash import StartupSplash

        # dialog 是 host 的子级：不留住 host，它会先被 GC 把 dialog 一起删掉（实测 RuntimeError）
        host = QWidget()
        self.addCleanup(host.deleteLater)
        splash = StartupSplash()
        about = AboutDialog(host)
        self.addCleanup(splash.deleteLater)
        self.addCleanup(about.deleteLater)
        self.assertEqual(_notice_labels(splash)[0].text(), _notice_labels(about)[0].text())

    def test_the_sentence_is_written_out_verbatim_in_exactly_one_place(self) -> None:
        """扫 AST 的字面量值，不扫源码文本。

        常量在 `disclaimer.py` 里写成两段相邻字面量（隐式拼接），整句**从不以任何子串形式
        出现在源码**里——拿 `VERBATIM in text` 去扫，天然恒空，那条断言只是在假绿。
        """
        hits = []
        for path in sorted((ROOT / "meapet").rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            if any(
                isinstance(node, ast.Constant) and node.value == VERBATIM
                for node in ast.walk(tree)
            ):
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(hits, ["meapet/desktop/disclaimer.py"], "有人又抄了一遍这句话的字面量")


if __name__ == "__main__":
    unittest.main()
