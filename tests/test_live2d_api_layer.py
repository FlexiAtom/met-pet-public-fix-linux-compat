"""live2d-py 两代名字层的回归：解析、缺格、以及闸门不再说谎。

Subject under test: `meapet.live2d_api` 的 `_resolve` / `_detect` / `_unavailable_reason`，
外加 `meapet.bootstrap` 那一格 live2d 判据是否**只**走这一处真值。

为什么要 L2 而不只靠探针：`scripts/live2d_generation_probe.py` 的两臂靠 `PYTHONPATH` 换包，
那份 cache 只在这台机器上；CI 上没有。这一族把两代的**符号面**各造一份喂进解析层，判的是
"名字怎么被挑出来、缺哪一格报哪一格"，与本机装了哪一代无关。

这里一律只做 `getattr`，绝不构造模型实例——1.0.0 的 `Model` 在没有 GL 上下文时构造直接段错误
（实测 rc=139，0.7 的 `v3.Model()` 不段，这条坑是新版带进来的）。产品侧同一约束，见
`meapet/live2d_api.py` 的 `_detect`。

改名清单与两臂实测读数：
`~/.Athena/projects/meapet/reference/live2d-py-1.0.0-20261016/readouts.md`
"""
from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meapet import bootstrap, live2d_api  # noqa: E402

WIDGET_SOURCE = ROOT / "meapet" / "desktop" / "live2d_widget.py"

#: 两代各自那一套名字（0.7 在 `live2d.v3`，1.0.0 在顶层 `live2d`）。
GEN_07 = ("LAppModel", "SetParameterValue", "SetAutoBlinkEnable", "SetAutoBreathEnable")
GEN_100 = ("Model", "SetParamById", "SetAutoBlink", "SetAutoBreath")


def _model_cls(names: tuple[str, ...]):
    """造一个"模型类"：只有所需的名字，其余一律不存在（真包里旧名不是废弃而是没有）。"""
    namespace = {name: (lambda *_a, **_kw: None) for name in names}
    return type("FakeModel", (), namespace)


def _namespace(module_name: str, model_name: str, setter_names: tuple[str, ...],
               *, with_version: str | None = None) -> ModuleType:
    module = ModuleType(module_name)
    setattr(module, model_name, _model_cls(("Draw", "Update") + setter_names))
    if with_version is not None:
        module.__version__ = with_version
    return module


class TestGenerationResolution(unittest.TestCase):
    def test_07_namespace_resolves_old_names(self):
        module = _namespace("live2d.v3", GEN_07[0], GEN_07[1:])
        api, missing = live2d_api._detect(module, "live2d.v3")
        self.assertIsNone(missing)
        self.assertEqual(
            (api.namespace, api.model_class, api.param_setter, api.blink_setter, api.breath_setter),
            ("live2d.v3", *GEN_07),
        )

    def test_100_namespace_resolves_new_names(self):
        module = _namespace("live2d", GEN_100[0], GEN_100[1:], with_version="1.0.0")
        api, missing = live2d_api._detect(module, "live2d")
        self.assertIsNone(missing)
        self.assertEqual(
            (api.namespace, api.model_class, api.param_setter, api.blink_setter, api.breath_setter),
            ("live2d", *GEN_100),
        )

    def test_first_candidate_wins_when_a_module_offers_both(self):
        """合成的一份"两代名字都有"的模块——钉的是 `_resolve` 那条"顺序即优先级"，不是任何真包。"""
        module = _namespace("live2d.v3", GEN_07[0], GEN_07[1:] + GEN_100[1:])
        api, missing = live2d_api._detect(module, "live2d.v3")
        self.assertIsNone(missing)
        self.assertEqual((api.param_setter, api.blink_setter, api.breath_setter), GEN_07[1:])


class TestMissingCells(unittest.TestCase):
    def test_no_package_reports_package(self):
        api, missing = live2d_api._detect(None, None)
        self.assertIsNone(api)
        self.assertEqual(missing, "package")

    def test_module_without_either_model_class_reports_model(self):
        module = ModuleType("live2d")
        module.__version__ = "0.0.0"
        api, missing = live2d_api._detect(module, "live2d")
        self.assertIsNone(api)
        self.assertEqual(missing, "model")

    def test_each_setter_cell_is_its_own_gate(self):
        """眨眼/呼吸/写参任缺一个都判不可用：渲染层三格都会真调，缺一格 initializeGL 就抛。"""
        for key, retired_name in zip(("param", "blink", "breath"), GEN_100[1:]):
            with self.subTest(missing=key):
                remaining = tuple(n for n in GEN_100[1:] if n != retired_name)
                module = _namespace("live2d", GEN_100[0], remaining)
                api, missing = live2d_api._detect(module, "live2d")
                self.assertIsNone(api)
                self.assertEqual(missing, key)

    def test_non_callable_attribute_does_not_count(self):
        """名字在场但不可调用 ⇒ 不算解析成功（`getattr` 命中不等于能用）。"""
        module = _namespace("live2d", GEN_100[0], GEN_100[1:])
        module.Model = type("Model", (), {name: "只是个字符串" for name in GEN_100[1:]})
        api, missing = live2d_api._detect(module, "live2d")
        self.assertIsNone(api)
        self.assertEqual(missing, "param")


class TestReasonTruthfulness(unittest.TestCase):
    def test_unrecognized_but_installed_does_not_claim_not_installed(self):
        """这一格就是旧闸门的谎：包 import 得上、有版本号，但符号面认不出来。"""
        module = _namespace("live2d", GEN_100[0], (), with_version="9.9.9")
        api, missing = live2d_api._detect(module, "live2d")
        self.assertIsNone(api)
        self.assertEqual(missing, "param")
        reason = live2d_api._unavailable_reason(missing, None, "9.9.9")
        self.assertIn("装了", reason)
        self.assertIn("9.9.9", reason)
        for name in live2d_api.LIVE2D_API_NAMES[missing]:
            self.assertIn(name, reason)
        self.assertNotIn("not installed", reason)
        self.assertNotIn("import 不上", reason)

    def test_missing_package_reason_lists_both_namespaces(self):
        error = ModuleNotFoundError("No module named 'live2d'")
        reason = live2d_api._unavailable_reason("package", error, None)
        self.assertIn("live2d.v3", reason)
        self.assertIn("import 不上", reason)
        self.assertIn(str(error), reason)

    def test_resolved_surface_has_no_reason(self):
        self.assertEqual(live2d_api._unavailable_reason(None, None, "0.7.0.4"), "")


class TestBootstrapSingleTruth(unittest.TestCase):
    def test_gate_matches_live2d_api_on_this_machine(self):
        self.assertEqual(
            bootstrap._verify_module_available("live2d"), live2d_api.LIVE2D_AVAILABLE
        )

    def test_gate_follows_the_layer_even_when_import_succeeds(self):
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


class TestWidgetDoesNotHardcodeGenerationNames(unittest.TestCase):
    """渲染层写死任一代的名字，就是绕过这一层——下次换包又是安静退回 PNG。"""

    def _attribute_names(self) -> list[str]:
        tree = ast.parse(WIDGET_SOURCE.read_text(encoding="utf-8"))
        names = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)]
        names += [
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in (*GEN_07, *GEN_100)
        ]
        return names

    def test_no_retired_names_in_the_ast(self):
        offenders = sorted(set(self._attribute_names()) & set((*GEN_07, *GEN_100)))
        self.assertEqual(offenders, [], "live2d_widget 里出现了写死的某一代名字")

    def test_no_static_import_of_live2d_v3(self):
        tree = ast.parse(WIDGET_SOURCE.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertNotIn("live2d.v3", imported)
        self.assertNotIn("live2d", imported)


if __name__ == "__main__":
    unittest.main()
