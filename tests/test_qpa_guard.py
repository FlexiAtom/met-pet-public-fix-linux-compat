"""Qt 平台插件闸门的回归：三条判据少一条都会退化成猜测，所以每一格单独钉。

这一族钉的是**判据**，不是 Qt：目录用临时目录里的假 `libq*.so`，"这个 `.so` 加载得起来吗"
走注入的探针。真探针（`ctypes.CDLL` 默认 `RTLD_NOW`）与 `QPluginLoader`
（带 `ResolveAllSymbolsHint`）同一语义那一格，是人工 2026-10-16 现报那两行
`Could not load the Qt platform plugin` 的逐字复现——存
`~/.Athena/projects/meapet/reference/qt-qpa-plugin-mismatch/08-faithful-probe.txt`，
不在这里重跑（这里一起真 QApplication 就得挑合成器脸色）。

另钉两条：闸门必须排在 `QApplication(sys.argv)` **之前**（排在后面就等于没写——那一发是
`qFatal → abort()`，Python 的 try/except 接不住，死后没有任何出口能再说话）；以及
「只说不改」那一档**一次都不碰内置那一套**（去验它就是把那些 `.so` 映射进本进程，实测那
一步单独就够让程序起来，那就不叫不改）。
"""
import ast
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional, Sequence
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meapet.desktop import qpa_guard  # noqa: E402

APP_SOURCE = ROOT / "meapet" / "desktop" / "app.py"
PLUGINS = ("libqxcb.so", "libqwayland-generic.so")


def _mkinstall(base: Path, name: str) -> str:
    """造一个假的"一套 Qt"：`<name>/platforms/libq*.so`，内容无所谓——探针是注入的。"""
    d = base / name
    (d / "platforms").mkdir(parents=True, exist_ok=True)
    for p in PLUGINS:
        (d / "platforms" / p).write_bytes(b"not an elf")
    return str(d)


def _prober(loadable: set) -> tuple[callable, list]:
    seen: list[str] = []

    def probe(path: str) -> tuple[bool, str]:
        seen.append(path)
        if any(os.path.realpath(path) == os.path.realpath(x) for x in loadable):
            return True, ""
        return False, (f"{path}: undefined symbol: _ZTI23QPlatformVulkanInstance, "
                       "version Qt_5_PRIVATE_API")

    return probe, seen


class TestSearchCandidates(unittest.TestCase):
    def setUp(self):
        # realpath 一次：/tmp 若是软链，判据里的 realpath 比较会让断言看着像错的
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="qpa_guard_")))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.a = _mkinstall(self.base, "a")
        self.b = _mkinstall(self.base, "b")
        self.builtin = _mkinstall(self.base, "builtin")

    def test_order_is_qt_not_ours(self):
        """`QT_QPA_PLATFORM_PLUGIN_PATH` 抢在 `QT_PLUGIN_PATH` 前，内置压轴——Qt 的搜索顺序。"""
        env = {
            qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH: os.path.join(self.a, "platforms"),
            qpa_guard.ENV_PLUGIN_PATH: self.b,
        }
        got = qpa_guard.search_candidates(env, self.builtin)
        rp = os.path.realpath
        self.assertEqual(
            [d for d, _ in got],
            [os.path.join(rp(self.a), "platforms"), os.path.join(rp(self.b), "platforms"),
             os.path.join(rp(self.builtin), "platforms")],
        )
        self.assertEqual([src for _, src in got],
                         [qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH, qpa_guard.ENV_PLUGIN_PATH,
                          "Qt 内置"])

    def test_entry_that_already_is_platforms_dir_is_not_duplicated(self):
        env = {qpa_guard.ENV_PLUGIN_PATH: os.path.join(self.b, "platforms")}
        got = [d for d, _ in qpa_guard.search_candidates(env, self.builtin)]
        self.assertEqual(got, [os.path.join(self.b, "platforms"),
                               os.path.join(self.builtin, "platforms")])

    def test_nonexistent_env_entry_is_skipped_like_qt_does(self):
        env = {qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH: str(self.base / "nope"),
               qpa_guard.ENV_PLUGIN_PATH: str(self.base / "gone")}
        self.assertEqual([d for d, _ in qpa_guard.search_candidates(env, self.builtin)],
                         [os.path.join(self.builtin, "platforms")])


class TestPreferredNames(unittest.TestCase):
    """那句错该说哪一只插件的——按 Qt 的选路，不按目录里的字母序。"""

    def test_explicit_platform_wins_and_tolerates_fallback_list_and_args(self):
        self.assertEqual(
            qpa_guard.preferred_plugin_names({qpa_guard.ENV_QPA_PLATFORM: "wayland,xcb"}),
            ["libqwayland.so", "libqwayland-generic.so", "libqxcb.so", "libqxcb-generic.so"],
        )
        self.assertEqual(
            qpa_guard.preferred_plugin_names({qpa_guard.ENV_QPA_PLATFORM: "xcb:--scale=2"}),
            ["libqxcb.so", "libqxcb-generic.so"],
        )

    def test_wayland_session_tries_wayland_then_xcb(self):
        got = qpa_guard.preferred_plugin_names({qpa_guard.ENV_WAYLAND_DISPLAY: "wayland-1"})
        self.assertEqual(got[:2], ["libqwayland.so", "libqwayland-generic.so"])
        self.assertIn("libqxcb.so", got)

    def test_plain_session_prefers_xcb(self):
        self.assertEqual(qpa_guard.preferred_plugin_names({}), ["libqxcb.so",
                                                               "libqxcb-generic.so"])

    def test_key_and_file_name_differ_on_this_machine(self):
        """本机两套目录里 `wayland` 那只的文件名是 `libqwayland-generic.so`（实测 ls），
        所以键名与带 -generic 的两种写法都要排进去，否则那句又落回字母序。"""
        got = qpa_guard.preferred_plugin_names({qpa_guard.ENV_QPA_PLATFORM: "wayland"})
        self.assertIn("libqwayland-generic.so", got)


class TestGate(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.path.realpath(tempfile.mkdtemp(prefix="qpa_guard_")))
        self.addCleanup(shutil.rmtree, self.base, True)
        self.win = _mkinstall(self.base, "win")     # QT_PLUGIN_PATH 指向的那一套
        self.tgt = _mkinstall(self.base, "tgt")     # 与已加载 libQt5Core 同路的那一套
        self.lines: list[str] = []
        self.win_plat = os.path.join(self.win, "platforms")
        self.tgt_plat = os.path.join(self.tgt, "platforms")
        # 闸门只在 sys.platform == "linux" 时进门；判据本身与宿主无关。
        # 钉成 linux，这 7 条判据断言在 Windows 上也跑真的——否则拿到的是
        # skip_platform，测的变成"进门那一步"，判据本身没人验。
        self._platform = mock.patch.object(qpa_guard.sys, "platform", "linux")
        self._platform.start()
        self.addCleanup(self._platform.stop)

    def _run(self, env: dict, loadable: set, seen: Optional[list] = None) -> dict:
        probe, probe_seen = _prober(loadable)
        outcome = qpa_guard.ensure_platform_plugins_loadable(
            self.lines.append, env=env, probe=probe, builtin_plugins=self.tgt
        )
        if seen is not None:
            seen.extend(probe_seen)
        return outcome

    def test_no_override_is_matched_and_silent(self):
        got = self._run({}, loadable=set())
        self.assertEqual(got["kind"], "matched")
        self.assertEqual(self.lines, [])

    def test_same_install_says_nothing_and_touches_nothing(self):
        """一致那一格：一个字不说，一个键不改。能跑的口子今天能跑，明天也一样。"""
        env = {qpa_guard.ENV_PLUGIN_PATH: self.tgt}
        got = self._run(env, loadable=set())
        self.assertEqual(got["kind"], "matched")
        self.assertEqual(self.lines, [])
        self.assertNotIn(qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH, env)

    def test_all_fail_repairs_narrowly_and_keeps_plugin_path(self):
        seen: list[str] = []
        env = {qpa_guard.ENV_PLUGIN_PATH: self.win, qpa_guard.ENV_QPA_PLATFORM: "xcb"}
        got = self._run(env, loadable={os.path.join(self.tgt_plat, "libqxcb.so")}, seen=seen)
        self.assertEqual(got["kind"], "repaired")
        # 只改平台插件那一格：QT_PLUGIN_PATH 原样留着（那里头挂着 fcitx5 输入法插件，
        # 实测三个 IM 插件在打包 core 下都加载得起来，撤掉是拿走真东西）
        self.assertEqual(env[qpa_guard.ENV_PLUGIN_PATH], self.win)
        self.assertEqual(env[qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH], self.tgt_plat)
        joined = "\n".join(self.lines)
        self.assertIn("两套 Qt", joined)
        self.assertIn("undefined symbol: _ZTI23QPlatformVulkanInstance", joined)
        self.assertIn("qFatal", joined)
        self.assertIn(".venv/bin/python", joined)
        self.assertIn(qpa_guard.ENV_NO_REPAIR, joined)
        # 报的那句是 Qt 选路先试的那只（xcb），不是目录里排最前的（libqwayland-generic.so
        # 按字母序在前）——指错了插件，人照那句去补系统包会补错那一族。
        self.assertEqual(got["first_error_plugin"], "libqxcb.so")
        self.assertIn("libqxcb.so: undefined symbol", joined)
        self.assertNotIn("libqwayland-generic.so: undefined", joined)
        # 两条判据各自验过：win 的插件全验，tgt 至少验到一个——不是拿同一格充数
        self.assertIn(os.path.join(self.win_plat, "libqxcb.so"), seen)
        self.assertIn(os.path.join(self.tgt_plat, "libqwayland-generic.so"), seen)

    def test_unlisted_plugin_falls_back_to_first_failure(self):
        """那一套里没有 xcb/wayland 时，退回「第一个加载失败的」，并且把它的文件名一起报出来。"""
        d = self.base / "eglsonly" / "platforms"
        d.mkdir(parents=True)
        (d / "libqeglfs.so").write_bytes(b"not an elf")
        env = {qpa_guard.ENV_PLUGIN_PATH: str(self.base / "eglsonly"),
               qpa_guard.ENV_QPA_PLATFORM: "xcb"}
        got = self._run(env, loadable=set())
        self.assertEqual(got["kind"], "warned")
        self.assertEqual(got["first_error_plugin"], "libqeglfs.so")
        self.assertIn("libqeglfs.so: undefined symbol", "\n".join(self.lines))

    def test_partially_loadable_is_not_the_same_trap(self):
        """只有一部分失败 ⇒ 那一套里还有 Qt 用得上的东西 ⇒ 不许改指。"""
        env = {qpa_guard.ENV_PLUGIN_PATH: self.win}
        got = self._run(env, loadable={os.path.join(self.win_plat, "libqxcb.so")})
        self.assertEqual(got["kind"], "noop")
        self.assertNotIn(qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH, env)
        self.assertIn("至少有一个平台插件加载得起来", "\n".join(self.lines))

    def test_target_also_unloadable_does_not_point_there(self):
        env = {qpa_guard.ENV_PLUGIN_PATH: self.win}
        got = self._run(env, loadable=set())
        self.assertEqual(got["kind"], "warned")
        self.assertNotIn(qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH, env)
        self.assertIn("内置那一套也验不出可加载的平台插件", "\n".join(self.lines))
        self.assertIn("undefined symbol: _ZTI23QPlatformVulkanInstance", "\n".join(self.lines))

    def test_no_repair_flag_makes_the_gate_say_only(self):
        seen: list[str] = []
        env = {qpa_guard.ENV_PLUGIN_PATH: self.win, qpa_guard.ENV_NO_REPAIR: "1"}
        got = self._run(env, loadable={os.path.join(self.tgt_plat, "libqxcb.so")}, seen=seen)
        self.assertEqual(got["kind"], "warned")
        self.assertNotIn(qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH, env)
        self.assertIn(f"{qpa_guard.ENV_NO_REPAIR}=1 ⇒ 只说不改", "\n".join(self.lines))
        self.assertIn("两套 Qt", "\n".join(self.lines))
        # 「不改」必须是真的：验内置那一套就是把那些 `.so` 映射进本进程，实测那一步单独
        # 就足以让后面的 QApplication 起来（什么都不改也 OK platform=xcb，存 10/11 两份读数）。
        # 所以标了这一档，内置那一套一次都不许碰。
        self.assertEqual([p for p in seen if p.startswith(self.tgt_plat)], [])
        self.assertTrue([p for p in seen if p.startswith(self.win_plat)])

    def test_nonlinux_returns_without_any_presumption(self):
        """非 Linux 直接返回：打包态/Windows 那一格没量过，不做跨平台推定，也不说话。"""
        env = {qpa_guard.ENV_PLUGIN_PATH: self.win}
        with mock.patch.object(sys, "platform", "win32"):
            got = self._run(env, loadable={os.path.join(self.tgt_plat, "libqxcb.so")})
        self.assertEqual(got["kind"], "skip_platform")
        self.assertEqual(self.lines, [])
        self.assertNotIn(qpa_guard.ENV_QPA_PLATFORM_PLUGIN_PATH, env)


class TestWiring(unittest.TestCase):
    def test_gate_runs_before_qapplication_construction(self):
        """排在 `QApplication(sys.argv)` 之后就是没写：那一发 abort，接不住。"""
        tree = ast.parse(APP_SOURCE.read_text(encoding="utf-8"))
        main = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        marks: list[tuple[int, str]] = []
        for node in ast.walk(main):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None) or ""
                if name == "ensure_platform_plugins_loadable":
                    marks.append((node.lineno, "gate"))
                elif name == "QApplication":
                    marks.append((node.lineno, "qapp"))
        self.assertEqual([k for _, k in sorted(marks)], ["gate", "qapp"],
                         f"闸门必须排在 QApplication 之前，实际 {marks}")


if __name__ == "__main__":
    unittest.main()
