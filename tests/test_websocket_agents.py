"""Agent 模式只使用上游原生 WebSocket 协议。"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meapet.agent.base import (
    AgentTurnRequest,
    ImageAttachment,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)


def _reply(text: str = "你好，主人") -> str:
    return (
        f"<MEAPET_SEGMENT><DISPLAY>{text}</DISPLAY>"
        f'<META>{{"voice_text":"{text}","voice_language":"zh-CN",'
        '"mood":"happy","tts_style":""}</META>'
        "</MEAPET_SEGMENT><MEAPET_DONE />"
    )


class _ScriptedSocket:
    def __init__(self, initial=(), handler=None):
        self.incoming: asyncio.Queue[object] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.handler = handler
        for frame in initial:
            self.push(frame)

    def push(self, frame: dict) -> None:
        self.incoming.put_nowait(json.dumps(frame, ensure_ascii=False))

    def push_error(self, error: BaseException) -> None:
        self.incoming.put_nowait(error)

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
    def __init__(self, socket: _ScriptedSocket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, _exc_type, _exc, _tb):
        await self.socket.close()


class _Connector:
    def __init__(self, *sockets: _ScriptedSocket):
        self.sockets = list(sockets)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return _Connection(self.sockets.pop(0))


def _openclaw_hello(*, max_payload: int = 25 * 1024 * 1024):
    return {
        "type": "hello-ok",
        "protocol": 4,
        "server": {"version": "test"},
        "features": {
            "methods": [
                "chat.send",
                "chat.abort",
                "chat.history",
            ],
            "events": ["chat", "session.operation", "session.tool"],
        },
        "snapshot": {},
        "auth": {
            "role": "operator",
            "scopes": ["operator.read", "operator.write"],
        },
        "policy": {
            "maxPayload": max_payload,
            "maxBufferedBytes": 50 * 1024 * 1024,
            "tickIntervalMs": 15_000,
        },
    }


class TestAgentFactory(unittest.TestCase):
    def test_factory_selects_native_websocket_adapter_and_resolves_secrets(self):
        from meapet.agent.factory import create_agent_adapter_from_config
        from meapet.agent.hermes import HermesAdapter
        from meapet.agent.openclaw import OpenClawAdapter

        with mock.patch.dict(
            os.environ,
            {
                "HERMES_DASHBOARD_SESSION_TOKEN": "hermes-secret",
                "MEAPET_AGENT_TOKEN": "openclaw-secret",
            },
            clear=False,
        ):
            hermes = create_agent_adapter_from_config(
                {
                    "llm": {
                        "mode": "agent",
                        "agent": {
                            "kind": "hermes",
                            "base_url": "ws://127.0.0.1:9119/api/ws",
                            "auth_token": "$HERMES_DASHBOARD_SESSION_TOKEN",
                        },
                    }
                }
            )
            openclaw = create_agent_adapter_from_config(
                {
                    "llm": {
                        "mode": "agent",
                        "agent": {
                            "kind": "openclaw",
                            "base_url": "ws://127.0.0.1:18789",
                            "auth_token": "$MEAPET_AGENT_TOKEN",
                            "session_key": "agent:main:meapet:test",
                        },
                    }
                }
            )

        self.assertIsInstance(hermes, HermesAdapter)
        self.assertEqual(hermes.config.auth_token, "hermes-secret")
        self.assertIsInstance(openclaw, OpenClawAdapter)
        self.assertEqual(openclaw.config.auth_token, "openclaw-secret")

    def test_factory_rejects_http_agent_endpoint(self):
        from meapet.agent.factory import create_agent_adapter_from_config

        with self.assertRaisesRegex(ValueError, "WebSocket|ws"):
            create_agent_adapter_from_config(
                {
                    "llm": {
                        "mode": "agent",
                        "agent": {
                            "kind": "hermes",
                            "base_url": "http://127.0.0.1:8642",
                            "auth_token": "secret",
                        },
                    }
                }
            )


class TestOpenClawIdentity(unittest.TestCase):
    def test_v3_signature_payload_matches_gateway_byte_contract(self):
        from meapet.agent.openclaw_identity import build_device_auth_payload_v3

        payload = build_device_auth_payload_v3(
            device_id="dev-1",
            client_id="openclaw-macos",
            client_mode="ui",
            role="operator",
            scopes=("operator.admin", "operator.read"),
            signed_at_ms=1700000000000,
            token="tok-123",
            nonce="nonce-abc",
            platform="iOS",
            device_family="iPhone",
        )

        self.assertEqual(
            payload,
            "v3|dev-1|openclaw-macos|ui|operator|"
            "operator.admin,operator.read|1700000000000|"
            "tok-123|nonce-abc|ios|iphone",
        )

    def test_device_identity_is_created_once_and_reloaded(self):
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "identity.json"
            created = OpenClawDeviceIdentity.load_or_create(path)
            loaded = OpenClawDeviceIdentity.load_or_create(path)

            self.assertEqual(loaded.device_id, created.device_id)
            self.assertEqual(
                loaded.private_key_bytes,
                created.private_key_bytes,
            )
            self.assertEqual(len(created.public_key_bytes), 32)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o077, 0)


class TestOpenClawWebSocketAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_gateway_connection_and_streams_two_turns(self):
        from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            if frame.get("method") == "connect":
                socket.push(
                    {
                        "type": "res",
                        "id": "connect",
                        "ok": True,
                        "payload": _openclaw_hello(),
                    }
                )
            elif frame.get("method") == "chat.send":
                request_id = frame["id"]
                run_id = f"run-{request_id}"
                session_key = frame["params"]["sessionKey"]
                socket.push(
                    {
                        "type": "res",
                        "id": request_id,
                        "ok": True,
                        "payload": {
                            "runId": run_id,
                            "status": "accepted",
                        },
                    }
                )
                socket.push(
                    {
                        "type": "event",
                        "event": "chat",
                        "payload": {
                            "runId": run_id,
                            "sessionKey": session_key,
                            "seq": 1,
                            "state": "delta",
                            "deltaText": _reply(),
                        },
                    }
                )
                socket.push(
                    {
                        "type": "event",
                        "event": "chat",
                        "payload": {
                            "runId": run_id,
                            "sessionKey": session_key,
                            "seq": 2,
                            "state": "final",
                        },
                    }
                )
                socket.push(
                    {
                        "type": "res",
                        "id": request_id,
                        "ok": True,
                        "payload": {"runId": run_id, "status": "ok"},
                    }
                )

        socket = _ScriptedSocket(
            initial=(
                {
                    "type": "event",
                    "event": "connect.challenge",
                    "payload": {"nonce": "nonce"},
                },
            ),
            handler=handler,
        )
        connector = _Connector(socket)
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = OpenClawAdapter(
                OpenClawConfig(
                    base_url="ws://127.0.0.1:18789",
                    auth_token="secret",
                    session_key="agent:main:meapet:test",
                    identity_path=str(Path(temp_dir) / "identity.json"),
                    timeout_seconds=2,
                ),
                connector=connector,
                identity=OpenClawDeviceIdentity.from_private_bytes(bytes(range(32))),
            )
            results = []
            for turn_id in ("turn-1", "turn-2"):
                events = [
                    event
                    async for event in adapter.stream_turn(
                        AgentTurnRequest(turn_id=turn_id, user_text="你好")
                    )
                ]
                results.extend(
                    event for event in events if isinstance(event, TurnCompleted)
                )
            await adapter.close()

        self.assertEqual(len(connector.calls), 1)
        self.assertEqual(len(results), 2)
        sends = [
            frame for frame in socket.sent if frame.get("method") == "chat.send"
        ]
        self.assertEqual(
            [frame["params"]["idempotencyKey"] for frame in sends],
            ["turn-1", "turn-2"],
        )
        self.assertEqual(results[0].result.segments[0].display_text, "你好，主人")

    async def test_disconnect_resends_same_idempotency_key_on_new_connection(self):
        from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        attempts = 0

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            nonlocal attempts
            if frame.get("method") == "connect":
                socket.push(
                    {
                        "type": "res",
                        "id": "connect",
                        "ok": True,
                        "payload": _openclaw_hello(),
                    }
                )
                return
            if frame.get("method") != "chat.send":
                return
            attempts += 1
            run_id = "run-stable"
            socket.push(
                {
                    "type": "res",
                    "id": frame["id"],
                    "ok": True,
                    "payload": {"runId": run_id, "status": "accepted"},
                }
            )
            if attempts == 1:
                socket.push_error(ConnectionError("link lost"))
                return
            socket.push(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "runId": run_id,
                        "sessionKey": frame["params"]["sessionKey"],
                        "seq": 1,
                        "state": "delta",
                        "deltaText": _reply("恢复成功"),
                    },
                }
            )
            socket.push(
                {
                    "type": "event",
                    "event": "chat",
                    "payload": {
                        "runId": run_id,
                        "sessionKey": frame["params"]["sessionKey"],
                        "seq": 2,
                        "state": "final",
                    },
                }
            )

        sockets = [
            _ScriptedSocket(
                (
                    {
                        "type": "event",
                        "event": "connect.challenge",
                        "payload": {"nonce": f"nonce-{index}"},
                    },
                ),
                handler=handler,
            )
            for index in range(2)
        ]
        connector = _Connector(*sockets)
        adapter = OpenClawAdapter(
            OpenClawConfig(
                auth_token="secret",
                session_key="agent:main:meapet:test",
                timeout_seconds=2,
            ),
            connector=connector,
            identity=OpenClawDeviceIdentity.from_private_bytes(bytes(range(32))),
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(turn_id="turn-reconnect", user_text="继续")
            )
        ]
        await adapter.close()

        self.assertEqual(len(connector.calls), 2)
        sends = [
            frame
            for socket in sockets
            for frame in socket.sent
            if frame.get("method") == "chat.send"
        ]
        self.assertEqual(
            [frame["params"]["idempotencyKey"] for frame in sends],
            ["turn-reconnect", "turn-reconnect"],
        )
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(
            completed[0].result.segments[0].display_text,
            "恢复成功",
        )

    async def test_reconnect_recovers_completed_reply_from_gateway_history(self):
        from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        attempts = 0

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            nonlocal attempts
            method = frame.get("method")
            if method == "connect":
                socket.push(
                    {
                        "type": "res",
                        "id": "connect",
                        "ok": True,
                        "payload": _openclaw_hello(),
                    }
                )
                return
            if method == "chat.send":
                attempts += 1
                socket.push(
                    {
                        "type": "res",
                        "id": frame["id"],
                        "ok": True,
                        "payload": {
                            "runId": "run-stable",
                            "status": "started" if attempts == 1 else "ok",
                        },
                    }
                )
                if attempts == 1:
                    socket.push_error(ConnectionError("final event lost"))
                return
            if method == "chat.history":
                socket.push(
                    {
                        "type": "res",
                        "id": frame["id"],
                        "ok": True,
                        "payload": {
                            "messages": [
                                {"role": "user", "content": "继续"},
                                {
                                    "role": "assistant",
                                    "content": _reply("从历史恢复"),
                                },
                            ]
                        },
                    }
                )

        sockets = [
            _ScriptedSocket(
                (
                    {
                        "type": "event",
                        "event": "connect.challenge",
                        "payload": {"nonce": f"nonce-{index}"},
                    },
                ),
                handler=handler,
            )
            for index in range(2)
        ]
        connector = _Connector(*sockets)
        adapter = OpenClawAdapter(
            OpenClawConfig(
                auth_token="secret",
                session_key="agent:main:meapet:test",
                timeout_seconds=2,
            ),
            connector=connector,
            identity=OpenClawDeviceIdentity.from_private_bytes(bytes(range(32))),
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(
                    turn_id="turn-history",
                    user_text="继续",
                    history=(
                        {"role": "user", "content": "上一问"},
                        {"role": "assistant", "content": "上一答"},
                    ),
                )
            )
        ]
        await adapter.close()

        sends = [
            frame
            for socket in sockets
            for frame in socket.sent
            if frame.get("method") == "chat.send"
        ]
        self.assertEqual(
            [frame["params"]["idempotencyKey"] for frame in sends],
            ["turn-history", "turn-history"],
        )
        history_calls = [
            frame
            for frame in sockets[1].sent
            if frame.get("method") == "chat.history"
        ]
        self.assertEqual(len(history_calls), 1)
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(
            completed[0].result.segments[0].display_text,
            "从历史恢复",
        )

    async def test_cancel_sends_chat_abort_over_the_active_connection(self):
        from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        chat_started = asyncio.Event()

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "connect":
                socket.push(
                    {
                        "type": "res",
                        "id": "connect",
                        "ok": True,
                        "payload": _openclaw_hello(),
                    }
                )
            elif method == "chat.send":
                socket.push(
                    {
                        "type": "res",
                        "id": frame["id"],
                        "ok": True,
                        "payload": {
                            "runId": "run-cancel",
                            "status": "started",
                        },
                    }
                )
                chat_started.set()
            elif method == "chat.abort":
                socket.push(
                    {
                        "type": "res",
                        "id": frame["id"],
                        "ok": True,
                        "payload": {"status": "ok"},
                    }
                )

        socket = _ScriptedSocket(
            (
                {
                    "type": "event",
                    "event": "connect.challenge",
                    "payload": {"nonce": "nonce"},
                },
            ),
            handler=handler,
        )
        adapter = OpenClawAdapter(
            OpenClawConfig(
                auth_token="secret",
                session_key="agent:main:meapet:test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
            identity=OpenClawDeviceIdentity.from_private_bytes(bytes(range(32))),
        )

        async def collect():
            return [
                event
                async for event in adapter.stream_turn(
                    AgentTurnRequest(turn_id="turn-cancel", user_text="停止")
                )
            ]

        task = asyncio.create_task(collect())
        await asyncio.wait_for(chat_started.wait(), timeout=1)
        for _ in range(20):
            state = adapter._active.get("turn-cancel")
            if state is not None and state.run_id:
                break
            await asyncio.sleep(0)
        await adapter.cancel_turn("turn-cancel")
        events = await asyncio.wait_for(task, timeout=1)
        await adapter.close()

        aborts = [
            frame for frame in socket.sent if frame.get("method") == "chat.abort"
        ]
        self.assertEqual(len(aborts), 1)
        self.assertEqual(aborts[0]["params"]["runId"], "run-cancel")
        self.assertTrue(
            any(isinstance(event, TurnCancelled) for event in events)
        )

    async def test_rejects_turn_larger_than_gateway_advertised_payload(self):
        from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            if frame.get("method") == "connect":
                socket.push(
                    {
                        "type": "res",
                        "id": "connect",
                        "ok": True,
                        "payload": _openclaw_hello(max_payload=512),
                    }
                )

        socket = _ScriptedSocket(
            (
                {
                    "type": "event",
                    "event": "connect.challenge",
                    "payload": {"nonce": "nonce"},
                },
            ),
            handler=handler,
        )
        adapter = OpenClawAdapter(
            OpenClawConfig(
                auth_token="secret",
                session_key="agent:main:meapet:test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
            identity=OpenClawDeviceIdentity.from_private_bytes(bytes(range(32))),
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(
                    turn_id="turn-oversized",
                    user_text="x" * 2_000,
                )
            )
        ]
        await adapter.close()

        failures = [event for event in events if isinstance(event, TurnFailed)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, "payload_too_large")
        self.assertFalse(
            any(frame.get("method") == "chat.send" for frame in socket.sent)
        )

    async def test_malformed_gateway_run_id_fails_without_killing_dispatch(self):
        from meapet.agent.openclaw import OpenClawAdapter, OpenClawConfig
        from meapet.agent.openclaw_identity import OpenClawDeviceIdentity

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "connect":
                socket.push(
                    {
                        "type": "res",
                        "id": "connect",
                        "ok": True,
                        "payload": _openclaw_hello(),
                    }
                )
            elif method == "chat.send":
                socket.push(
                    {
                        "type": "res",
                        "id": frame["id"],
                        "ok": True,
                        "payload": {
                            "runId": "invalid\nrun",
                            "status": "started",
                        },
                    }
                )

        socket = _ScriptedSocket(
            (
                {
                    "type": "event",
                    "event": "connect.challenge",
                    "payload": {"nonce": "nonce"},
                },
            ),
            handler=handler,
        )
        adapter = OpenClawAdapter(
            OpenClawConfig(
                auth_token="secret",
                session_key="agent:main:meapet:test",
                timeout_seconds=2,
            ),
            connector=_Connector(socket),
            identity=OpenClawDeviceIdentity.from_private_bytes(bytes(range(32))),
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(
                    turn_id="turn-invalid-run",
                    user_text="你好",
                )
            )
        ]
        await adapter.close()

        failures = [event for event in events if isinstance(event, TurnFailed)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, "protocol")


class TestHermesWebSocketAdapter(unittest.IsolatedAsyncioTestCase):
    async def test_close_4401_before_ready_is_reported_as_authentication(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        class AuthenticationClose(ConnectionError):
            code = 4401

        socket = _ScriptedSocket()
        socket.push_error(AuthenticationClose("unauthorized"))
        adapter = HermesAdapter(
            HermesConfig(auth_token="wrong", timeout_seconds=2),
            connector=_Connector(socket),
            config_sink={},
        )

        async def collect():
            return [
                event
                async for event in adapter.stream_turn(
                    AgentTurnRequest(
                        turn_id="turn-auth-failure",
                        user_text="你好",
                    )
                )
            ]

        events = await asyncio.wait_for(collect(), timeout=1)
        await adapter.close()

        failures = [event for event in events if isinstance(event, TurnFailed)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, "authentication")

    async def test_uses_json_rpc_session_and_native_image_upload(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def respond(socket: _ScriptedSocket, request_id: str, result: dict) -> None:
            socket.push({"jsonrpc": "2.0", "id": request_id, "result": result})

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            request_id = frame.get("id")
            if method == "session.create":
                respond(
                    socket,
                    request_id,
                    {
                        "session_id": "runtime-1",
                        "stored_session_id": "stored-1",
                        "messages": [],
                    },
                )
            elif method == "session.history":
                respond(socket, request_id, {"messages": []})
            elif method == "image.attach_bytes":
                respond(socket, request_id, {"attached": True})
            elif method == "prompt.submit":
                respond(socket, request_id, {"status": "streaming"})
                socket.push(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "message.delta",
                            "session_id": "runtime-1",
                            "payload": {"text": _reply()},
                        },
                    }
                )
                socket.push(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "message.complete",
                            "session_id": "runtime-1",
                            "payload": {
                                "text": _reply(),
                                "status": "complete",
                            },
                        },
                    }
                )

        socket = _ScriptedSocket(
            initial=(
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {
                        "type": "gateway.ready",
                        "payload": {"skin": "test"},
                    },
                },
            ),
            handler=handler,
        )
        sink: dict = {}
        connector = _Connector(socket)
        adapter = HermesAdapter(
            HermesConfig(
                base_url="ws://127.0.0.1:9119/api/ws",
                auth_token="a/b c",
                timeout_seconds=2,
            ),
            connector=connector,
            config_sink=sink,
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(
                    turn_id="turn-1",
                    user_text="看图",
                    attachments=(
                        ImageAttachment(
                            media_type="image/png",
                            data="YWJj",
                            file_name="capture.png",
                        ),
                    ),
                )
            )
        ]
        await adapter.close()

        self.assertEqual(
            connector.calls[0][0],
            "ws://127.0.0.1:9119/api/ws?token=a%2Fb+c",
        )
        self.assertEqual(sink["remote_session_id"], "stored-1")
        methods = [frame.get("method") for frame in socket.sent]
        self.assertEqual(
            methods,
            [
                "session.create",
                "session.history",
                "image.attach_bytes",
                "prompt.submit",
            ],
        )
        attach = socket.sent[2]["params"]
        self.assertEqual(attach["session_id"], "runtime-1")
        self.assertEqual(attach["content_base64"], "YWJj")
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].result.segments[0].display_text, "你好，主人")

    async def test_disconnect_recovers_from_session_history_without_resubmit(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def respond(socket: _ScriptedSocket, request_id: str, result: dict) -> None:
            socket.push({"jsonrpc": "2.0", "id": request_id, "result": result})

        def first_handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "session.create":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-1",
                        "stored_session_id": "stored-1",
                    },
                )
            elif method == "session.history":
                respond(socket, frame["id"], {"messages": []})
            elif method == "prompt.submit":
                respond(socket, frame["id"], {"status": "streaming"})
                socket.push_error(ConnectionError("link lost"))

        def second_handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "session.resume":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-2",
                        "resumed": "stored-1",
                        "status": "idle",
                        "running": False,
                    },
                )
            elif method == "session.history":
                respond(
                    socket,
                    frame["id"],
                    {
                        "messages": [
                            {"role": "user", "content": "继续"},
                            {"role": "assistant", "content": _reply("已恢复")},
                        ]
                    },
                )

        first = _ScriptedSocket(
            (
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"type": "gateway.ready", "payload": {}},
                },
            ),
            handler=first_handler,
        )
        second = _ScriptedSocket(
            (
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"type": "gateway.ready", "payload": {}},
                },
            ),
            handler=second_handler,
        )
        connector = _Connector(first, second)
        adapter = HermesAdapter(
            HermesConfig(auth_token="secret", timeout_seconds=2),
            connector=connector,
            config_sink={},
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(turn_id="turn-recover", user_text="继续")
            )
        ]
        await adapter.close()

        self.assertEqual(len(connector.calls), 2)
        prompts = [
            frame
            for socket in (first, second)
            for frame in socket.sent
            if frame.get("method") == "prompt.submit"
        ]
        self.assertEqual(len(prompts), 1)
        completed = [event for event in events if isinstance(event, TurnCompleted)]
        self.assertEqual(
            completed[0].result.segments[0].display_text,
            "已恢复",
        )

    async def test_disconnect_does_not_mistake_old_protocol_reply_for_new_turn(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def respond(socket: _ScriptedSocket, request_id: str, result: dict) -> None:
            socket.push({"jsonrpc": "2.0", "id": request_id, "result": result})

        def first_handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "session.create":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-1",
                        "stored_session_id": "stored-1",
                    },
                )
            elif method == "session.history":
                respond(
                    socket,
                    frame["id"],
                    {
                        "messages": [
                            {"role": "user", "content": "上一问"},
                            {
                                "role": "assistant",
                                "content": _reply("上一答"),
                            },
                        ]
                    },
                )
            elif method == "prompt.submit":
                respond(socket, frame["id"], {"status": "streaming"})
                socket.push_error(ConnectionError("link lost before execution"))

        def second_handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "session.resume":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-2",
                        "resumed": "stored-1",
                        "status": "idle",
                        "running": False,
                    },
                )
            elif method == "session.history":
                respond(
                    socket,
                    frame["id"],
                    {
                        "messages": [
                            {"role": "user", "content": "上一问"},
                            {
                                "role": "assistant",
                                "content": _reply("上一答"),
                            },
                        ]
                    },
                )

        sockets = [
            _ScriptedSocket(
                (
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {"type": "gateway.ready", "payload": {}},
                    },
                ),
                handler=handler,
            )
            for handler in (first_handler, second_handler)
        ]
        adapter = HermesAdapter(
            HermesConfig(auth_token="secret", timeout_seconds=2),
            connector=_Connector(*sockets),
            config_sink={},
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(
                    turn_id="turn-not-executed",
                    user_text="新问题",
                    history=(
                        {"role": "user", "content": "上一问"},
                        {"role": "assistant", "content": "上一答"},
                    ),
                )
            )
        ]
        await adapter.close()

        self.assertFalse(
            any(isinstance(event, TurnCompleted) for event in events)
        )
        failures = [event for event in events if isinstance(event, TurnFailed)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, "connection")

    async def test_disconnect_surfaces_retained_remote_error_without_hanging(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def respond(socket: _ScriptedSocket, request_id: str, result: dict) -> None:
            socket.push({"jsonrpc": "2.0", "id": request_id, "result": result})

        def first_handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "session.create":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-1",
                        "stored_session_id": "stored-1",
                    },
                )
            elif method == "session.history":
                respond(socket, frame["id"], {"messages": []})
            elif method == "prompt.submit":
                respond(socket, frame["id"], {"status": "streaming"})
                socket.push_error(ConnectionError("terminal frame lost"))

        def second_handler(socket: _ScriptedSocket, frame: dict) -> None:
            if frame.get("method") == "session.resume":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-2",
                        "resumed": "stored-1",
                        "status": "idle",
                        "running": False,
                        "inflight": {
                            "user": "新问题",
                            "assistant": "",
                            "streaming": False,
                            "status": "error",
                            "error": "private upstream detail",
                            "recoverable": True,
                        },
                    },
                )

        sockets = [
            _ScriptedSocket(
                (
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {"type": "gateway.ready", "payload": {}},
                    },
                ),
                handler=handler,
            )
            for handler in (first_handler, second_handler)
        ]
        adapter = HermesAdapter(
            HermesConfig(auth_token="secret", timeout_seconds=2),
            connector=_Connector(*sockets),
            config_sink={},
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(
                    turn_id="turn-retained-error",
                    user_text="新问题",
                )
            )
        ]
        await adapter.close()

        failures = [event for event in events if isinstance(event, TurnFailed)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, "backend")
        self.assertNotIn("private upstream detail", failures[0].safe_message)

    async def test_cancel_sends_session_interrupt_over_the_active_connection(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        prompt_started = asyncio.Event()

        def respond(socket: _ScriptedSocket, request_id: str, result: dict) -> None:
            socket.push({"jsonrpc": "2.0", "id": request_id, "result": result})

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "session.create":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-cancel",
                        "stored_session_id": "stored-cancel",
                    },
                )
            elif method == "session.history":
                respond(socket, frame["id"], {"messages": []})
            elif method == "prompt.submit":
                respond(socket, frame["id"], {"status": "streaming"})
                prompt_started.set()
            elif method == "session.interrupt":
                respond(socket, frame["id"], {"interrupted": True})

        socket = _ScriptedSocket(
            (
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"type": "gateway.ready", "payload": {}},
                },
            ),
            handler=handler,
        )
        adapter = HermesAdapter(
            HermesConfig(auth_token="secret", timeout_seconds=2),
            connector=_Connector(socket),
            config_sink={},
        )

        async def collect():
            return [
                event
                async for event in adapter.stream_turn(
                    AgentTurnRequest(
                        turn_id="hermes-cancel",
                        user_text="停止",
                    )
                )
            ]

        task = asyncio.create_task(collect())
        await asyncio.wait_for(prompt_started.wait(), timeout=1)
        await adapter.cancel_turn("hermes-cancel")
        events = await asyncio.wait_for(task, timeout=1)
        await adapter.close()

        interrupts = [
            frame
            for frame in socket.sent
            if frame.get("method") == "session.interrupt"
        ]
        self.assertEqual(len(interrupts), 1)
        self.assertEqual(
            interrupts[0]["params"]["session_id"],
            "runtime-cancel",
        )
        self.assertTrue(
            any(isinstance(event, TurnCancelled) for event in events)
        )

    async def test_interactive_request_is_not_auto_approved_or_left_hanging(self):
        from meapet.agent.hermes import HermesAdapter, HermesConfig

        def respond(socket: _ScriptedSocket, request_id: str, result: dict) -> None:
            socket.push({"jsonrpc": "2.0", "id": request_id, "result": result})

        def handler(socket: _ScriptedSocket, frame: dict) -> None:
            method = frame.get("method")
            if method == "session.create":
                respond(
                    socket,
                    frame["id"],
                    {
                        "session_id": "runtime-approval",
                        "stored_session_id": "stored-approval",
                    },
                )
            elif method == "session.history":
                respond(socket, frame["id"], {"messages": []})
            elif method == "prompt.submit":
                respond(socket, frame["id"], {"status": "streaming"})
                socket.push(
                    {
                        "jsonrpc": "2.0",
                        "method": "event",
                        "params": {
                            "type": "approval.request",
                            "session_id": "runtime-approval",
                            "payload": {
                                "request_id": "sensitive-request",
                                "details": "must not reach the UI",
                            },
                        },
                    }
                )
            elif method == "session.interrupt":
                respond(socket, frame["id"], {"interrupted": True})

        socket = _ScriptedSocket(
            (
                {
                    "jsonrpc": "2.0",
                    "method": "event",
                    "params": {"type": "gateway.ready", "payload": {}},
                },
            ),
            handler=handler,
        )
        adapter = HermesAdapter(
            HermesConfig(auth_token="secret", timeout_seconds=2),
            connector=_Connector(socket),
            config_sink={},
        )
        events = [
            event
            async for event in adapter.stream_turn(
                AgentTurnRequest(
                    turn_id="turn-approval",
                    user_text="执行敏感操作",
                )
            )
        ]
        await adapter.close()

        failures = [event for event in events if isinstance(event, TurnFailed)]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].category, "interaction_required")
        self.assertNotIn("details", failures[0].safe_message)
        methods = [frame.get("method") for frame in socket.sent]
        self.assertIn("session.interrupt", methods)
        self.assertNotIn("approval.respond", methods)


if __name__ == "__main__":
    unittest.main()
