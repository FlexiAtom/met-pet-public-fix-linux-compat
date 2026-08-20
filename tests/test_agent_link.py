"""通用 Agent Link v1 字段、聊天与前端工具契约。"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import unittest
from unittest import mock

from meapet.agent.base import AgentTurnRequest, TurnCancelled, TurnCompleted


def _reply(text: str = "连接成功") -> str:
    return (
        f"<MEA_PET_SEGMENT><DISPLAY>{text}</DISPLAY>"
        f'<META>{{"voice_text":"{text}","voice_language":"zh-CN",'
        '"mood":"happy","tts_style":""}</META>'
        "</MEA_PET_SEGMENT><MEA_PET_DONE />"
    )


class _Socket:
    def __init__(self, handler=None):
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.handler = handler

    def push(self, frame: dict) -> None:
        self.incoming.put_nowait(json.dumps(frame, ensure_ascii=False))

    async def recv(self) -> str:
        value = await self.incoming.get()
        if isinstance(value, BaseException):
            raise value
        return str(value)

    async def send(self, data: str) -> None:
        frame = json.loads(data)
        self.sent.append(frame)
        if self.handler is not None:
            self.handler(self, frame)

    async def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, socket: _Socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, _exc_type, _exc, _tb):
        await self.socket.close()


class _Connector:
    def __init__(self, *sockets: _Socket):
        self.sockets = list(sockets)
        self.calls = []

    def __call__(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return _Connection(self.sockets.pop(0))


class _ProxyAwareConnector:
    def __init__(self, socket: _Socket):
        self.socket = socket
        self.proxy = True

    def __call__(self, url: str, *, proxy=True, **_kwargs):
        self.proxy = proxy
        return _Connection(self.socket)


def _ready(hello: dict) -> dict:
    from meapet.agent.link_protocol import make_agent_link_frame

    return make_agent_link_frame(
        "control.ready",
        {
            "version": "1.0",
            "authenticated": True,
            "agent_name": "测试 Agent",
            "server_version": "test",
            "capabilities": {
                "chat": {
                    "submit": True,
                    "streaming": True,
                    "cancel": True,
                },
                "tools": {
                    "dynamic": True,
                    "call": True,
                    "cancel": True,
                },
            },
            "required_extensions": [],
        },
        session_id=hello["session_id"],
        reply_to=hello["id"],
    )


class TestAgentLinkEnvelope(unittest.TestCase):
    def test_fixed_envelope_accepts_namespaced_extensions(self):
        from meapet.agent.link_protocol import (
            AgentLinkFrame,
            make_agent_link_frame,
        )

        raw = make_agent_link_frame(
            "chat.submit",
            {"content": "你好"},
            message_id="turn-1",
            session_id="session-1",
            extensions={"vendor.trace": {"enabled": True}},
        )
        parsed = AgentLinkFrame.parse(raw)

        self.assertEqual(parsed.type, "chat.submit")
        self.assertEqual(parsed.extensions["vendor.trace"]["enabled"], True)
        self.assertEqual(
            set(parsed.to_dict()),
            {
                "version",
                "type",
                "id",
                "session_id",
                "reply_to",
                "payload",
                "extensions",
            },
        )

    def test_rejects_unknown_major_and_unscoped_extension(self):
        from meapet.agent.link_protocol import (
            AgentLinkFrame,
            AgentLinkProtocolError,
            make_agent_link_frame,
        )

        valid = make_agent_link_frame("control.ping")
        with self.assertRaisesRegex(AgentLinkProtocolError, "主版本"):
            AgentLinkFrame.parse({**valid, "version": "2.0"})
        with self.assertRaisesRegex(AgentLinkProtocolError, "命名空间"):
            make_agent_link_frame(
                "control.ping",
                extensions={"trace": True},
            )
        with self.assertRaisesRegex(AgentLinkProtocolError, "必须是字符串"):
            AgentLinkFrame.parse({**valid, "id": 123})

    def test_version_must_be_numeric_major_minor_but_accepts_v1_minor(self):
        from meapet.agent.link_protocol import (
            AgentLinkFrame,
            AgentLinkProtocolError,
            make_agent_link_frame,
        )

        valid = make_agent_link_frame("control.ping")
        self.assertEqual(
            AgentLinkFrame.parse({**valid, "version": "1.7"}).version,
            "1.7",
        )
        for malformed in ("1", "1.", "1.x", "01.0", "1.0.0"):
            with self.subTest(version=malformed):
                with self.assertRaisesRegex(
                    AgentLinkProtocolError,
                    "major.minor",
                ):
                    AgentLinkFrame.parse({**valid, "version": malformed})


class TestCapabilityRegistry(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_hides_request_id_and_call_injects_transport_id(self):
        from meapet.control.capabilities import CapabilityRegistry

        received = {}

        async def remember(
            value: str,
            request_id: str = "",
        ) -> dict[str, str]:
            received["request_id"] = request_id
            return {"value": value}

        registry = CapabilityRegistry()
        registry.add_tool(
            remember,
            name="meapet.remember",
            description="保存一个测试值并返回该值。",
        )

        definition = registry.protocol_snapshot()["tools"][0]
        self.assertNotIn(
            "request_id",
            definition["input_schema"]["properties"],
        )
        result = await registry.call(
            "meapet.remember",
            {"value": "ok"},
            request_id="call-1",
        )
        self.assertEqual(result, {"value": "ok"})
        self.assertEqual(received["request_id"], "call-1")

    async def test_invalid_arguments_are_typed(self):
        from meapet.control.capabilities import (
            CapabilityArgumentsError,
            CapabilityRegistry,
        )

        async def needs_count(count: int) -> dict[str, int]:
            return {"count": count}

        registry = CapabilityRegistry()
        registry.add_tool(
            needs_count,
            name="meapet.needs_count",
            description="接收一个整数并返回。",
        )
        with self.assertRaises(CapabilityArgumentsError):
            await registry.call(
                "meapet.needs_count",
                {"count": "not-an-integer"},
                request_id="call-invalid",
            )

    async def test_mcp_and_agent_link_share_the_same_schema_source(self):
        from meapet.control.broker import CompanionControlBroker
        from meapet.control.capabilities import build_companion_capabilities
        from meapet.control.mcp_server import build_companion_mcp

        broker = CompanionControlBroker(state={})
        registry = build_companion_capabilities(broker)
        server = build_companion_mcp(broker, registry=registry)
        mcp_tools = {tool.name: tool for tool in await server.list_tools()}

        self.assertEqual(
            set(mcp_tools),
            {tool.name for tool in registry.tools()},
        )
        for tool in registry.tools():
            with self.subTest(tool=tool.name):
                mcp_schema = copy.deepcopy(mcp_tools[tool.name].inputSchema)
                mcp_schema.get("properties", {}).pop("request_id", None)
                if isinstance(mcp_schema.get("required"), list):
                    mcp_schema["required"] = [
                        name
                        for name in mcp_schema["required"]
                        if name != "request_id"
                    ]
                self.assertEqual(mcp_schema, tool.input_schema)
                self.assertEqual(
                    mcp_tools[tool.name].description,
                    tool.description,
                )


class TestAgentLinkFactory(unittest.TestCase):
    def test_factory_builds_generic_adapter_and_generates_stable_ids(self):
        from meapet.agent.agent_link import AgentLinkAdapter
        from meapet.agent.factory import create_agent_adapter_from_config

        config = {
            "llm": {
                "mode": "agent",
                "agent": {
                    "kind": "agent_link",
                    "base_url": "ws://127.0.0.1:8766/agent-link",
                    "auth_token": "$MEA_PET_AGENT_LINK_TOKEN",
                },
            }
        }
        with mock.patch.dict(
            os.environ,
            {"MEA_PET_AGENT_LINK_TOKEN": "agent-link-secret"},
            clear=False,
        ):
            first = create_agent_adapter_from_config(config)
            second = create_agent_adapter_from_config(config)

        self.assertIsInstance(first, AgentLinkAdapter)
        self.assertEqual(first.config.auth_token, "agent-link-secret")
        self.assertEqual(first.config.device_id, second.config.device_id)
        self.assertEqual(first.config.session_id, second.config.session_id)


class TestAgentLinkTransport(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_connection_bypasses_automatic_system_proxy(self):
        from meapet.agent.ws_transport import PersistentJsonWebSocket

        connector = _ProxyAwareConnector(_Socket())
        transport = PersistentJsonWebSocket(
            "ws://127.0.0.1:8766/agent-link",
            timeout_seconds=2,
            connector=connector,
        )

        await transport.ensure_connected()
        await transport.close()

        self.assertIsNone(connector.proxy)


class TestAgentLinkAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_frame_from_another_session(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig
        from meapet.agent.link_protocol import (
            AgentLinkProtocolError,
            make_agent_link_frame,
        )

        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=2,
            )
        )
        with self.assertRaisesRegex(AgentLinkProtocolError, "会话 ID"):
            await adapter._dispatch_frame(
                make_agent_link_frame(
                    "control.ping",
                    session_id="another-session",
                )
            )
        await adapter.close()

    async def test_real_websocket_completes_handshake_chat_and_tool_call(self):
        from websockets.asyncio.server import serve

        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig
        from meapet.agent.link_protocol import make_agent_link_frame
        from meapet.control.broker import CompanionControlBroker
        from meapet.control.capabilities import build_companion_capabilities

        loop = asyncio.get_running_loop()
        tool_result = loop.create_future()
        received_types = []

        async def handler(websocket) -> None:
            hello = json.loads(await websocket.recv())
            received_types.append(hello["type"])
            self.assertEqual(
                hello["payload"]["client"]["id"],
                "meapet",
            )
            await websocket.send(
                json.dumps(_ready(hello), ensure_ascii=False)
            )

            snapshot = json.loads(await websocket.recv())
            received_types.append(snapshot["type"])
            await websocket.send(
                json.dumps(
                    make_agent_link_frame(
                        "tool.call",
                        {
                            "name": "meapet.get_state",
                            "arguments": {},
                        },
                        message_id="real-tool-1",
                        session_id=hello["session_id"],
                    ),
                    ensure_ascii=False,
                )
            )

            chat_done = False
            tool_done = False
            while not chat_done or not tool_done:
                frame = json.loads(await websocket.recv())
                received_types.append(frame["type"])
                if frame["type"] == "chat.submit":
                    await websocket.send(
                        json.dumps(
                            make_agent_link_frame(
                                "chat.delta",
                                {
                                    "seq": 1,
                                    "text": _reply("真实连接成功"),
                                },
                                session_id=hello["session_id"],
                                reply_to=frame["id"],
                            ),
                            ensure_ascii=False,
                        )
                    )
                    await websocket.send(
                        json.dumps(
                            make_agent_link_frame(
                                "chat.final",
                                {},
                                session_id=hello["session_id"],
                                reply_to=frame["id"],
                            ),
                            ensure_ascii=False,
                        )
                    )
                    chat_done = True
                elif frame["type"] == "tool.result":
                    if not tool_result.done():
                        tool_result.set_result(frame)
                    tool_done = True

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            adapter = AgentLinkAdapter(
                AgentLinkConfig(
                    base_url=f"ws://127.0.0.1:{port}/agent-link",
                    auth_token="secret",
                    device_id="device-real",
                    session_id="session-real",
                    timeout_seconds=3,
                )
            )
            adapter.bind_capability_registry(
                build_companion_capabilities(
                    CompanionControlBroker(
                        state={
                            "frontend_capabilities": {"renderer": "png"},
                            "companion_state": {"busy": False},
                        }
                    )
                )
            )
            try:
                events = [
                    event
                    async for event in adapter.stream_turn(
                        AgentTurnRequest(
                            turn_id="turn-real",
                            user_text="端到端测试",
                        )
                    )
                ]
                result_frame = await asyncio.wait_for(tool_result, timeout=3)
            finally:
                await adapter.close()

        completed = [
            event for event in events if isinstance(event, TurnCompleted)
        ]
        self.assertEqual(
            completed[0].result.segments[0].display_text,
            "真实连接成功",
        )
        self.assertEqual(received_types[:2], ["control.hello", "tools.snapshot"])
        self.assertIn("chat.submit", received_types)
        self.assertIn("tool.accepted", received_types)
        self.assertEqual(result_frame["reply_to"], "real-tool-1")
        self.assertEqual(
            result_frame["payload"]["result"]["frontend_capabilities"][
                "renderer"
            ],
            "png",
        )

    async def test_probe_rejects_backend_without_chat_capability(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig

        def handler(socket: _Socket, frame: dict) -> None:
            if frame["type"] != "control.hello":
                return
            ready = _ready(frame)
            ready["payload"]["capabilities"]["chat"]["submit"] = False
            socket.push(ready)

        socket = _Socket(handler)
        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
        )

        with self.assertRaisesRegex(ValueError, "聊天请求能力"):
            await adapter.probe()
        await adapter.close()

    async def test_probe_rejects_malformed_selected_version(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig

        def handler(socket: _Socket, frame: dict) -> None:
            if frame["type"] != "control.hello":
                return
            ready = _ready(frame)
            ready["payload"]["version"] = "1.x"
            socket.push(ready)

        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=2,
            ),
            connector=_Connector(_Socket(handler)),
        )

        with self.assertRaisesRegex(ValueError, "major.minor"):
            await adapter.probe()
        await adapter.close()

    async def test_one_connection_streams_chat_and_executes_frontend_tool(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig
        from meapet.agent.link_protocol import make_agent_link_frame
        from meapet.control.broker import CompanionControlBroker
        from meapet.control.capabilities import build_companion_capabilities

        tool_call_sent = False

        def handler(socket: _Socket, frame: dict) -> None:
            nonlocal tool_call_sent
            if frame["type"] == "control.hello":
                socket.push(_ready(frame))
            elif frame["type"] == "tools.snapshot" and not tool_call_sent:
                tool_call_sent = True
                socket.push(
                    make_agent_link_frame(
                        "tool.call",
                        {
                            "name": "meapet.get_state",
                            "arguments": {},
                        },
                        message_id="tool-state-1",
                        session_id=frame["session_id"],
                    )
                )
            elif frame["type"] == "chat.submit":
                socket.push(
                    make_agent_link_frame(
                        "chat.delta",
                        {"seq": 1, "text": _reply()},
                        session_id=frame["session_id"],
                        reply_to=frame["id"],
                    )
                )
                socket.push(
                    make_agent_link_frame(
                        "chat.final",
                        {},
                        session_id=frame["session_id"],
                        reply_to=frame["id"],
                    )
                )

        socket = _Socket(handler)
        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                auth_token="secret",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
        )
        adapter.bind_capability_registry(
            build_companion_capabilities(
                CompanionControlBroker(
                    state={
                        "frontend_capabilities": {"renderer": "png"},
                        "companion_state": {"busy": False},
                    }
                )
            )
        )

        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(turn_id="turn-1", user_text="你好")
            )
        ]
        for _ in range(20):
            if any(frame["type"] == "tool.result" for frame in socket.sent):
                break
            await asyncio.sleep(0)
        await adapter.close()

        completed = [
            event for event in events if isinstance(event, TurnCompleted)
        ]
        self.assertEqual(
            completed[0].result.segments[0].display_text,
            "连接成功",
        )
        frame_types = [frame["type"] for frame in socket.sent]
        self.assertEqual(frame_types[:2], ["control.hello", "tools.snapshot"])
        self.assertIn("chat.submit", frame_types)
        self.assertIn("tool.accepted", frame_types)
        self.assertLess(
            frame_types.index("tool.accepted"),
            frame_types.index("tool.result"),
        )
        result = next(
            frame for frame in socket.sent if frame["type"] == "tool.result"
        )
        self.assertEqual(result["reply_to"], "tool-state-1")
        self.assertEqual(
            result["payload"]["result"]["frontend_capabilities"]["renderer"],
            "png",
        )

    async def test_reconnect_resends_active_chat_with_same_id(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig
        from meapet.agent.link_protocol import make_agent_link_frame

        first_submit_ids = []
        second_submit_ids = []

        def first_handler(socket: _Socket, frame: dict) -> None:
            if frame["type"] == "control.hello":
                socket.push(_ready(frame))
            elif frame["type"] == "chat.submit":
                first_submit_ids.append(frame["id"])
                socket.incoming.put_nowait(OSError("测试断线"))

        def second_handler(socket: _Socket, frame: dict) -> None:
            if frame["type"] == "control.hello":
                socket.push(_ready(frame))
            elif frame["type"] == "chat.submit":
                second_submit_ids.append(frame["id"])
                socket.push(
                    make_agent_link_frame(
                        "chat.delta",
                        {"seq": 1, "text": _reply("重连成功")},
                        session_id=frame["session_id"],
                        reply_to=frame["id"],
                    )
                )
                socket.push(
                    make_agent_link_frame(
                        "chat.final",
                        {},
                        session_id=frame["session_id"],
                        reply_to=frame["id"],
                    )
                )

        connector = _Connector(
            _Socket(first_handler),
            _Socket(second_handler),
        )
        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=3,
            ),
            connector=connector,
        )

        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(turn_id="turn-reconnect", user_text="继续")
            )
        ]
        await adapter.close()

        completed = [
            event for event in events if isinstance(event, TurnCompleted)
        ]
        self.assertEqual(
            completed[0].result.segments[0].display_text,
            "重连成功",
        )
        self.assertEqual(first_submit_ids, ["turn-reconnect"])
        self.assertEqual(second_submit_ids, ["turn-reconnect"])
        self.assertEqual(len(connector.calls), 2)

    async def test_duplicate_terminal_tool_call_is_not_executed_twice(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig
        from meapet.agent.link_protocol import make_agent_link_frame
        from meapet.control.capabilities import CapabilityRegistry

        executions = 0
        results_received = asyncio.Event()
        call_sent = False

        async def echo(value: str) -> dict[str, str]:
            nonlocal executions
            executions += 1
            return {"value": value}

        def handler(socket: _Socket, frame: dict) -> None:
            nonlocal call_sent
            if frame["type"] == "control.hello":
                socket.push(_ready(frame))
            elif frame["type"] == "tools.snapshot" and not call_sent:
                call_sent = True
                socket.push(
                    make_agent_link_frame(
                        "tool.call",
                        {
                            "name": "meapet.echo",
                            "arguments": {"value": "ok"},
                        },
                        message_id="tool-idempotent",
                        session_id=frame["session_id"],
                    )
                )
            elif frame["type"] == "tool.result":
                result_count = sum(
                    item["type"] == "tool.result" for item in socket.sent
                )
                if result_count == 1:
                    socket.push(
                        make_agent_link_frame(
                            "tool.call",
                            {
                                "name": "meapet.echo",
                                "arguments": {"value": "ok"},
                            },
                            message_id="tool-idempotent",
                            session_id=frame["session_id"],
                        )
                    )
                elif result_count == 2:
                    results_received.set()

        registry = CapabilityRegistry()
        registry.add_tool(
            echo,
            name="meapet.echo",
            description="回显一个字符串，用于验证工具调用幂等性。",
        )
        socket = _Socket(handler)
        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
        )
        adapter.bind_capability_registry(registry)

        await adapter.probe()
        await asyncio.wait_for(results_received.wait(), timeout=2)
        await adapter.close()

        self.assertEqual(executions, 1)
        results = [
            frame for frame in socket.sent if frame["type"] == "tool.result"
        ]
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    async def test_registry_change_pushes_a_complete_new_tool_snapshot(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig
        from meapet.control.capabilities import CapabilityRegistry

        snapshots_received = asyncio.Event()

        def handler(socket: _Socket, frame: dict) -> None:
            if frame["type"] == "control.hello":
                socket.push(_ready(frame))
            elif frame["type"] == "tools.snapshot":
                snapshot_count = sum(
                    item["type"] == "tools.snapshot" for item in socket.sent
                )
                if snapshot_count == 2:
                    snapshots_received.set()

        async def first_tool() -> dict[str, str]:
            return {"tool": "first"}

        async def second_tool() -> dict[str, str]:
            return {"tool": "second"}

        registry = CapabilityRegistry()
        registry.add_tool(
            first_tool,
            name="meapet.first",
            description="返回第一个测试结果。",
        )
        socket = _Socket(handler)
        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
        )
        adapter.bind_capability_registry(registry)

        await adapter.probe()
        registry.add_tool(
            second_tool,
            name="meapet.second",
            description="返回第二个测试结果。",
        )
        await asyncio.wait_for(snapshots_received.wait(), timeout=2)
        await adapter.close()

        snapshots = [
            frame
            for frame in socket.sent
            if frame["type"] == "tools.snapshot"
        ]
        self.assertEqual(
            {tool["name"] for tool in snapshots[-1]["payload"]["tools"]},
            {"meapet.first", "meapet.second"},
        )
        self.assertGreater(
            snapshots[-1]["payload"]["revision"],
            snapshots[0]["payload"]["revision"],
        )

    async def test_close_wakes_active_chat_without_waiting_for_timeout(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig

        submitted = asyncio.Event()

        def handler(socket: _Socket, frame: dict) -> None:
            if frame["type"] == "control.hello":
                socket.push(_ready(frame))
            elif frame["type"] == "chat.submit":
                submitted.set()

        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=30,
            ),
            connector=_Connector(_Socket(handler)),
        )

        async def collect_events():
            return [
                event
                async for event in adapter.stream_turn(
                    AgentTurnRequest(turn_id="turn-close", user_text="等待")
                )
            ]

        stream_task = asyncio.create_task(collect_events())
        await asyncio.wait_for(submitted.wait(), timeout=2)
        await asyncio.wait_for(adapter.close(), timeout=1)
        events = await asyncio.wait_for(stream_task, timeout=1)

        self.assertTrue(
            any(isinstance(event, TurnCancelled) for event in events)
        )

    async def test_cancel_uses_chat_cancel_with_original_request_id(self):
        from meapet.agent.agent_link import AgentLinkAdapter, AgentLinkConfig

        def handler(socket: _Socket, frame: dict) -> None:
            if frame["type"] == "control.hello":
                socket.push(_ready(frame))

        socket = _Socket(handler)
        adapter = AgentLinkAdapter(
            AgentLinkConfig(
                base_url="ws://127.0.0.1:8766/agent-link",
                device_id="device-test",
                session_id="session-test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
        )
        await adapter.probe()
        await adapter.cancel_turn("turn-cancel")
        await adapter.close()

        cancel = next(
            frame for frame in socket.sent if frame["type"] == "chat.cancel"
        )
        self.assertEqual(cancel["reply_to"], "turn-cancel")
        self.assertEqual(cancel["payload"]["request_id"], "turn-cancel")


class TestAgentLinkDesktopInitialization(unittest.TestCase):
    def test_generated_device_id_is_persisted_with_existing_session(self):
        from meapet.conversation.timeline import ConversationKey
        from meapet.desktop.app import MeaPet

        class Memory:
            @staticmethod
            def load_conversation_turns():
                return ()

            @staticmethod
            def save_conversation_turn(_turn, *, max_turns):
                return max_turns

        class Host:
            def __init__(self):
                self.config = {
                    "llm": {
                        "mode": "agent",
                        "agent": {
                            "kind": "agent_link",
                            "base_url": "ws://127.0.0.1:8766/agent-link",
                            "session_id": "session-existing",
                            "device_id": "",
                        },
                    },
                    "ui": {"timeline_turns": 5},
                }
                self.memory = Memory()
                self.saved = 0

            def _save_config(self):
                self.saved += 1

            @staticmethod
            def _refresh_conversation_key():
                return ConversationKey(
                    "agent",
                    "agent_link",
                    "session-existing",
                )

            @staticmethod
            def _maybe_show_first_run_hint():
                return None

        host = Host()
        with mock.patch("meapet.desktop.app.QTimer.singleShot"):
            MeaPet._init_chat(host)

        device_id = host.config["llm"]["agent"]["device_id"]
        self.assertTrue(device_id.startswith("meapet-device-"))
        self.assertEqual(host.saved, 1)
