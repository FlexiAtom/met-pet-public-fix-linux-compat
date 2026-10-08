"""`meapet --doctor` —— 无头只读取证入口的红线。

人工 2026-10-08 放行（逐字见 `~/.Athena/projects/meapet/voice.md`）：「好，可以动码，做无头模式」，
且改判过两条前提：「无头模式是给AI用的」「目的是测试，不论是否打包」。这里钉的是**入口的形状**，
不是各台机器读数的对错：

- **不进 GUI 就不许建 Qt 应用**——无显示服务时那一下是 abort，不是异常，catch 不到（默认关，`--with-gui` 显式开）；
- **只读**——跑一遍不许动 config 文件；
- **探测自身失败要变成一行读数**，不许把 traceback 递给调用方（"无头"两个字判的就是这一格）；
- **stdout 只有报告**——产品侧那些函数里有直接 print 的，取证期间得串到 stderr；
- **两路 TTS 各一条，且吃 diagnostics 那一份真值**——doctor 里重算就是第二套真值。

全部遮掉真健康检查（`MeaTTS.health_check` 在本机 19s），要钉的是接线与红线。
"""
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meapet import doctor  # noqa: E402
from meapet.tts.diagnostics import speech_facts, tts_for_engine  # noqa: E402

#: 默认那一臂（`--skip-tts`）的项序，逐格与 `doctor.collect` 对齐；layer 那一格就在 backend 之后。
BASE_ITEMS = [
    "runtime",
    "paths",
    "config",
    "llm",
    "display_env",
    "backend",
    "layer_shim",
    "fidus",
    "audio",
    "input_devices",
    "asr",
    "screens",
    "log_tail",
]


class DoctorRequestedTest(unittest.TestCase):
    def test_whole_word_only(self) -> None:
        """`--doctors` 那种参数不该被劫持成取证——劫持了它就是"双击没反应"的另一种形状。"""
        self.assertTrue(doctor.doctor_requested(["python", "pet.py", "--doctor"]))
        self.assertFalse(doctor.doctor_requested(["python", "pet.py", "--doctors"]))
        self.assertFalse(doctor.doctor_requested(["python", "pet.py", "--check"]))
        self.assertFalse(doctor.doctor_requested([]))


class ItemShapeTest(unittest.TestCase):
    def _items(self, **kw):
        return doctor.collect({}, **kw)

    def test_default_has_layer_and_no_tts_when_skipped(self) -> None:
        names = [item.name for item in self._items(skip_tts=True)]
        self.assertEqual(names, BASE_ITEMS)

    def test_skip_layer_drops_the_one_that_actually_inits(self) -> None:
        names = [item.name for item in self._items(skip_tts=True, skip_layer=True)]
        self.assertNotIn("layer_shim", names)
        self.assertEqual(names, [n for n in BASE_ITEMS if n != "layer_shim"])

    def test_both_tts_engines_get_their_own_row(self) -> None:
        """engine 那格在配置里只能是一个值，而两路的坏法完全不同：合起来看只会互相遮蔽。"""
        with mock.patch("meapet.tts.service.MeaTTS.health_check", lambda self: False):
            names = [item.name for item in self._items(skip_tts=False)]
        self.assertIn("tts.gpt_sovits", names)
        self.assertIn("tts.vits", names)

    def test_every_item_carries_its_own_judge(self) -> None:
        """没有判据的读数会被读成"这台机器不行"——本入口要消灭的正是这个形状。"""
        for item in self._items(skip_tts=True):
            self.assertTrue(item.name and item.judge, f"{item.name} 缺判据")


class ProbeFailureTest(unittest.TestCase):
    def test_a_raising_probe_becomes_a_row_not_a_traceback(self) -> None:
        def boom():
            raise RuntimeError("探测自己炸了")

        item = doctor._probe("boom", "判据", boom)
        self.assertIn("探测自身失败 RuntimeError", item.value)
        self.assertEqual(item.name, "boom")

    def test_secret_in_a_raising_probe_is_redacted(self) -> None:
        item = doctor._probe(
            "boom", "判据", lambda: (_ for _ in ()).throw(RuntimeError("sk-abcdef123456"))
        )
        self.assertNotIn("sk-abcdef123456", item.value)


class QtNotBuiltTest(unittest.TestCase):
    def test_default_collect_does_not_create_a_qt_app(self) -> None:
        """无显示服务时建 Qt 应用是 abort，不是异常——默认那一下必须不发生。"""
        from PyQt5.QtCore import QCoreApplication

        had = QCoreApplication.instance() is not None
        item = doctor._probe("screens", "判据", lambda: doctor._screens(False))
        self.assertEqual(QCoreApplication.instance() is not None, had)
        self.assertIn("未取", item.value)
        self.assertIn("--with-gui", item.value)

    def test_with_gui_uses_the_product_geometry_helper(self) -> None:
        from PyQt5.QtWidgets import QApplication

        app = QApplication.instance() or QApplication(sys.argv)
        with mock.patch(
            "meapet.desktop.screen_geometry.available_geometry_for", return_value=None
        ) as helper:
            text = doctor._screens(True)
        self.assertIn(f"Qt platformName={app.platformName()}", text)
        helper.assert_called_once_with(None)


class ReadOnlyTest(unittest.TestCase):
    def test_a_full_run_leaves_the_config_file_byte_identical(self) -> None:
        """只读是这个入口唯一的对外承诺：跑一遍取证，不许动任何一个文件。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            original = json.dumps({"tts": {"enabled": True, "engine": "vits"}})
            path.write_text(original, encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                rc = doctor.main(["--doctor", "--skip-tts", f"--config={path}"])
            self.assertEqual(rc, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_whitelist_itself_has_no_secret_shaped_key(self) -> None:
        """白名单这一层是**结构性**的闸门：将来有人往里加 `llm.api_key`，靠下面那条"报告里没有它"
        是测不出来的——`scrub_secrets` 会先把值清成空串，于是加错键也照样绿。两道分开钉。"""
        secretish = ("api_key", "auth_token", "token", "secret", "password")
        bad = [
            f"{section}.{key}"
            for section, key in doctor.CONFIG_SUMMARY_KEYS
            if any(mark in key.lower() for mark in secretish)
        ]
        self.assertEqual(bad, [], f"这些键名字像凭据，不该进白名单：{bad}")

    def test_llm_row_prints_no_credential_and_no_raw_endpoint(self) -> None:
        """新加的 `llm` 那格是**唯一一处会碰凭据的探测**，所以它的红线单独钉：
        值不印、`api_base` 原文也不印（有人把 token 写在查询串里），只印归族结果与在场布尔。"""
        config = {"llm": {"api_key": "sk-doctor-never-print",
                          "api_base": "https://api.deepseek.com/v1?token=leak-me",
                          "model": "deepseek-chat"}}
        row = doctor._llm(config)
        self.assertNotIn("sk-doctor-never-print", row)
        self.assertNotIn("deepseek.com", row)
        self.assertNotIn("leak-me", row)
        self.assertIn("direct/HTTP key=在场(来源=配置)", row)

    def test_llm_row_states_its_own_scope(self) -> None:
        """这格测不到 agent 链接那一路的凭据——不写明就会把"缺席"读成"这台机器没配任何东西"。"""
        from meapet.doctor import collect

        row = next(item for item in collect({}, skip_tts=True, skip_layer=True)
                   if item.name == "llm")
        self.assertIn("只覆盖 direct/HTTP", row.judge)
        self.assertIn("direct/HTTP", row.value)

    def test_json_format_reports_the_whitelist_only(self) -> None:
        """第二道：即便有人放宽了白名单，`scrub_secrets` 也在打印之前——报告里不该出现凭据值。"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps({"llm": {"api_key": "sk-doctor-should-not-print"},
                            "tts": {"enabled": True, "engine": "vits", "api_key": "sk-tts-key"}}),
                encoding="utf-8",
            )
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch.object(sys, "stderr", err), redirect_stdout(out):
                rc = doctor.main(["--doctor", "--skip-tts", "--doctor-format", "json",
                                  f"--config={path}"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())  # stdout 必须只有 JSON
            self.assertNotIn("sk-doctor-should-not-print", out.getvalue())
            self.assertNotIn("sk-tts-key", out.getvalue())
            self.assertEqual(len(payload["items"]), len(BASE_ITEMS))

    def test_product_prints_are_moved_off_stdout(self) -> None:
        """`wayland_layer.is_available()` 那一类会直接 print；混进报告就没法整块贴回来。"""
        out = io.StringIO()
        with mock.patch("meapet.desktop.wayland_layer.is_available",
                        side_effect=lambda: print("[layer] ✗ 这条不该在 stdout") or False):
            with redirect_stdout(out):
                rc = doctor.main(["--doctor", "--skip-tts"])
        self.assertEqual(rc, 0)
        self.assertNotIn("[layer] ✗", out.getvalue())
        self.assertTrue(out.getvalue().startswith("meapet --doctor"))


class SharedTruthTest(unittest.TestCase):
    def test_doctor_tts_row_uses_the_same_facts_as_the_menu(self) -> None:
        """判据只有一份（`meapet.tts.diagnostics`）；doctor 里重算就开始自证。"""
        config = {"tts": {"enabled": True, "engine": "vits"}}
        with mock.patch("meapet.tts.service.MeaTTS.health_check", lambda self: False):
            row = doctor._tts(config, "vits")
        expected = speech_facts("vits", tts_for_engine(config, "vits"))
        self.assertIn("前置=不齐", row)
        self.assertIn(expected, row)

    def test_doctor_llm_row_carries_the_product_family_verdict(self) -> None:
        """`family=` 必须是 `llm_endpoint_family()` 那个归族结果，不是 doctor 自己认字符串。"""
        from meapet.config.store import llm_endpoint_family

        config = {"llm": {"api_base": "https://api.deepseek.com/v1", "model": "deepseek-chat"}}
        row = doctor._llm(config)
        self.assertIn(llm_endpoint_family(config["llm"]), row)
        self.assertIn("model=deepseek-chat", row)

    def test_doctor_asr_row_asks_both_precisions_of_the_product_table(self) -> None:
        """档位表与目录发现都用 `meapet.paths` 那一个真值；两档分开报是"下错档"那种坏法的判据。"""
        from meapet.paths import VOICE_ASR_MODEL_FILES

        config = {"voice_input": {"enabled": True, "precision": "fp32"}}
        row = doctor._asr(config)
        for precision in VOICE_ASR_MODEL_FILES:
            self.assertIn(precision, row)
        self.assertIn("配置档=fp32", row)
        self.assertIn("fp32(配置档)", row)
        self.assertIn("int8(另一档)", row)


class EntryRoutingTest(unittest.TestCase):
    """`pet.main()` 里 doctor 与 GUI 的先后。

    这一组**必须**先把 `meapet.desktop.app` 换成桩：反向证伪时（把那道拦截改坏）真 GUI
    会在开发者的桌面上开起来并挂住测试进程——一个坏掉的锁应当立刻变红，不该长成一个窗口。
    """

    @staticmethod
    def _gui_stub():
        stub = types.ModuleType("meapet.desktop.app")
        stub.main = lambda: (_ for _ in ()).throw(AssertionError("走到了 GUI 主循环"))
        return stub

    def test_pet_main_routes_to_doctor_before_installing_anything(self) -> None:
        """`--doctor` 拦在 `ensure_pet_dependencies` **之前**：来取证的那台机器往往就是依赖坏的那台，
        而装依赖是写侧动作——先装再问，doctor 就不再是只读入口了。"""
        import pet

        with mock.patch.dict(sys.modules, {"meapet.desktop.app": self._gui_stub()}), \
            mock.patch("meapet.doctor.main", return_value=7) as run, \
            mock.patch("meapet.bootstrap.ensure_pet_dependencies") as install, \
            mock.patch.object(sys, "argv", ["pet.py", "--doctor", "--skip-tts"]):
            rc = pet.main()
        self.assertEqual(rc, 7)
        run.assert_called_once_with(["--doctor", "--skip-tts"])
        install.assert_not_called()

    def test_pet_main_without_the_flag_still_goes_to_the_gui(self) -> None:
        """反向那一半：不带 `--doctor` 时不许被劫持——启动链的形状不能被取证入口改。"""
        import pet

        with mock.patch.dict(sys.modules, {"meapet.desktop.app": self._gui_stub()}), \
            mock.patch("meapet.bootstrap.ensure_pet_dependencies", return_value=False) as install, \
            mock.patch("meapet.doctor.main") as run, \
            mock.patch.object(sys, "argv", ["pet.py", "--check"]):
            rc = pet.main()
        self.assertEqual(rc, 2)
        run.assert_not_called()
        install.assert_called_once()


if __name__ == "__main__":
    unittest.main()
