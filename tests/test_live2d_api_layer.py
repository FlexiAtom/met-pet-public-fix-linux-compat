"""live2d-py **1.0 单代**符号面闸门的回归：门票诚实、缺格点名、安装面钉同一个数。

Subject under test:
- `meapet.live2d_api` 的 `_missing_symbols` / `_unavailable_reason`（渲染层那张门票）；
- `meapet.bootstrap` 那一格 live2d 判据是否**只**走这一处真值；
- `meapet/desktop/live2d_widget.py` 是否只写 1.0 那一代的名字（AST，不 import Qt）；
- 四个安装面（`启动桌宠.bat`、向导、两份 README、`linux_requirements.txt`）钉的下限是否
  与 `live2d_api.LIVE2D_PY_MIN` **同一个数**。

为什么要 L2 而不只靠探针：`scripts/live2d_generation_probe.py` 靠 `PYTHONPATH` 换包，那份 cache
只在这台机器上，CI 上没有。这一族喂的是**符号面**，判"缺哪一格报哪一格"，与本机装了哪一代无关。

为什么反向钉（人工 2026-10-17 裁决：不背两代兼容债）：两代都认那层已撤，产品只走 1.0.0 那一代的
名字，旧名（`LAppModel` / `SetParameterValue` / `SetAutoBlinkEnable` / `SetAutoBreathEnable` /
`live2d.v3`）在 1.0.0 里**不是废弃而是不存在**（发布正文「Removed the live2d.v3 and live2d.v2cpp
modules」）。于是这里的契约是：1.0 的名字**必须**在场，旧名字**不许**出现在渲染层，而一份 0.7 形状
的假模块必须被拒掉且 reason 点名缺哪些格子——**闸门不为兼容服务，为门票诚实服务**。

这里一律只做 `getattr`，绝不构造模型实例——1.0.0 的 `Model` 在没有 GL 上下文时构造直接段错误
（本机实测 rc=139）。产品侧同一约束，见 `meapet/live2d_api.py`。

改名清单与两臂实测读数：
`~/.Athena/projects/meapet/reference/live2d-py-1.0.0-20261016/readouts.md`
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import NamedTuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meapet import bootstrap, live2d_api  # noqa: E402

WIDGET_SOURCE = ROOT / "meapet" / "desktop" / "live2d_widget.py"
API_SOURCE = ROOT / "meapet" / "live2d_api.py"

#: 1.0.0 里已不存在的旧名（渲染层出现任何一个 = 又在替上游背兼容债）。
RETIRED_NAMES = (
    "LAppModel",
    "SetParameterValue",
    "SetAutoBlinkEnable",
    "SetAutoBreathEnable",
    "GetParameterCount",
)

#: 安装面：钉下限的地方。**判不了装了什么**——盘上已有的旧 wheel、镜像给的别的版本都绕过 specifier，
#: 所以这一族与上面的符号闸门是两道各管一段的闸，谁都不能替谁。
PIN_SURFACES = (
    "启动桌宠.bat",
    "wizard/page_env.py",
    "README.md",
    "README.en.md",
    "linux_requirements.txt",
)

#: 版本号**段数不限**、且把比较符一起抓出来。两格都是反证里量出来的，不是设计癖：
#: ① 旧版写成 `\d+\.\d+\.\d+`（要求三段），于是把 `启动桌宠.bat` 三条安装路里的一条改成
#:    `live2d-py>=0.7` 时**整个匹配不上**，`set(pinned)` 里只有那两个 `1.0.0`，测试照绿
#:    —— 反证 R3 第一次跑就是 `rc=0`。② 光看数字不看符：`==0.7.0.4` 也是"第二个数"。
PIN_RE = re.compile(r"live2d-py\s*(>=|==|~=|>|<=|<)\s*(\d+(?:\.\d+)*)")

#: 真会发出去装的那几行的形状（`pip install` / 向导 `pkg_map` 的值 / requirements 生效行）。
INSTALL_MARKERS = ("pip install", '"live2d-py": [')


class _VersionInfo(NamedTuple):
    """`sys.version_info` 的形状替身——真那个是 structseq，造不出实例。"""

    major: int
    minor: int
    micro: int
    releaselevel: str = "final"
    serial: int = 0


def _stub(*_args, **_kwargs):
    return None


def _fake_model(names: tuple[str, ...], *, as_strings: bool = False):
    values = {n: ("只是个字符串" if as_strings else _stub) for n in names}
    return type("Model", (), values)


def _fake_module(
    *,
    version: str | None = "1.0.0",
    module_names: tuple[str, ...] = live2d_api.MODULE_SYMBOLS,
    model_names: tuple[str, ...] = live2d_api.MODEL_SYMBOLS,
    model_as_strings: bool = False,
    extra: dict | None = None,
) -> ModuleType:
    """造一份 `live2d` 模块面：只有所列的名字，其余一律不存在（真包里旧名不是别名而是没有）。"""
    module = ModuleType("live2d")
    if version is not None:
        module.__version__ = version
    for name in module_names:
        setattr(
            module,
            name,
            _fake_model(model_names, as_strings=model_as_strings)
            if name == "Model"
            else _stub,
        )
    for key, value in (extra or {}).items():
        setattr(module, key, value)
    return module


def _evaluate(module, *, python: tuple[int, int, int] = (3, 12, 5)):
    """在同一份假模块面下一次取回 `(缺格, 那句人话)`。

    必须一起取：`_unavailable_reason` 里的版本号读的是模块全局 `live2d`，把它留到 patch 退出
    之后再调，读到的就是本机真包那份版本——测试会自证假话。`sys` 也一起换；`sys.version_info`
    本身是造不出实例的 structseq，所以用同形状的 NamedTuple 顶（生产码读 `.major`/`.minor`，
    而 `sys.version_info < (3, 11)` 这种比较要求它是 tuple）。
    """
    version_info = _VersionInfo(*python)
    fake_sys = SimpleNamespace(version_info=version_info)
    with mock.patch.object(live2d_api, "live2d", module), \
            mock.patch.object(live2d_api, "sys", fake_sys):
        missing = live2d_api._missing_symbols()
        return missing, live2d_api._unavailable_reason(missing, None)


def _missing_with(module) -> list:
    missing, _ = _evaluate(module)
    return missing


class TestSymbolGate(unittest.TestCase):
    def test_full_1_0_surface_gets_the_ticket(self):
        self.assertEqual(_missing_with(_fake_module()), [])

    def test_0_7_shaped_namespace_is_refused(self):
        """本机 `.venv` 现在那份就是 0.7.0.4——它的形状在这里被当场拒掉，不再冒充"可用"。"""
        module = _fake_module(
            version="0.7.0.4",
            module_names=("init", "dispose"),
            model_names=(),
            extra={
                "LAppModel": type(
                    "LAppModel",
                    (),
                    {n: _stub for n in
                     ("SetParameterValue", "SetAutoBlinkEnable", "SetAutoBreathEnable",
                      "LoadModelJson", "GetParameterCount")},
                ),
            },
        )
        missing, reason = _evaluate(module)
        self.assertIn("live2d.Model", missing)
        for name in ("glInit", "clearBuffer", "MotionPriority"):
            self.assertIn(f"live2d.{name}", missing)
        self.assertIn("0.7.0.4", reason)
        self.assertIn("live2d.Model", reason)
        self.assertNotIn("import 不上", reason)  # 包装着呢，那句是假话

    def test_each_module_tier_symbol_is_its_own_cell(self):
        for name in live2d_api.MODULE_SYMBOLS:
            with self.subTest(missing=name):
                remaining = tuple(n for n in live2d_api.MODULE_SYMBOLS if n != name)
                self.assertEqual(
                    _missing_with(_fake_module(module_names=remaining)), [f"live2d.{name}"]
                )

    def test_each_model_tier_symbol_is_its_own_cell(self):
        """`Model` 上缺任一格都判不可用：渲染层四格都会真调，缺一格 initializeGL/每帧跟随就抛。"""
        for name in live2d_api.MODEL_SYMBOLS:
            with self.subTest(missing=name):
                remaining = tuple(n for n in live2d_api.MODEL_SYMBOLS if n != name)
                self.assertEqual(
                    _missing_with(_fake_module(model_names=remaining)),
                    [f"live2d.Model.{name}"],
                )

    def test_non_callable_attribute_does_not_count(self):
        """名字在场但不可调用 ⇒ 不算能用（两档同一规则，1.0.0 这十个名字实测全为 callable）。"""
        self.assertEqual(
            _missing_with(_fake_module(model_as_strings=True)),
            [f"live2d.Model.{name}" for name in live2d_api.MODEL_SYMBOLS],
        )
        module = _fake_module()
        module.glInit = "只是个字符串"
        self.assertEqual(_missing_with(module), ["live2d.glInit"])


class TestReasonTruthfulness(unittest.TestCase):
    def test_absent_package_says_import_failed_and_names_the_floor(self):
        error = ModuleNotFoundError("No module named 'live2d'")
        reason = live2d_api._unavailable_reason(None, error)
        self.assertIn("import 不上", reason)
        self.assertIn(f">={live2d_api.LIVE2D_PY_MIN}", reason)
        self.assertIn(str(error), reason)

    def test_installed_but_unrecognized_does_not_claim_not_installed(self):
        """这一格就是旧闸门的谎：包 import 得上、有版本号，但符号面认不出来。"""
        missing, reason = _evaluate(_fake_module(version="9.9.9", model_names=()))
        self.assertTrue(missing)
        self.assertIn("装了", reason)
        self.assertIn("9.9.9", reason)
        for name in missing:
            self.assertIn(name, reason)
        self.assertNotIn("not installed", reason)
        self.assertNotIn("import 不上", reason)

    def test_python_floor_is_named_only_when_actually_below(self):
        """live2d-py 从 0.8 起 `Requires-Python >=3.11`（PyPI 现量），3.10 那句必须点名版本号。"""
        module = _fake_module(module_names=("init", "glInit", "clearBuffer", "dispose",
                                            "MotionPriority"))  # 缺 live2d.Model
        _, reason = _evaluate(module, python=(3, 10, 13))
        self.assertIn("3.11", reason)
        self.assertIn("3.10", reason)
        _, reason = _evaluate(module, python=(3, 12, 5))
        self.assertNotIn("3.11+", reason)

    def test_gate_cannot_be_available_without_a_reason_being_empty(self):
        """模块级那组常量的不变式：available ⇔ 缺格为空列表；不 available 就必须有话可说。

        本机装了哪一代都会过这条——它钉的是"拒绝时不许沉默"，与本机读数无关。
        """
        self.assertEqual(
            live2d_api.LIVE2D_AVAILABLE,
            live2d_api.LIVE2D_MISSING is not None and not live2d_api.LIVE2D_MISSING,
        )
        if live2d_api.LIVE2D_AVAILABLE:
            self.assertEqual(live2d_api.LIVE2D_UNAVAILABLE_REASON, "")
        else:
            self.assertNotEqual(live2d_api.LIVE2D_UNAVAILABLE_REASON, "")


class TestBootstrapSingleTruth(unittest.TestCase):
    def test_gate_matches_live2d_api_on_this_machine(self):
        self.assertEqual(
            bootstrap._verify_module_available("live2d"), live2d_api.LIVE2D_AVAILABLE
        )

    def test_gate_follows_the_symbol_surface_even_when_import_succeeds(self):
        """find_spec 与 __version__ 都在场时，判据仍只跟 `LIVE2D_AVAILABLE` 走。"""
        if importlib.util.find_spec("live2d") is None:
            self.skipTest("本机没装 live2d-py，这一格在没包时是空判")
        with mock.patch.object(live2d_api, "LIVE2D_AVAILABLE", False):
            self.assertFalse(bootstrap._verify_module_available("live2d"))
        with mock.patch.object(live2d_api, "LIVE2D_AVAILABLE", True):
            self.assertTrue(bootstrap._verify_module_available("live2d"))

    def test_other_modules_stay_on_find_spec(self):
        self.assertTrue(bootstrap._verify_module_available("json"))
        self.assertFalse(bootstrap._verify_module_available("definitely_not_a_real_module_xyz"))


class TestWidgetWritesOnlyOneGeneration(unittest.TestCase):
    """渲染层只许写 1.0 那一代的名字：写回旧名就是又在背兼容债，而旧名在 1.0.0 里根本不存在。"""

    def _attribute_names(self, source: str) -> set[str]:
        tree = ast.parse(source)
        return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

    def test_1_0_names_are_present(self):
        names = self._attribute_names(WIDGET_SOURCE.read_text(encoding="utf-8"))
        required = {
            "Model", "MotionPriority", "glInit", "clearBuffer", "init", "dispose",
            "LoadModelJson", "SetAutoBlink", "SetAutoBreath", "SetParamById",
        }
        self.assertEqual(sorted(required - names), [])

    def test_no_retired_names_in_the_ast(self):
        offenders = sorted(self._attribute_names(WIDGET_SOURCE.read_text(encoding="utf-8"))
                           & set(RETIRED_NAMES))
        self.assertEqual(offenders, [], "live2d_widget 里出现了 1.0.0 已删除的旧名")

    def test_no_namespace_hunting(self):
        """两代都认那层的形状不许回来：不 import 子命名空间，也不用 `importlib` 动态探。

        只查**代码形状**（AST），不查散文——`live2d_api` 的 docstring 里要引那句发布正文
        「Removed the live2d.v3 and live2d.v2cpp modules」，拒绝文案也要点名删了什么。
        """
        for path, where in ((WIDGET_SOURCE, "live2d_widget"), (API_SOURCE, "live2d_api")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported += [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
            with self.subTest(source=where):
                self.assertEqual([m for m in imported if m.startswith("live2d.")], [])
                self.assertNotIn("importlib", imported)
                self.assertNotIn("import_module", attrs)


class TestWizardSharesTheGate(unittest.TestCase):
    """向导那句「就绪」必须读产品自己那张门票——`pip` 元数据只证明"装过"，不证明能用。

    这条是行为测试不是文本匹配：把分支换回 `check_installed("live2d-py")` 会当场红
    （装了 0.7 的机器上那句会返回 True，向导却说"就绪"，正是旧闸门的谎）。
    """

    def _run_branch(self, available: bool, missing=("live2d.Model",)):
        """`missing` 是那张门票的缺格读数：`None` = 包 import 不上，列表 = 装着了但名字对不上。"""
        from unittest.mock import Mock

        from wizard.page_env import EnvCheckPage

        fake = SimpleNamespace(
            _checklist=[("live2d-py", "", False)],
            log=Mock(),
            _set_item_status=Mock(),
            _set_skipped_status=Mock(),
            _mark_optional_missing=Mock(),
            _set_progress=Mock(),
            _finish_check_summary=Mock(),
        )
        platform = {
            "is_windows": False, "is_linux": False, "is_wsl": False,
            "is_macos": False, "display": "测试", "system": "测试", "arch": "x86_64",
        }
        with mock.patch.object(live2d_api, "LIVE2D_AVAILABLE", available), \
                mock.patch.object(live2d_api, "LIVE2D_MISSING", None if available else missing), \
                mock.patch.object(
                    live2d_api, "LIVE2D_UNAVAILABLE_REASON",
                    "" if available else "装了但缺 live2d.Model",
                ), \
                mock.patch("wizard.page_env.PLATFORM", platform):
            EnvCheckPage._run_checks_impl(fake)
        return fake

    def test_ready_label_only_when_symbol_surface_passes(self):
        for available in (True, False):
            with self.subTest(available=available):
                fake = self._run_branch(available)
                name, ok, label = fake._set_item_status.call_args[0][:3]
                self.assertEqual((name, ok), ("live2d-py", available))
                self.assertEqual(label.startswith("✅"), available)

    def test_status_text_splits_installed_wrong_from_absent(self):
        """「未装」与「装着了但对不上」是两种话——旧闸门那句谎在**状态栏**的第二个落点。"""
        for missing, want, refuse in (
            (None, "未装", "接口对不上"),
            (["live2d.Model"], "接口对不上", "未装"),
        ):
            with self.subTest(missing=missing):
                fake = self._run_branch(False, missing)
                label = fake._set_item_status.call_args[0][2]
                self.assertIn(want, label)
                self.assertNotIn(refuse, label)

    def test_refusal_reason_reaches_the_log(self):
        """不 ready 时那句要说清缺什么，而不是又一句"未装"。"""
        fake = self._run_branch(False)
        logged = " ".join(str(c.args[0]) for c in fake.log.call_args_list)
        self.assertIn("live2d.Model", logged)
        self.assertNotIn("未装（可选）", logged)  # 包装着呢，那句是假话
        fake._mark_optional_missing.assert_called_once_with("live2d-py")


class TestInstallSurfacesAgreeOnTheFloor(unittest.TestCase):
    """安装面钉的数与 `LIVE2D_PY_MIN` 必须同一个数——别处抄第二个数就是双真值。"""

    def _lines(self, rel: str):
        # 只读不回写：`Path.read_text(newline=...)` 在 3.13 之前不存在（本机 3.12.13 实测
        # `TypeError: unexpected keyword argument 'newline'`），而按行匹配不需要保住 CRLF——
        # 需要保住的是**写**的那一侧，见反证脚本 `falsify_single_gen.py` 的 bytes 处理。
        text = (ROOT / rel).read_text(encoding="utf-8")
        return [(lineno, raw.strip()) for lineno, raw in enumerate(text.splitlines(), 1)]

    def test_every_surface_has_a_pin_and_no_second_number(self):
        """每一个带比较符的 `live2d-py` 串都必须正好是 `>=<下限>`——段数不限，符也管。"""
        for rel in PIN_SURFACES:
            with self.subTest(surface=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                specs = PIN_RE.findall(text)
                self.assertTrue(specs, f"{rel} 里没有 live2d-py 的下限")
                for op, ver in specs:
                    self.assertEqual((op, ver), (">=", live2d_api.LIVE2D_PY_MIN),
                                     f"{rel} 钉的不是同一个数：{op}{ver}")

    def test_every_requirement_line_carries_the_floor(self):
        """每一条**真发出去**的安装行都得带界。反证 R3 那一发只改坏三条里的一条就蒙混过关，
        根因是"至少有一处钉了"这类判据天生看不见漏掉的那条——这里改成逐行、且按整条算。
        裸名 `pip install live2d-py` 也归这一格管：它会解析到最新，而最新未必凑齐名字。
        """
        checked = 0
        for rel in PIN_SURFACES:
            for lineno, line in self._lines(rel):
                if "live2d-py" not in line:
                    continue
                if line.startswith("#"):
                    continue
                is_requirement = rel == "linux_requirements.txt"
                if not (is_requirement or any(m in line for m in INSTALL_MARKERS)):
                    continue
                checked += 1
                self.assertIn(f"live2d-py>={live2d_api.LIVE2D_PY_MIN}", line.replace(" ", ""),
                              f"{rel}:{lineno} 这一发没带下限：{line}")
        self.assertGreaterEqual(checked, 6, "安装行一条都没扫到 = 判据空转")

    def test_windows_launcher_never_hands_the_venv_a_python_below_the_floor(self):
        """`.bat` 兜底那条系统 Python 选路的下限 = `LIVE2D_PY_PYTHON_MIN`（3.11）。

        钉文本是因为这一格**没有别的真值可钉**：Linux 侧那个 `>=3.11` 靠
        `linux_requirements.txt:34` 那颗 PEP 508 marker 兜住，而批处理写不了 marker——
        下限只存在于候选表和两道 `version_info` 闸门里。uv 取不到托管解释器的那台机器
        会走到这条兜底路，选中 3.10 就得到一个**永远装不上 Live2D 的 .venv**，
        而那件事当时没有任何一句话会说。
        """
        floor = live2d_api.LIVE2D_PY_PYTHON_MIN
        launcher = (ROOT / "启动桌宠.bat").read_text(encoding="utf-8")
        gate = f">=({floor[0]},{floor[1]})"
        below = f">=({floor[0]},{floor[1] - 1})"
        self.assertGreaterEqual(
            launcher.count(gate), 2, f"两条解释器闸门里有一条没带下限 {gate}")
        self.assertNotIn(below, launcher, "闸门里还留着低一档的门")
        for lineno, line in self._lines("启动桌宠.bat"):
            if "for %%V in (" in line:
                candidate = f"{floor[0]}.{floor[1] - 1}"
                self.assertNotIn(candidate, line,
                                 f"启动桌宠.bat:{lineno} 候选表还留着 {candidate}")
        self.assertIn(f"install Python {floor[0]}.{floor[1]}+", launcher,
                      ":venv_fail 那句补救还写着低一档的版本")


if __name__ == "__main__":
    unittest.main()
