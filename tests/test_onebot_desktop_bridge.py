"""OneBot 桌面接线与 HTTP API 发送端的单元测试。

覆盖两处此前没有测试的地方：

1. ``HttpApiSender``（``http_api`` 通信方式）—— 走 httpx MockTransport，不碰真网络。
2. ``PetOneBotMixin`` —— 轮询取轮次、回复回收、超时释放、启停与重连。
   Qt 侧全部打桩（``QTimer`` 替身 + 假 ``OneBotService``），不启动真实服务。
"""

from __future__ import annotations

import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from meapet.onebot.api import HttpApiSender  # noqa: E402


# --------------------------------------------------------------------------- #
# 1) http_api 发送端
# --------------------------------------------------------------------------- #


class HttpApiSenderTests(unittest.IsolatedAsyncioTestCase):
    async def _call(self, handler, *, sender: HttpApiSender, action="send_private_msg",
                    params=None):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def fake_get_client():
            return client

        try:
            with mock.patch("meapet.http_async.get_client", fake_get_client):
                return await sender.send(action, params or {"user_id": 1, "message": "hi"})
        finally:
            await client.aclose()

    async def test_posts_action_path_with_bearer_and_json_body(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 7}})

        sender = HttpApiSender("http://peer:3000", access_token="tok")
        result = await self._call(handler, sender=sender)

        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"message_id": 7})
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/send_private_msg")
        self.assertEqual(seen["auth"], "Bearer tok")
        self.assertEqual(seen["body"]["action"], "send_private_msg")
        self.assertEqual(seen["body"]["params"]["user_id"], 1)
        self.assertTrue(seen["body"]["echo"])

    async def test_omits_authorization_when_token_empty(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": None})

        sender = HttpApiSender("http://peer:3000")
        self.assertTrue((await self._call(handler, sender=sender)).ok)
        self.assertIsNone(seen["auth"])

    async def test_falls_back_to_base_url_when_action_path_is_404(self) -> None:
        paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path == "/send_private_msg":
                return httpx.Response(404, text="not found")
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": 1}})

        sender = HttpApiSender("http://peer:3000")
        result = await self._call(handler, sender=sender)

        self.assertTrue(result.ok)
        self.assertEqual(paths, ["/send_private_msg", "/"])

    async def test_falls_back_then_fails_when_base_also_rejects(self) -> None:
        """动作路径与基址都返回 405：应尝试两次后失败，不无限重试。"""
        paths = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(405, text="nope")

        sender = HttpApiSender("http://peer:3000")
        result = await self._call(handler, sender=sender)
        self.assertFalse(result.ok)
        self.assertEqual(paths, ["/send_private_msg", "/"])
        self.assertIn("405", result.error)

    async def test_http_error_is_reported_as_failure(self) -> None:
        sender = HttpApiSender("http://peer:3000")
        result = await self._call(lambda request: httpx.Response(500, text="boom"), sender=sender)
        self.assertFalse(result.ok)
        self.assertIn("500", result.error)
        self.assertIn("500", sender.last_error)

    async def test_non_json_body_is_failure(self) -> None:
        sender = HttpApiSender("http://peer:3000")
        result = await self._call(
            lambda request: httpx.Response(200, text="<html>hi</html>"), sender=sender
        )
        self.assertFalse(result.ok)
        self.assertIn("JSON", result.error)

    async def test_transport_error_is_failure_not_exception(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        sender = HttpApiSender("http://peer:3000")
        result = await self._call(handler, sender=sender)
        self.assertFalse(result.ok)
        self.assertIn("ConnectError", result.error)
        self.assertEqual(sender.last_error, "ConnectError")

    async def test_missing_api_base_fails_without_network(self) -> None:
        sender = HttpApiSender("")
        self.assertFalse(sender.available)
        result = await sender.send("get_status", {})
        self.assertFalse(result.ok)
        self.assertIn("api_base", result.error)

    async def test_trailing_slash_on_base_is_normalised(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": None})

        sender = HttpApiSender("http://peer:3000/")
        await self._call(handler, sender=sender)
        self.assertEqual(seen["url"], "http://peer:3000/send_private_msg")


# --------------------------------------------------------------------------- #
# 2) 桌面接线
# --------------------------------------------------------------------------- #


class _Signal:
    def connect(self, callback):
        self.callback = callback


class _Timer:
    """QTimer 替身：记录 start/stop/deleteLater，不真的跑。"""

    def __init__(self, *_args):
        self.timeout = _Signal()
        self.interval = None
        self.started = False
        self.stopped = False
        self.deleted = False

    def setInterval(self, interval):
        self.interval = interval

    def start(self, interval=None):
        self.started = True
        if interval is not None:
            self.interval = interval

    def stop(self):
        self.stopped = True

    def deleteLater(self):
        self.deleted = True


class FakeService:
    """OneBotService 替身，记录所有交互。"""

    instances: list = []

    def __init__(self, config):
        self.config = config
        self.started = False
        self.stopped = False
        self.running = False
        self.turns: list = []
        self.completed: list = []
        self.failed: list = []
        self.take_calls = 0
        FakeService.instances.append(self)

    def validate(self):
        return []

    def start(self):
        self.started = True
        self.running = True

    def stop(self, *args, **kwargs):
        self.stopped = True
        self.running = False

    def summary(self):
        return "fake"

    def status_snapshot(self):
        return []

    def take_ready_turn(self):
        self.take_calls += 1
        return self.turns.pop(0) if self.turns else None

    def complete_turn(self, turn_id, text):
        self.completed.append((turn_id, text))

    def fail_turn(self, turn_id, reason=""):
        self.failed.append((turn_id, reason))


def make_turn(turn_id: str, prompt: str = "你好", deadline: float | None = None):
    """对齐真实 ``PendingTurn``：``prompt`` 是它自己的属性，``message`` 是原始消息。"""
    message = types.SimpleNamespace(conversation_key="private:42")
    return types.SimpleNamespace(
        turn_id=turn_id,
        message=message,
        prompt=prompt,
        deadline=deadline if deadline is not None else time.time() + 60,
    )


class Host:
    """最小宿主：只提供 mixin 需要的东西。"""

    def __init__(self, config):
        self.config = config
        self._awaiting_reply = False
        self.chatted: list = []

    def _do_chat(self, text):
        self.chatted.append(text)


from meapet.desktop.onebot_bridge import PetOneBotMixin  # noqa: E402


class MixinHost(PetOneBotMixin, Host):
    pass


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeService.instances = []
        self.bridge = sys.modules["meapet.desktop.onebot_bridge"]
        self.timer_patch = mock.patch.object(self.bridge, "QTimer", _Timer)
        self.service_patch = mock.patch(
            "meapet.onebot.service.OneBotService", FakeService
        )
        self.timer_patch.start()
        self.service_patch.start()
        self.addCleanup(self.timer_patch.stop)
        self.addCleanup(self.service_patch.stop)

    # -- 生命周期 ------------------------------------------------------- #

    def test_disabled_config_does_not_start_anything(self):
        host = MixinHost({"onebot": {"enabled": False}})
        host._init_onebot()
        self.assertIsNone(host._onebot_service)
        self.assertIsNone(host._onebot_poll_timer)
        self.assertEqual(FakeService.instances, [])

    def test_enabled_config_starts_service_and_poll_timer(self):
        host = MixinHost({"onebot": {"enabled": True}})
        host._init_onebot()

        service = host._onebot_service
        self.assertIsInstance(service, FakeService)
        self.assertTrue(service.started)
        self.assertIsNotNone(host._onebot_poll_timer)
        self.assertTrue(host._onebot_poll_timer.started)
        self.assertEqual(host._onebot_poll_timer.interval, self.bridge.POLL_INTERVAL_MS)
        self.assertEqual(host._onebot_turn_id, "")

    def test_missing_onebot_section_is_disabled(self):
        host = MixinHost({})
        host._init_onebot()
        self.assertIsNone(host._onebot_service)

    def test_stop_tears_down_timer_and_service(self):
        host = MixinHost({"onebot": {"enabled": True}})
        host._init_onebot()
        timer = host._onebot_poll_timer
        service = host._onebot_service

        host._stop_onebot()

        self.assertTrue(timer.stopped)
        self.assertTrue(timer.deleted)
        self.assertTrue(service.stopped)
        self.assertIsNone(host._onebot_service)
        self.assertIsNone(host._onebot_poll_timer)
        self.assertEqual(host._onebot_turn_id, "")

    def test_stop_is_safe_when_never_initialised(self):
        host = MixinHost({"onebot": {"enabled": False}})
        host._stop_onebot()  # 不应抛异常
        self.assertIsNone(host._onebot_service)

    def test_status_text_reports_disabled(self):
        host = MixinHost({"onebot": {"enabled": False}})
        host._init_onebot()
        self.assertEqual(host._onebot_status_text(), "未启用")

    # -- 轮询 ----------------------------------------------------------- #

    def _started(self) -> MixinHost:
        host = MixinHost({"onebot": {"enabled": True}})
        host._init_onebot()
        return host

    def test_poll_takes_turn_and_starts_chat(self):
        host = self._started()
        host._onebot_service.turns.append(make_turn("t1", "[群 8] 甲：在吗"))

        host._poll_onebot()

        self.assertEqual(host.chatted, ["[群 8] 甲：在吗"])
        self.assertEqual(host._onebot_turn_id, "t1")

    def test_poll_does_nothing_without_pending_turn(self):
        host = self._started()
        host._poll_onebot()
        self.assertEqual(host.chatted, [])
        self.assertEqual(host._onebot_turn_id, "")

    def test_poll_skips_while_awaiting_reply_without_taking_turn(self):
        host = self._started()
        host._onebot_service.turns.append(make_turn("t1"))
        host._awaiting_reply = True

        host._poll_onebot()

        self.assertEqual(host.chatted, [])
        self.assertEqual(host._onebot_service.take_calls, 0)
        self.assertEqual(host._onebot_turn_id, "")

    def test_poll_does_not_start_second_turn_while_one_runs(self):
        host = self._started()
        host._onebot_service.turns.append(make_turn("t1"))
        host._onebot_service.turns.append(make_turn("t2"))
        host._poll_onebot()

        host._poll_onebot()

        self.assertEqual(host.chatted, ["你好"])
        self.assertEqual(host._onebot_turn_id, "t1")
        self.assertEqual(host._onebot_service.take_calls, 1)

    def test_poll_releases_expired_turn_then_takes_next(self):
        host = self._started()
        host._onebot_turn_id = "stale"
        host._onebot_turn_deadline = time.time() - 1
        host._onebot_service.turns.append(make_turn("t2", "下一条"))

        host._poll_onebot()

        self.assertEqual(host._onebot_service.failed, [("stale", "timeout")])
        self.assertEqual(host.chatted, ["下一条"])
        self.assertEqual(host._onebot_turn_id, "t2")

    def test_poll_swallows_do_chat_exception_and_releases(self):
        host = self._started()
        host._onebot_service.turns.append(make_turn("t1"))

        def boom(_text):
            raise RuntimeError("kaboom")

        host._do_chat = boom
        host._poll_onebot()

        self.assertEqual(host._onebot_service.failed, [("t1", "RuntimeError")])
        self.assertEqual(host._onebot_turn_id, "")

    # -- 回复回收 ------------------------------------------------------- #

    def test_reply_is_handed_back_to_service(self):
        host = self._started()
        host._onebot_turn_id = "t1"
        host._onebot_turn_deadline = time.time() + 60

        host._notify_channel_turn_finished("回复喵")

        self.assertEqual(host._onebot_service.completed, [("t1", "回复喵")])
        self.assertEqual(host._onebot_service.failed, [])
        self.assertEqual(host._onebot_turn_id, "")
        self.assertEqual(host._onebot_turn_deadline, 0.0)

    def test_empty_reply_marks_turn_failed(self):
        host = self._started()
        host._onebot_turn_id = "t1"
        host._notify_channel_turn_finished("", "chat_error")
        self.assertEqual(host._onebot_service.completed, [])
        self.assertEqual(host._onebot_service.failed, [("t1", "chat_error")])

    def test_reply_without_active_turn_is_ignored(self):
        """本地手打的一轮不能触发回发 QQ。"""
        host = self._started()
        host._notify_channel_turn_finished("本地聊天")
        self.assertEqual(host._onebot_service.completed, [])
        self.assertEqual(host._onebot_service.failed, [])

    def test_reply_is_idempotent_after_first_delivery(self):
        host = self._started()
        host._onebot_turn_id = "t1"
        host._notify_channel_turn_finished("第一次")
        host._notify_channel_turn_finished("第二次")
        self.assertEqual(host._onebot_service.completed, [("t1", "第一次")])

    # -- 重连 ----------------------------------------------------------- #

    def test_reload_replaces_service_and_timer(self):
        host = self._started()
        first_service = host._onebot_service
        first_timer = host._onebot_poll_timer

        host._reload_onebot()

        self.assertTrue(first_service.stopped)
        self.assertTrue(first_timer.stopped)
        self.assertIsNot(host._onebot_service, first_service)
        self.assertTrue(host._onebot_service.started)

    def test_reload_without_enabled_config_leaves_nothing(self):
        host = self._started()
        host.config = {"onebot": {"enabled": False}}
        host._reload_onebot()
        self.assertIsNone(host._onebot_service)


if __name__ == "__main__":
    unittest.main()
