"""假 Agent 端（`meapet/agent/fake_agent.py`）的契约：剧本、落队计数、回路闸、凭据面。

这里跑的是**真 WebSocket**（`websockets.asyncio.server` + 真 `AgentLinkAdapter`），不是假 socket：
本案要的那句话是"帧真走到了客户端那段搬运"，用假 socket 自证就等于自己宣布自己通过。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import unittest
from pathlib import Path

import pytest

from meapet.agent import agent_link
from meapet.agent.agent_link import COUNTER_FIELDS, AgentLinkAdapter, AgentLinkConfig
from meapet.agent.base import ToolStatus
from meapet.agent.fake_agent import (
    DEFAULT_SCRIPT,
    FakeAgentScriptError,
    load_script,
    scripted_agent_server,
)
from meapet.agent.link_protocol import make_agent_link_frame


def _adapter(url: str, *, auth_token: str = "") -> AgentLinkAdapter:
    return AgentLinkAdapter(
        AgentLinkConfig(
            base_url=url,
            auth_token=auth_token,
            device_id="device-fake",
            session_id="session-fake",
            timeout_seconds=5,
        )
    )


class DefaultScriptTest(unittest.IsolatedAsyncioTestCase):
    """内置那四步各自的后果。**每一格都要单独钉**：那一行 doctor 读数里出现的每个数，
    在这里都得有一条测试说"它不是碰巧是这个值"。
    """

    async def test_the_four_queueable_frames_land_and_only_the_bad_one_gets_a_receipt(self):
        async with scripted_agent_server() as server:
            adapter = _adapter(server.url)
            try:
                await adapter.probe()
                result = await server.wait_result(5)
                idle = adapter.take_idle_tool_statuses()
            finally:
                await adapter.close()

        assert result.queueable_sent == 4, "假端按同判据数出来的分母不是 4"
        assert len(idle) == 4, f"落队 {len(idle)} 与分母 {result.queueable_sent} 不一致＝搬运坏了"
        assert idle[0].state == "running"
        assert idle[0].safe_text == "假过程帧 #1", "内容没跟着走，那串数字只是计数"
        # **计数也得到客户端那侧**，且只在剧本挂了的那两发上出现：四发全带的话这一格
        # 会退化成把上面那个「落队」再抄一遍。第 2 发那个带小数的 elapsed_ms 是 JS 网关常态。
        assert result.counter_sent == 2, "剧本挂在头两发的计数，分母应当是 2"
        assert (idle[0].call_index, idle[0].elapsed_ms, idle[0].tokens) == (1, 250, 40)
        assert (idle[1].call_index, idle[1].elapsed_ms, idle[1].tokens) == (2, 501, None)
        assert (idle[2].call_index, idle[2].elapsed_ms, idle[2].tokens) == (None, None, None)
        # **能解析**的那四发只入队、不发回执（agent_link.py:649-655）；唯一那一发回执
        # 来自"解析不出 + required"那一步。多出任何一发都要当场看见。
        assert result.error_codes == ("NO_ACTIVE_TURN",)
        # 握手自带一份快照，`tools.refresh` 那一步要到第二份。
        assert result.snapshot_seen == 2, f"tools.refresh 没换来第二份快照：{result.snapshot_seen}"
        assert result.barrier_acked is True, "pong 没到＝前三步没跑完，那串计数都是悬空的"
        assert result.sent == 7, "四步：4 可解析 + 1 解析不出 + refresh + ping"


class EnqueueBreakageTest(unittest.IsolatedAsyncioTestCase):
    """反证那一格：**分母不从分子那侧算**，否则把搬运打坏会读出"0/0"这种看不出的坏法。"""

    async def test_a_broken_enqueue_reads_zero_of_four_not_zero_of_zero(self):
        original = AgentLinkAdapter._tool_status_from_payload
        AgentLinkAdapter._tool_status_from_payload = lambda self, payload: None
        try:
            async with scripted_agent_server() as server:
                adapter = _adapter(server.url)
                try:
                    await adapter.probe()
                    result = await server.wait_result(5)
                    idle = adapter.take_idle_tool_statuses()
                finally:
                    await adapter.close()
        finally:
            AgentLinkAdapter._tool_status_from_payload = original

        assert len(idle) == 0
        assert result.queueable_sent == 4, "分子被打坏了，分母必须还是 4——否则这句读不出是坏了"


class CounterBreakageTest(unittest.IsolatedAsyncioTestCase):
    """上面那一格的计数版：把 `safe_counter` 打坏，分子归 0、分母不动。

    假端在 `import` 时就把 `safe_counter` 这个名字绑到了自己那份引用，所以这里只打得到
    客户端那一份——正好是要的形状：`计数 0/2` 而不是 `0/0`。
    """

    async def test_a_broken_counter_parser_reads_zero_of_two_not_zero_of_zero(self):
        original = agent_link.safe_counter
        agent_link.safe_counter = lambda value: None
        try:
            async with scripted_agent_server() as server:
                adapter = _adapter(server.url)
                try:
                    await adapter.probe()
                    result = await server.wait_result(5)
                    idle = adapter.take_idle_tool_statuses()
                finally:
                    await adapter.close()
        finally:
            agent_link.safe_counter = original

        assert [(status.call_index, status.elapsed_ms, status.tokens) for status in idle] == [
            (None, None, None)
        ] * 4
        assert len(idle) == 4, "计数解析坏了不该把整条状态文本也丢掉——入队只看 state/safe_text"
        assert result.queueable_sent == 4
        assert result.counter_sent == 2, "分母是假端那一份引用，不该跟着客户端那侧一起归零"


class CredentialSurfaceTest(unittest.IsolatedAsyncioTestCase):
    """假端**不读凭据**：只记 token 的长度，值不进任何结果。

    这格不是风格：`factory._resolve_secret` 把 env 排在任何 config 字面量之前，所以
    `--agent-script` 那趟真可能带着机器上那枚 token 上握手（doctor 侧临时摘 env 是另一道闸）。
    长度这一格证明摘掉了；"值不在结果里"证明就算没摘，也没有第二个可能被印出来的去处。
    """

    async def test_token_value_never_reaches_the_result(self):
        secret = "super-secret-token-should-not-appear"
        async with scripted_agent_server() as server:
            adapter = _adapter(server.url, auth_token=secret)
            try:
                await adapter.probe()
                result = await server.wait_result(5)
            finally:
                await adapter.close()

        assert result.hello_token_lengths == (len(secret),)
        assert secret not in str(result)
        assert secret not in repr(result.received_types)


class LoopbackGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_non_loopback_host_is_refused_before_anything_listens(self):
        with pytest.raises(FakeAgentScriptError, match="不是回路"):
            async with scripted_agent_server(host="10.0.0.1"):
                pass

    async def test_an_empty_script_is_refused(self):
        with pytest.raises(FakeAgentScriptError, match="空的"):
            async with scripted_agent_server(script=()):
                pass


class LoadScriptTest(unittest.TestCase):
    """外部剧本：**校验在场、越权写法当场拒**。文件不该能把这一趟从回路里拽出去。

    这一格用 `tempfile` 而不是 pytest 的 `tmp_path`：本案的异步那几格要
    `unittest.IsolatedAsyncioTestCase`，同一个文件里混两种 fixture 口径会把同步这几格
    直接打成 TypeError（`unittest` 不认 `tmp_path` 这个位置参数）。
    """

    def setUp(self):
        import tempfile

        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)

    def _write(self, document: dict) -> str:
        path = Path(self._folder.name) / f"script-{abs(hash(json.dumps(document, sort_keys=True)))}.json"
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_a_frame_cannot_steal_the_session(self):
        path = self._write(
            {"steps": [{"frames": [{"type": "control.ping", "payload": {}}], "session_id": "别的会话"}]}
        )
        with pytest.raises(FakeAgentScriptError, match="不由剧本定"):
            load_script(path)

    def test_the_top_level_cannot_carry_an_address(self):
        path = self._write(
            {"steps": [{"frames": [{"type": "control.ping"}]}], "base_url": "ws://example.com/x"}
        )
        with pytest.raises(FakeAgentScriptError, match="顶层"):
            load_script(path)

    def test_unknown_frame_keys_are_named(self):
        path = self._write(
            {"steps": [{"frames": [{"type": "control.ping", "extensions": {}}]}]}
        )
        with pytest.raises(FakeAgentScriptError, match="extensions"):
            load_script(path)

    def test_errors_carry_which_step_so_a_ten_step_script_is_locatable(self):
        path = self._write(
            {
                "steps": [
                    {"frames": [{"type": "control.ping"}]},
                    {"frames": [{"payload": {}}]},
                ]
            }
        )
        with pytest.raises(FakeAgentScriptError, match=r"第 2 步 第 1 帧: 没有 type"):
            load_script(path)

    def test_an_empty_steps_array_is_refused(self):
        path = self._write({"steps": []})
        with pytest.raises(FakeAgentScriptError, match="steps 是空的"):
            load_script(path)

    def test_a_frame_is_validated_at_load_time_not_at_send_time(self):
        """`type` 写成非法形态（大写/缺点号）时，`make_agent_link_frame` 在 load 就抛。"""
        path = self._write({"steps": [{"frames": [{"type": "ChatToolStatus"}]}]})
        with pytest.raises(Exception, match="type"):
            load_script(path)


class ExternalScriptRunTest(unittest.IsolatedAsyncioTestCase):
    """外部剧本要真能跑，而且要按 `wait_for` 的顺序跑——否者上面那些校验只是文本游戏。"""

    async def test_wait_for_gates_the_step_and_the_frames_still_arrive(self):
        document = {
            "steps": [
                {
                    "frames": [
                        {
                            "type": "chat.tool_status",
                            "payload": {"state": "done", "safe_text": "外部剧本那一发"},
                            "id": "ext-1",
                        }
                    ],
                    # 等握手后那份快照到了再发——不发在 `_ready` 之前，免得入队那一档撞时序。
                    "wait_for": "tools.snapshot",
                },
                {"frames": [{"type": "control.ping", "id": "ext-2"}]},
            ]
        }
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "script.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            steps = load_script(str(path))
            async with scripted_agent_server(script=steps) as server:
                adapter = _adapter(server.url)
                try:
                    await adapter.probe()
                    result = await server.wait_result(5)
                    idle = adapter.take_idle_tool_statuses()
                finally:
                    await adapter.close()

        assert [status.safe_text for status in idle] == ["外部剧本那一发"]
        assert result.queueable_sent == 1
        assert result.barrier_acked is True
        # 剧本里写的 `id` 就是上线的那一个：稳定 ID 让"哪一步坏了"读得出来。
        assert steps[0].frames[0]["id"] == "ext-1"


class ScriptShapeTest(unittest.TestCase):
    def test_the_default_script_keeps_queueable_frames_well_below_the_eviction_line(self):
        """`_MAX_IDLE_TOOL_STATUSES = 16`：贴着那条线，"落队 N"会被淘汰揉成"N 封顶"。"""
        queueable = sum(
            1
            for step in DEFAULT_SCRIPT
            for frame in step.frames
            if frame["type"] == "chat.tool_status"
            and str(frame["payload"].get("state") or "").strip()
            and str(frame["payload"].get("safe_text") or "").strip()
        )
        assert queueable <= 5, f"内置剧本队列型帧 {queueable} 发，太多"

    def test_the_last_default_step_is_the_ping_barrier(self):
        """屏障必须真的是最后一步：串行派发下，pong 到了才说前面都跑完了。"""
        assert DEFAULT_SCRIPT[-1].frames[-1]["type"] == "control.ping"

    def test_the_counter_names_on_the_wire_match_the_carrier_fields(self):
        """载荷键名、`_tool_status_from_payload` 取的那串、`ToolStatus` 上的字段名是同一件事写三处。

        这里钉住后两处（第一处由协议文档 §7.2 与上面那条真 WebSocket 测试钉）：漂移的后果是
        某个计数静默变成 None，而读数那一格看不出区别。
        """
        carried = {
            field.name
            for field in dataclasses.fields(ToolStatus)
            if field.name not in ("state", "safe_text")
        }
        assert set(COUNTER_FIELDS) == carried

    def test_frames_are_built_envelope_valid_at_definition_time(self):
        from meapet.agent.link_protocol import AgentLinkFrame

        for step in DEFAULT_SCRIPT:
            for frame in step.frames:
                parsed = AgentLinkFrame.parse(frame)
                assert parsed.type == frame["type"]
        assert make_agent_link_frame("control.ping", {})["version"] == "1.0"
