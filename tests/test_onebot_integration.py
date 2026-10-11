"""OneBot 适配器的端到端集成测试（真实 socket，不需要 NapCat / QQ）。

与 :mod:`tests.test_onebot_protocol` 的区别：这里真的开 socket、真的跑 uvicorn，
覆盖四种通信方式的完整链路：

- 反向 WS：测试端扮演 NapCat 连进来 → 事件上行 → 回复以 API 调用下行。
- 正向 WS：测试端扮演 NapCat 的 WS 服务端 → 我们主动连出去 → 同样双向。
- hub：同一进程里既有上游（假 NapCat）又有下游消费者，验证
  「上游事件转发给下游」+「下游 send_* 转发到上游」的全双工链路。
- 鉴权：token 不匹配时连接被拒绝。

**为什么不用 ``IsolatedAsyncioTestCase``**：它会为每个用例新建并销毁一个事件循环，
而 uvicorn / websockets 的后台任务挂在服务自己的 long-lived loop 上，销毁测试循环时
会卡在 ``_cancel_all_tasks``。因此这里统一用 ``meapet.async_runtime.run()`` 把用例体
跑在**与生产同一个** asyncio 守护循环上，生命周期干净且不需要拆循环。
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from websockets.asyncio.client import connect as ws_connect  # noqa: E402
from websockets.asyncio.server import serve as ws_serve  # noqa: E402

from meapet.async_runtime import run  # noqa: E402
from meapet.onebot.service import OneBotService  # noqa: E402
from meapet.onebot.types import ApiResponse  # noqa: E402

TOKEN = "test-token-0123456789"
CASE_TIMEOUT = 60.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def ok_response(request: dict) -> dict:
    return {
        "status": "ok",
        "retcode": 0,
        "data": {"message_id": 1},
        "echo": request.get("echo"),
    }


def private_event(text: str, message_id: int = 1, self_id: int = 10000) -> dict:
    return {
        "time": 1,
        "self_id": self_id,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": 42,
        "message": text,
        "raw_message": text,
        "sender": {"user_id": 42, "nickname": "测试", "card": "", "role": ""},
    }


def group_event(text: str, *, at: bool, group_id: int = 888) -> dict:
    prefix = [{"type": "at", "data": {"qq": "10000"}}] if at else []
    return {
        "time": 1,
        "self_id": 10000,
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 2,
        "group_id": group_id,
        "user_id": 43,
        "message": prefix + [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "sender": {"user_id": 43, "nickname": "n", "card": "card", "role": "member"},
    }


async def wait_until(predicate, timeout: float = 8.0, interval: float = 0.05) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return predicate()


async def connect_with_retry(url: str, headers=None, attempts: int = 60):
    """等 uvicorn 真正开始监听。"""
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return await ws_connect(url, additional_headers=headers or {}, max_size=None)
        except Exception as exc:
            last = exc
            await asyncio.sleep(0.1)
    raise AssertionError(f"无法连接 {url}: {type(last).__name__}: {last}")


async def recv_json(connection, timeout: float = 8.0) -> dict:
    return json.loads(await asyncio.wait_for(connection.recv(), timeout=timeout))


class FakeNapCat:
    """扮演 OneBot 实现端（正向 WS 服务端），与用例跑在同一条循环上。"""

    def __init__(self, port: int) -> None:
        self.port = port
        self.frames: list = []
        self.auth = ""
        self.path = ""
        self.connection = None
        self._manager = None

    async def __aenter__(self) -> "FakeNapCat":
        self._manager = ws_serve(self._handler, "127.0.0.1", self.port, max_size=None)
        await self._manager.__aenter__()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self._manager.__aexit__(*exc_info)

    async def _handler(self, websocket) -> None:
        request = getattr(websocket, "request", None)
        headers = getattr(request, "headers", None) or {}
        for key, value in headers.items():
            if str(key).lower() == "authorization":
                self.auth = str(value)
        self.path = str(getattr(request, "path", "") or "")
        self.connection = websocket
        try:
            async for raw in websocket:
                payload = json.loads(raw)
                self.frames.append(payload)
                if "action" in payload:
                    await websocket.send(json.dumps(ok_response(payload)))
        except Exception:
            pass
        finally:
            self.connection = None

    async def push(self, payload: dict) -> None:
        if self.connection is None:
            raise AssertionError("假 NapCat 还没有连接")
        await self.connection.send(json.dumps(payload))

    def actions(self, name: str) -> list:
        return [frame for frame in self.frames if frame.get("action") == name]


class IntegrationCase(unittest.TestCase):
    """把用例体丢到 async_runtime 的循环上跑。"""

    timeout = CASE_TIMEOUT

    def scenario(self, coro) -> None:
        run(coro, timeout=self.timeout)


# --------------------------------------------------------------------------- #
# 接收端 · 反向 WS
# --------------------------------------------------------------------------- #


class ReverseWsIntegrationTest(IntegrationCase):
    async def _scenario(self, body) -> None:
        port = free_port()
        service = OneBotService(
            {
                "enabled": True,
                "reply_mode": "pet",
                "reverse_ws": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": port,
                    "path": "/onebot/v11/ws",
                    "access_token": TOKEN,
                },
            }
        )
        service.start()
        connection = await connect_with_retry(
            f"ws://127.0.0.1:{port}/onebot/v11/ws",
            {"Authorization": f"Bearer {TOKEN}"},
        )
        try:
            await body(service, connection)
        finally:
            await connection.close()
            service.stop()
            await asyncio.sleep(0.3)

    def test_event_in_reply_out(self) -> None:
        async def body(service, connection) -> None:
            await connection.send(json.dumps(private_event("你好喵")))
            self.assertTrue(await wait_until(lambda: service.pending_count > 0))
            turn = service.take_ready_turn()
            self.assertIsNotNone(turn)
            self.assertEqual(turn.message.text, "你好喵")
            self.assertEqual(turn.message.user_id, 42)

            service.complete_turn(turn.turn_id, "你好呀")
            request = await recv_json(connection)
            self.assertEqual(request["action"], "send_private_msg")
            self.assertEqual(request["params"]["user_id"], 42)
            self.assertEqual(request["params"]["message"], "你好呀")
            self.assertTrue(request.get("echo"))

            await connection.send(json.dumps(ok_response(request)))
            self.assertTrue(await wait_until(lambda: service.stats["replies_sent"] == 1))
            self.assertEqual(service.stats["send_failures"], 0)

        self.scenario(self._scenario(body))

    def test_api_call_over_reverse_ws(self) -> None:
        """接收端连接同时也是发送端：服务能通过它主动调 API。"""

        async def body(service, connection) -> None:
            self.assertTrue(await wait_until(lambda: service.senders.pick() is not None))
            task = asyncio.ensure_future(service.send_text_to("private", 7, "主动发言"))
            request = await recv_json(connection)
            self.assertEqual(request["action"], "send_private_msg")
            self.assertEqual(request["params"]["user_id"], 7)
            await connection.send(json.dumps(ok_response(request)))
            self.assertTrue(await asyncio.wait_for(task, timeout=5))

        self.scenario(self._scenario(body))

    def test_token_is_required(self) -> None:
        async def body(service, _connection) -> None:
            rejected = False
            try:
                intruder = await ws_connect(
                    f"ws://127.0.0.1:{service.config['reverse_ws']['port']}/onebot/v11/ws",
                    open_timeout=3,
                    max_size=None,
                )
                try:
                    await asyncio.wait_for(intruder.recv(), timeout=2)
                except Exception:
                    rejected = True
                await intruder.close()
            except Exception:
                rejected = True
            self.assertTrue(rejected, "无 token 的连接必须被拒绝")
            self.assertEqual(service.stats["messages_queued"], 0)

        self.scenario(self._scenario(body))

    def test_group_message_needs_at(self) -> None:
        async def body(service, connection) -> None:
            await connection.send(json.dumps(group_event("hi", at=False)))
            await asyncio.sleep(0.3)
            self.assertEqual(service.pending_count, 0)
            self.assertEqual(service.stats["messages_denied"], 1)

            await connection.send(json.dumps(group_event("hi", at=True)))
            self.assertTrue(await wait_until(lambda: service.pending_count > 0))
            turn = service.take_ready_turn()
            self.assertEqual(turn.message.group_id, 888)
            self.assertEqual(turn.message.conversation_key, "group:888")
            self.assertIn("card", turn.prompt)

        self.scenario(self._scenario(body))


# --------------------------------------------------------------------------- #
# 发起端 · 正向 WS
# --------------------------------------------------------------------------- #


class ForwardWsIntegrationTest(IntegrationCase):
    async def _scenario(self, body) -> None:
        port = free_port()
        async with FakeNapCat(port) as peer:
            service = OneBotService(
                {
                    "enabled": True,
                    "reply_mode": "pet",
                    "forward_ws": {
                        "enabled": True,
                        "url": f"ws://127.0.0.1:{port}/",
                        "access_token": TOKEN,
                        "reconnect": True,
                        "reconnect_initial_ms": 100,
                    },
                }
            )
            service.start()
            try:
                self.assertTrue(
                    await wait_until(
                        lambda: bool(service.forward_clients)
                        and service.forward_clients[0].connected,
                        timeout=10,
                    ),
                    "正向 WS 未能连上",
                )
                await body(service, peer)
            finally:
                service.stop()
                await asyncio.sleep(0.3)

    def test_connect_auth_and_bidirectional(self) -> None:
        async def body(service, peer) -> None:
            # 鉴权：header + query 两种形式都会带上
            self.assertEqual(peer.auth, f"Bearer {TOKEN}")
            self.assertIn(f"access_token={TOKEN}", peer.path)

            # 上行：假 NapCat 推事件给我们
            await peer.push(private_event("正向来的消息"))
            self.assertTrue(await wait_until(lambda: service.pending_count > 0))
            turn = service.take_ready_turn()
            self.assertEqual(turn.message.text, "正向来的消息")

            # 下行：回复通过同一条正向连接发回
            service.complete_turn(turn.turn_id, "收到喵")
            self.assertTrue(await wait_until(lambda: peer.actions("send_private_msg")))
            sent = peer.actions("send_private_msg")
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["params"]["message"], "收到喵")
            self.assertEqual(sent[0]["params"]["user_id"], 42)
            self.assertTrue(await wait_until(lambda: service.stats["replies_sent"] == 1))

        self.scenario(self._scenario(body))

    def test_reconnects_after_peer_drops(self) -> None:
        """对端断开后应自动重连（重连间隔 100ms）。"""

        async def body(service, peer) -> None:
            first = service.forward_clients[0]
            self.assertTrue(peer.connection is not None)
            await peer.connection.close()
            self.assertTrue(await wait_until(lambda: not first.connected, timeout=5))
            self.assertTrue(
                await wait_until(lambda: first.connected, timeout=10),
                "对端重连后应恢复连接",
            )

        self.scenario(self._scenario(body))


# --------------------------------------------------------------------------- #
# hub（下游消费者）
# --------------------------------------------------------------------------- #


class HubIntegrationTest(IntegrationCase):
    def test_upstream_event_reaches_hub_and_hub_send_reaches_upstream(self) -> None:
        async def scenario() -> None:
            upstream_port = free_port()
            hub_port = free_port()
            async with FakeNapCat(upstream_port) as peer:
                service = OneBotService(
                    {
                        "enabled": True,
                        "reply_mode": "both",
                        "forward_ws": {
                            "enabled": True,
                            "url": f"ws://127.0.0.1:{upstream_port}/",
                            "reconnect": True,
                            "reconnect_initial_ms": 100,
                        },
                        "hub": {
                            "enabled": True,
                            "host": "127.0.0.1",
                            "port": hub_port,
                            "ws_path": "/onebot/v11/hub",
                            "http_path": "/onebot/v11/hub/http",
                        },
                    }
                )
                service.start()
                consumer = await connect_with_retry(
                    f"ws://127.0.0.1:{hub_port}/onebot/v11/hub"
                )
                try:
                    self.assertTrue(
                        await wait_until(
                            lambda: bool(service.forward_clients)
                            and service.forward_clients[0].connected,
                            timeout=10,
                        )
                    )

                    # 1) 上游事件应转发给 hub 消费者
                    await peer.push(private_event("群里的消息", message_id=77))
                    forwarded = await recv_json(consumer)
                    self.assertEqual(forwarded["post_type"], "message")
                    self.assertEqual(forwarded["message_id"], 77)

                    # 2) hub 消费者发 send_private_msg，应被转发到上游
                    await consumer.send(
                        json.dumps(
                            {
                                "action": "send_private_msg",
                                "params": {"user_id": 42, "message": "来自 dsh 的回复"},
                                "echo": "hub-1",
                            }
                        )
                    )
                    response = await recv_json(consumer)
                    self.assertEqual(response["echo"], "hub-1")
                    self.assertEqual(response["status"], "ok")

                    self.assertTrue(
                        await wait_until(lambda: peer.actions("send_private_msg"))
                    )
                    upstream = peer.actions("send_private_msg")
                    self.assertEqual(len(upstream), 1)
                    self.assertEqual(upstream[0]["params"]["message"], "来自 dsh 的回复")
                finally:
                    await consumer.close()
                    service.stop()
                    await asyncio.sleep(0.3)

        self.scenario(scenario())

    def test_hub_rejects_management_actions(self) -> None:
        async def scenario() -> None:
            hub_port = free_port()
            service = OneBotService(
                {"enabled": True, "reply_mode": "hub", "hub": {"enabled": True,
                                                              "host": "127.0.0.1",
                                                              "port": hub_port}}
            )
            service.start()
            consumer = await connect_with_retry(
                f"ws://127.0.0.1:{hub_port}/onebot/v11/hub"
            )
            try:
                await consumer.send(
                    json.dumps(
                        {"action": "set_group_kick", "params": {"group_id": 1}, "echo": "e"}
                    )
                )
                response = await recv_json(consumer)
                self.assertEqual(response["retcode"], 103)
                self.assertEqual(response["status"], "failed")
            finally:
                await consumer.close()
                service.stop()
                await asyncio.sleep(0.3)

        self.scenario(scenario())


class RealHttpConsumer:
    """真实 HTTP 消费者：扮演 `httpClients` / SnowLuma 那种"只会 HTTP"的下游。

    跑在后台线程的 stdlib `http.server` 上——**不是** httpx 的 MockTransport，
    所以真的过一遍 TCP + HTTP 栈（那是单测覆盖不到的地方）。
    """

    def __init__(self, *, reply: str | None = None) -> None:
        self.requests: list = []
        self.reply = reply
        self._server = None
        self._thread = None

    def start(self) -> str:
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib 约定
                length = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = None
                outer.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("authorization"),
                        "content_type": self.headers.get("content-type"),
                        "body": body,
                    }
                )
                if outer.reply is None:
                    payload = {"status": "ok", "retcode": 0, "data": None}
                else:
                    # 规范 §7 的快速操作：响应体直接带 API 同形字段
                    payload = {"reply": outer.reply, "auto_escape": False}
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args) -> None:  # 静音
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_address[1]}/onebot/v11/http"

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


class _RecordingSender:
    """替身上游发送端：记录被要求发出去的消息。"""

    available = True

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.calls: list = []

    async def send(self, action, params):
        self.calls.append((action, dict(params)))
        return ApiResponse.from_payload({"status": "ok", "retcode": 0, "data": {"message_id": 1}})


class HttpPushIntegrationTest(IntegrationCase):
    """`hub.push_urls`（规范 §2.2 事件 HTTP POST 上报）——真实 socket 版。

    这个特性原先只有 `httpx.MockTransport` 单测：那条路径**不经过**真实 HTTP 栈，
    也证明不了"真的发出了一个能收的 POST"。这里补上。
    """

    def _service(self, url: str) -> OneBotService:
        return OneBotService(
            {
                "enabled": True,
                "reply_mode": "hub",
                "hub": {
                    "enabled": True,
                    "publish_events": True,
                    "push_urls": [url],
                    "push_token": "push-token-abc",
                    "push_timeout_s": 5.0,
                },
            }
        )

    def test_event_is_posted_over_real_http_and_quick_op_reply_is_sent(self) -> None:
        consumer = RealHttpConsumer(reply="收到喵")
        url = consumer.start()
        service = self._service(url)
        sender = _RecordingSender("fake")
        service.senders.register(sender, fallback=True)

        async def scenario() -> None:
            try:
                service._on_upstream_event(private_event("你好"), "fake")

                self.assertTrue(
                    await wait_until(lambda: len(consumer.requests) == 1),
                    "真实 HTTP 消费者应当收到一条上报",
                )
                got = consumer.requests[0]
                self.assertEqual(got["path"], "/onebot/v11/http")
                self.assertEqual(got["authorization"], "Bearer push-token-abc")
                self.assertIn("application/json", got["content_type"] or "")
                # 事件帧按原样上报（不改写字段）
                self.assertEqual(got["body"]["post_type"], "message")
                self.assertEqual(got["body"]["raw_message"], "你好")
                self.assertEqual(got["body"]["user_id"], 42)

                # 快速操作：响应体里的 reply 应当被真的发回会话
                self.assertTrue(
                    await wait_until(lambda: len(sender.calls) == 1),
                    "响应里的快速操作 reply 应当变成一次真实发送",
                )
                action, params = sender.calls[0]
                self.assertEqual(action, "send_private_msg")
                self.assertEqual(params["user_id"], 42)
                self.assertEqual(params["message"], "收到喵")
                self.assertEqual(service.stats["events_pushed"], 1)
                self.assertEqual(service.stats["quick_operations"], 1)
            finally:
                service.stop()
                consumer.stop()

        self.scenario(scenario())

    def test_push_to_a_dead_consumer_counts_a_failure_without_raising(self) -> None:
        """对端不在（端口没人监听）：记失败、不抛异常、不影响其它功能。"""
        dead_url = f"http://127.0.0.1:{free_port()}/onebot/v11/http"
        service = self._service(dead_url)

        async def scenario() -> None:
            try:
                service._on_upstream_event(private_event("你好"), "fake")
                self.assertTrue(
                    await wait_until(lambda: service.stats["push_failures"] == 1),
                    "连不上对端应当计入 push_failures",
                )
                self.assertEqual(service.stats["events_pushed"], 0)
            finally:
                service.stop()

        self.scenario(scenario())


if __name__ == "__main__":
    unittest.main()
