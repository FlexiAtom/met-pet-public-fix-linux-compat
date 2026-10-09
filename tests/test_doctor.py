"""`meapet --doctor` —— 无头只读取证入口的红线。

人工 2026-10-08 放行（逐字见 `~/.Athena/projects/meapet/voice.md`）：「好，可以动码，做无头模式」，
且改判过两条前提：「无头模式是给AI用的」「目的是测试，不论是否打包」。这里钉的是**入口的形状**，
不是各台机器读数的对错：

- **不进 GUI 就不许建 Qt 应用**——无显示服务时那一下是 abort，不是异常，catch 不到（默认关，`--with-gui` 显式开）；
- **只读**——跑一遍不许动 config 文件；
- **探测自身失败要变成一行读数**，不许把 traceback 递给调用方（"无头"两个字判的就是这一格）；
- **stdout 只有报告**——产品侧那些函数里有直接 print 的，取证期间得串到 stderr；
- **两路 TTS 各一条，且吃 diagnostics 那一份真值**——doctor 里重算就是第二套真值；
- **会碰外设的两档都要显式开关**（`--speech-latency` 起推理落 wav、`--probe-agent` 问 TCP 后
  才开一次 WebSocket 且只连回路）——默认档一格不动、一个包不发。

除 `_port_open` 那条（它要的是真 socket，绑在回环上、不出这台机器）之外，全部遮掉真健康检查
（`MeaTTS.health_check` 在本机 19s）与真连接，要钉的是接线与红线。
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
    "input_thresholds",
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


class SpeechLatencyRowTest(unittest.TestCase):
    """`--speech-latency` 是本入口**唯一的写侧**（真起推理子进程 + 落 wav），默认一档都不许跑。

    这一组全部把真健康检查遮成"不齐"：既快又顺带钉住那条顺序——前置不齐时延迟格自己写"未测"，
    不会烧掉一发十几秒到两分钟的合成去换一个失败读数。
    """

    def test_default_run_never_enters_the_synth(self) -> None:
        with mock.patch(
            "meapet.tts.diagnostics.speech_latency", side_effect=AssertionError("默认档不该起合成")
        ):
            names = [item.name for item in doctor.collect({}, skip_tts=False, skip_layer=True)]
        self.assertFalse([n for n in names if n.startswith("tts.latency.")], names)

    def test_the_flag_adds_one_row_per_engine_after_the_prerequisites(self) -> None:
        with mock.patch("meapet.tts.service.MeaTTS.health_check", lambda self: False):
            items = doctor.collect({}, skip_tts=False, skip_layer=True, measure_speech=True)
        self.assertEqual(
            [item.name for item in items if item.name.startswith("tts.")],
            ["tts.gpt_sovits", "tts.vits", "tts.latency.gpt_sovits", "tts.latency.vits"],
            "延迟格排在两格前置之后：前面任何一格炸了都不该先付两分钟的合成钱",
        )
        for item in items:
            if item.name.startswith("tts.latency."):
                self.assertTrue(item.judge, f"{item.name} 缺判据")
                self.assertIn("未测：前置不齐", item.value)

    def test_skip_tts_skips_the_slowest_rows_too(self) -> None:
        """`--skip-tts` 存在的理由就是"那两格最慢"，写侧的延迟格更该跟着跳。"""
        with mock.patch(
            "meapet.tts.diagnostics.speech_latency", side_effect=AssertionError("skip 了就不该起")
        ):
            names = [item.name for item in doctor.collect({}, skip_tts=True, measure_speech=True)]
        self.assertFalse([n for n in names if n.startswith("tts.")], names)

    def test_the_cli_flag_reaches_the_collector(self) -> None:
        self.assertTrue(doctor._parse_args(["--doctor", "--speech-latency"]).speech_latency)
        self.assertFalse(doctor._parse_args(["--doctor"]).speech_latency)
        with mock.patch.object(doctor, "collect", return_value=[]) as collect, mock.patch(
            "meapet.config.store.load_config", return_value={}
        ):
            self.assertEqual(doctor.main(["--doctor", "--speech-latency"]), 0)
        self.assertTrue(collect.call_args.kwargs["measure_speech"])


class AgentChannelProbeTest(unittest.TestCase):
    """`--probe-agent` 是第二档**会碰外设**的格（先问 TCP，在听才真开一次 WebSocket）。

    这一组要钉的是四件事：默认档一次都不连；**非回路端点报"未连接"而不是去连**（替这台机器
    往外发请求不在 doctor 的授权里，TCP 那一问也算）；端口没在听时**那一发 WebSocket 不发**；
    读数里没有任何凭据形状的东西。
    """

    class _FakeConfig:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

    class _FakeAdapter:
        def __init__(self, base_url: str, probe_error: Exception | None = None) -> None:
            self.config = AgentChannelProbeTest._FakeConfig(base_url)
            self.probe_error = probe_error
            self.probe_calls = 0
            self.close_calls = 0

        async def probe(self):
            self.probe_calls += 1
            if self.probe_error is not None:
                raise self.probe_error
            return types.SimpleNamespace(
                platform="hermes", protocol="json-rpc-2.0", streaming=True, auth_token="不该被印"
            )

        async def close(self):
            self.close_calls += 1

    def _stub_factory(self, adapter, holder=None):
        def factory(config):
            if holder is not None:
                holder.append(config)
            return adapter

        return mock.patch(
            "meapet.agent.factory.create_agent_adapter_from_config", side_effect=factory
        )

    def _port_listening(self):
        """把 TCP 那一问遮成"在听"——这一组要单测的是 WebSocket 那一发，不是端口。"""
        return mock.patch.object(doctor, "_port_open", return_value=True)

    def test_default_run_never_opens_a_socket(self) -> None:
        with mock.patch.object(
            doctor, "_agent_channel", side_effect=AssertionError("默认档不该连")
        ):
            names = [item.name for item in doctor.collect({}, skip_tts=True)]
        self.assertNotIn("agent_channel", names)

    def test_the_flag_adds_the_row_after_the_rest(self) -> None:
        with mock.patch.object(doctor, "_agent_channel", return_value="在场"):
            items = doctor.collect({}, skip_tts=True, probe_agent=True)
        self.assertEqual(items[-1].name, "agent_channel")
        self.assertTrue(items[-1].judge)

    def test_non_loopback_endpoint_is_refused_not_requested(self) -> None:
        """云端/第三方 gateway 地址：这一格必须**不去连**，并把"为什么没连"写在读数里。

        那道 TCP 预问也在禁区里——`getaddrinfo` 加一次 SYN 本身就是往外发的东西，
        所以这里的 `_port_open` 遮成"一调就炸"。
        """
        adapter = self._FakeAdapter("ws://gateway.example.com:9119/api/ws")
        with self._stub_factory(adapter), mock.patch.object(
            doctor, "_port_open", side_effect=AssertionError("非回路端点连 TCP 都不该问")
        ):
            text = doctor._agent_channel({})
        self.assertEqual(adapter.probe_calls, 0, "非回路端点被连了——那就是替这台机器往外发请求")
        self.assertIn("未连接", text)
        self.assertIn("回路", text)

    def test_absent_gateway_is_a_reading_not_a_crash(self) -> None:
        """gateway 没起是本案回退链的**预期输入**，要变成一行读数。"""
        adapter = self._FakeAdapter(
            "ws://127.0.0.1:9119/api/ws", probe_error=ConnectionRefusedError("拒绝连接")
        )
        with self._stub_factory(adapter), self._port_listening():
            text = doctor._agent_channel({})
        self.assertIn("不在场", text)
        self.assertIn("ConnectionRefusedError", text)
        self.assertGreaterEqual(adapter.close_calls, 1)

    def test_present_gateway_prints_capabilities_and_no_credential(self) -> None:
        config = {"llm": {"agent": {"kind": "hermes", "api_key": "sekret-123"}}}
        adapter = self._FakeAdapter("ws://127.0.0.1:9119/api/ws")
        with self._stub_factory(adapter), self._port_listening():
            text = doctor._agent_channel(config)
        self.assertIn("在场", text)
        self.assertIn("platform=hermes", text)
        for leak in ("sekret-123", "不该被印", "auth_token"):
            self.assertNotIn(leak, text, f"凭据形状的东西漏进读数了：{leak}")

    def test_closed_port_never_sends_the_websocket(self) -> None:
        """端口没在听就**不发那一发**。这一道把"这台机器没配 agent 凭据"与"gateway 挂了"
        分开：产品侧 `hermes.py:349` 在无 token 时连接前就抛 `ValueError`，被 `probe()` 末尾
        那个 `except Exception` 归进「握手失败」——两支在读数里同形就没了可指的那一格。
        """
        adapter = self._FakeAdapter("ws://127.0.0.1:9119/api/ws")
        with self._stub_factory(adapter), mock.patch.object(
            doctor, "_port_open", return_value=False
        ):
            text = doctor._agent_channel({})
        self.assertEqual(adapter.probe_calls, 0, "端口关着还发 WebSocket——那一发的预算白付")
        self.assertIn("未监听", text)
        self.assertIn("9119", text)

    def test_the_failure_reason_under_the_wrapper_prints_too(self) -> None:
        """产品侧 `hermes.py:596` 写的是 `raise ValueError("…握手失败") from exc`：外层那句
        对"没配 token"和"对端拒连"完全一样，真因只在 `__cause__` 里。只印外层等于把两支捏回一支。
        """
        outer = ValueError("Hermes WebSocket Gateway 握手失败")
        outer.__cause__ = ValueError("Hermes WebSocket token is required; start hermes serve")
        adapter = self._FakeAdapter("ws://127.0.0.1:9119/api/ws", probe_error=outer)
        with self._stub_factory(adapter), self._port_listening():
            text = doctor._agent_channel({})
        self.assertIn("握手失败", text)
        self.assertIn("token is required", text)

    def test_port_reading_is_taken_from_the_url_not_recomputed(self) -> None:
        self.assertEqual(doctor._endpoint_port("ws://127.0.0.1:9119/api/ws"), 9119)
        self.assertEqual(doctor._endpoint_port("wss://127.0.0.1:8766/x"), 8766)
        self.assertEqual(doctor._endpoint_port("ws://127.0.0.1"), 80)
        self.assertEqual(doctor._endpoint_port("https://127.0.0.1"), 443)
        for bad in ("ws://127.0.0.1:notaport", "ws://127.0.0.1:99999", "不是地址"):
            self.assertIsNone(doctor._endpoint_port(bad), bad)

    def test_probe_does_not_mutate_the_callers_config(self) -> None:
        """工厂会往它拿到的那份 dict 里塞 session_id——所以喂副本，调用方那份不许动。"""
        config = {"llm": {"agent": {"kind": "hermes"}}}
        seen: list[dict] = []

        def factory(mutating_config):
            mutating_config["llm"]["agent"]["session_id"] = "meapet-injected"
            seen.append(mutating_config)
            return self._FakeAdapter("ws://127.0.0.1:9119/api/ws")

        with mock.patch(
            "meapet.agent.factory.create_agent_adapter_from_config", side_effect=factory
        ), self._port_listening():
            doctor._agent_channel(config)
        self.assertEqual(seen[0]["llm"]["agent"]["session_id"], "meapet-injected")
        self.assertEqual(config, {"llm": {"agent": {"kind": "hermes"}}})

    def test_a_broken_config_becomes_a_row_not_a_traceback(self) -> None:
        with mock.patch(
            "meapet.agent.factory.create_agent_adapter_from_config",
            side_effect=ValueError("llm.agent.kind 只能是三种"),
        ):
            text = doctor._agent_channel({"llm": {"agent": {"kind": "nope"}}})
        self.assertIn("适配器构造失败", text)

    def test_loopback_host_truth_table(self) -> None:
        for host in ("127.0.0.1", "::1", "localhost", "127.8.9.10"):
            self.assertTrue(doctor._is_loopback(host), host)
        for host in ("", "gateway.example.com", "10.0.0.5", "169.254.1.1", "8.8.8.8"):
            self.assertFalse(doctor._is_loopback(host), host)

    def test_port_gate_reads_a_real_loopback_listener(self) -> None:
        """`_port_open` 自己也得有一发真的：绑一台回环、听一次、关掉再问一次。

        遮掉它，上面那几条就只剩"探针说在听"；而这道闸的存在理由正是**它比 WebSocket 那句
        更诚实**，所以要拿真 socket 验它两档读数各归各。
        """
        import socket

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        self.assertTrue(doctor._port_open("127.0.0.1", port, budget=1.0))
        listener.close()
        self.assertFalse(doctor._port_open("127.0.0.1", port, budget=1.0))

    def test_the_cli_flag_reaches_the_collector(self) -> None:
        self.assertTrue(doctor._parse_args(["--doctor", "--probe-agent"]).probe_agent)
        self.assertFalse(doctor._parse_args(["--doctor"]).probe_agent)
        with mock.patch.object(doctor, "collect", return_value=[]) as collect, mock.patch(
            "meapet.config.store.load_config", return_value={}
        ):
            self.assertEqual(doctor.main(["--doctor", "--probe-agent"]), 0)
        self.assertTrue(collect.call_args.kwargs["probe_agent"])


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

    def test_input_thresholds_default_also_builds_nothing(self) -> None:
        """这一格和 screens 同档：默认那一下同样不能建 Qt 应用。"""
        from PyQt5.QtCore import QCoreApplication

        had = QCoreApplication.instance() is not None
        item = doctor._probe("input_thresholds", "判据", lambda: doctor._input_thresholds(False))
        self.assertEqual(QCoreApplication.instance() is not None, had)
        self.assertIn("未取", item.value)

    def test_input_thresholds_reads_the_product_names_not_copied_numbers(self) -> None:
        """产品门票那两个数必须**读自** `live2d_widget` 的名字。

        抄一份数字进来就是第二套真值：改一处漏一处，而这格存在的意义正是把"系统给的门槛"
        和"产品写死的门槛"并排印出来——两边都得是真值才有得比。
        """
        from meapet.desktop import live2d_widget

        with mock.patch.object(doctor, "_qt_app") as app_stub:
            hints = mock.Mock()
            hints.mouseDoubleClickInterval.return_value = 400
            hints.startDragDistance.return_value = 10
            app_stub.return_value = mock.Mock(platformName=lambda: "stub", styleHints=lambda: hints)
            text = doctor._input_thresholds(True)
        self.assertIn("doubleClickInterval=400ms", text)
        self.assertIn("startDragDistance=10px", text)
        self.assertIn(
            f"产品门票={live2d_widget.ZONE_CLICK_MAX_DISTANCE_PX}px"
            f"/{live2d_widget.ZONE_CLICK_MAX_HOLD_SECONDS}s",
            text,
        )

    def test_partition_gate_constants_exist_where_the_widget_reads_them(self) -> None:
        """门票从 `mouseReleaseEvent` 里提出来落到模块名上，条件不能因此变形。"""
        from meapet.desktop import live2d_widget

        self.assertEqual(live2d_widget.ZONE_CLICK_MAX_DISTANCE_PX, 35)
        self.assertEqual(live2d_widget.ZONE_CLICK_MAX_HOLD_SECONDS, 0.4)


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
