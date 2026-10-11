"""OneBot v11 协议层、配置、服务编排与接收端的单元测试。

按项目约定用 ``unittest``；网络服务器不绑真实端口，直接用
``httpx.ASGITransport`` 驱动 ASGI 应用。
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from meapet.onebot import cq, events, segments  # noqa: E402
from meapet.onebot.config import (  # noqa: E402
    DEFAULT_ONEBOT,
    active_modes,
    normalize_onebot_config,
    validate_onebot_config,
)
from meapet.onebot.service import OneBotService  # noqa: E402
from meapet.onebot.transport.server import OneBotServer, group_listeners  # noqa: E402
from meapet.onebot.types import ApiResponse, MessageSegment  # noqa: E402


class CqCodecTests(unittest.TestCase):
    def test_escape_and_unescape_round_trip(self) -> None:
        raw = "a&b[c]d,e"
        self.assertEqual(cq.unescape(cq.escape_value(raw)), raw)

    def test_unescape_is_not_double_decoded(self) -> None:
        # &amp;#91; 必须解成字面量 &#91;，而不是 [
        self.assertEqual(cq.unescape("&amp;#91;"), "&#91;")
        self.assertEqual(cq.unescape("&#91;"), "[")

    def test_parse_cq_mixes_text_and_segments(self) -> None:
        parsed = cq.parse_cq("hi[CQ:at,qq=10000] there[CQ:image,file=a.jpg]")
        self.assertEqual([item[0] for item in parsed], ["text", "at", "text", "image"])
        self.assertEqual(parsed[0][1]["text"], "hi")
        self.assertEqual(parsed[1][1]["qq"], "10000")
        self.assertEqual(parsed[3][1]["file"], "a.jpg")

    def test_parse_cq_decodes_escaped_separators(self) -> None:
        parsed = cq.parse_cq("[CQ:image,file=a&#44;b.jpg]")
        self.assertEqual(parsed[0][1]["file"], "a,b.jpg")

    def test_parse_cq_keeps_unterminated_code_as_text(self) -> None:
        parsed = cq.parse_cq("[CQ:at,qq=1")
        self.assertEqual(parsed, [("text", {"text": "[CQ:at,qq=1"})])

    def test_invalid_cq_body_stays_literal(self) -> None:
        parsed = cq.parse_cq("[CQ:]")
        self.assertEqual(parsed[0][0], "text")
        self.assertEqual(parsed[0][1]["text"], "[CQ:]")

    def test_build_cq_round_trip(self) -> None:
        built = cq.build_cq([("text", {"text": "a[b]"}), ("at", {"qq": "1"})])
        self.assertEqual(built, "a&#91;b&#93;[CQ:at,qq=1]")
        self.assertEqual(cq.parse_cq(built)[0][1]["text"], "a[b]")


class SegmentTests(unittest.TestCase):
    def test_string_message_is_cq_decoded(self) -> None:
        result = segments.normalize_message("[CQ:at,qq=all] hello")
        self.assertEqual(segments.segments_to_text(result), "@全体成员 hello")

    def test_array_message_is_not_unescaped(self) -> None:
        result = segments.normalize_message(
            [{"type": "text", "data": {"text": "&#91;literal&#93;"}}]
        )
        self.assertEqual(segments.segments_to_text(result), "&#91;literal&#93;")

    def test_single_segment_object(self) -> None:
        result = segments.normalize_message({"type": "text", "data": {"text": "x"}})
        self.assertEqual(len(result), 1)

    def test_none_and_garbage_degrade_to_empty(self) -> None:
        for value in (None, 42, {"data": {}}, []):
            self.assertEqual(segments.normalize_message(value), ())

    def test_placeholders_for_non_text_segments(self) -> None:
        result = segments.normalize_message(
            [
                {"type": "image", "data": {"file": "a"}},
                {"type": "record", "data": {"file": "b"}},
                {"type": "reply", "data": {"id": "3"}},
            ]
        )
        self.assertEqual(segments.segments_to_text(result), "[图片][语音]")

    def test_is_at_self_matches_self_and_all(self) -> None:
        self.assertTrue(segments.is_at_self(segments.normalize_message("[CQ:at,qq=999]"), 999))
        self.assertTrue(segments.is_at_self(segments.normalize_message("[CQ:at,qq=all]"), 999))
        self.assertFalse(segments.is_at_self(segments.normalize_message("[CQ:at,qq=1]"), 999))

    def test_segments_to_cq_round_trip(self) -> None:
        original = (MessageSegment("text", {"text": "a,b"}),)
        self.assertEqual(
            segments.normalize_message(segments.segments_to_cq(original)), original
        )


class EventParsingTests(unittest.TestCase):
    def test_private_message(self) -> None:
        event = events.parse_event(
            {
                "time": 1,
                "self_id": 10000,
                "post_type": "message",
                "message_type": "private",
                "sub_type": "friend",
                "message_id": 7,
                "user_id": 42,
                "message": "hello",
                "raw_message": "hello",
                "sender": {"nickname": "测试", "card": "", "role": ""},
            }
        )
        self.assertIsNotNone(event)
        message = event.message
        self.assertTrue(message.is_private)
        self.assertEqual(message.text, "hello")
        self.assertEqual(message.display_name, "测试")
        self.assertEqual(message.conversation_key, "private:42")

    def test_group_message_without_message_type_infers_group(self) -> None:
        event = events.parse_event(
            {"post_type": "message", "group_id": 888, "user_id": 42, "message": "hi"}
        )
        self.assertTrue(event.message.is_group)
        self.assertEqual(event.message.conversation_key, "group:888")

    def test_group_message_detects_at_self(self) -> None:
        event = events.parse_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 888,
                "user_id": 42,
                "self_id": 10000,
                "message": [{"type": "at", "data": {"qq": "10000"}}],
            }
        )
        self.assertTrue(event.message.is_at_self)

    def test_message_falls_back_to_raw_message(self) -> None:
        event = events.parse_event(
            {"post_type": "message", "message_type": "private", "user_id": 1,
             "raw_message": "fallback"}
        )
        self.assertEqual(event.message.text, "fallback")

    def test_notice_request_meta_events(self) -> None:
        self.assertEqual(
            events.parse_event({"post_type": "notice", "notice_type": "friend_add"}).kind,
            "notice",
        )
        self.assertEqual(
            events.parse_event({"post_type": "request", "request_type": "friend"}).request_type,
            "friend",
        )
        meta = events.parse_event(
            {"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 1}
        )
        self.assertEqual(meta.meta_event_type, "heartbeat")
        self.assertIsNone(meta.message)

    def test_unknown_post_type_is_kept_for_forwarding(self) -> None:
        event = events.parse_event({"post_type": "custom_thing", "self_id": 1})
        self.assertEqual(event.kind, "custom_thing")
        self.assertIsNone(event.message)

    def test_non_mapping_returns_none(self) -> None:
        self.assertIsNone(events.parse_event("nope"))

    def test_api_response_detection(self) -> None:
        self.assertTrue(events.is_api_response({"status": "ok", "retcode": 0, "echo": "1"}))
        self.assertFalse(events.is_api_response({"post_type": "message"}))
        self.assertFalse(events.is_api_response(None))

    def test_summarize_does_not_leak_text(self) -> None:
        event = events.parse_event(
            {"post_type": "message", "message_type": "private", "user_id": 5,
             "message": "secret-token-abc"}
        )
        self.assertNotIn("secret-token-abc", events.summarize(event))


class ConfigTests(unittest.TestCase):
    def test_defaults_when_given_garbage(self) -> None:
        cfg = normalize_onebot_config("not a dict")
        self.assertEqual(cfg["reply_mode"], "pet")
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["reverse_ws"]["port"], 14321)
        self.assertEqual(cfg["hub"]["ws_path"], "/onebot/v11/hub")

    def test_normalize_is_idempotent(self) -> None:
        once = normalize_onebot_config(
            {"enabled": True, "reply_mode": "BOTH", "self_ids": ["1", 2, 2, "x"],
             "reverse_ws": {"port": 99999}, "queue": {"max_pending": 9999}}
        )
        twice = normalize_onebot_config(once)
        self.assertEqual(once, twice)
        self.assertEqual(once["self_ids"], [1, 2])
        self.assertEqual(once["reverse_ws"]["port"], 14321)  # 越界回落默认
        self.assertEqual(once["queue"]["max_pending"], 16)
        self.assertEqual(once["reply_mode"], "both")

    def test_paths_get_leading_slash(self) -> None:
        cfg = normalize_onebot_config({"reverse_ws": {"path": "onebot/v11/ws"}})
        self.assertEqual(cfg["reverse_ws"]["path"], "/onebot/v11/ws")

    def test_reconnect_max_clamped_up_to_initial(self) -> None:
        cfg = normalize_onebot_config(
            {"forward_ws": {"reconnect_initial_ms": 5000, "reconnect_max_ms": 1000}}
        )
        self.assertEqual(cfg["forward_ws"]["reconnect_max_ms"], 5000)

    def test_active_modes(self) -> None:
        cfg = normalize_onebot_config(
            {"forward_ws": {"enabled": True}, "hub": {"enabled": True}}
        )
        self.assertEqual(active_modes(cfg), ["forward_ws", "hub"])

    def test_validation_flags_enabled_but_idle(self) -> None:
        issues = validate_onebot_config(normalize_onebot_config({"enabled": True}))
        self.assertTrue(any("没有任何启用中的通信方式" in item for item in issues))

    def test_validation_flags_http_post_without_http_api(self) -> None:
        cfg = normalize_onebot_config(
            {"enabled": True, "http_post": {"enabled": True}}
        )
        self.assertTrue(any("http_api" in item for item in validate_onebot_config(cfg)))

    def test_validation_flags_non_loopback_without_optin(self) -> None:
        cfg = normalize_onebot_config(
            {"enabled": True,
             "reverse_ws": {"enabled": True, "host": "0.0.0.0"}}
        )
        self.assertTrue(
            any("allow_insecure_remote" in item for item in validate_onebot_config(cfg))
        )
        cfg["reverse_ws"]["allow_insecure_remote"] = True
        self.assertEqual(validate_onebot_config(cfg), [])

    def test_validation_flags_bad_urls(self) -> None:
        cfg = normalize_onebot_config(
            {"enabled": True, "forward_ws": {"enabled": True, "url": "http://x"}}
        )
        self.assertTrue(any("ws://" in item for item in validate_onebot_config(cfg)))

    def test_disabled_config_has_no_issues(self) -> None:
        self.assertEqual(validate_onebot_config(normalize_onebot_config({})), [])

    def test_defaults_are_not_mutated(self) -> None:
        normalize_onebot_config({"allow": {"users": [1, 2, 3]}})
        self.assertEqual(DEFAULT_ONEBOT["allow"]["users"], [])


class FakeSender:
    name = "forward_ws"

    def __init__(self, ok: bool = True) -> None:
        self.available = True
        self.ok = ok
        self.calls = []

    async def send(self, action, params):
        self.calls.append((action, dict(params)))
        return ApiResponse(ok=self.ok, status="ok" if self.ok else "failed",
                           retcode=0 if self.ok else 1, data={"message_id": 11})


def _private_event(text: str, user_id: int = 42, message_id: int = 1) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": user_id,
        "self_id": 10000,
        "time": 1,
        "message": text,
        "raw_message": text,
        "sender": {"nickname": "n", "card": "", "role": ""},
    }


def _group_event(text: str, *, at: bool, group_id: int = 888) -> dict:
    prefix = [{"type": "at", "data": {"qq": "10000"}}] if at else []
    return {
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 2,
        "group_id": group_id,
        "user_id": 43,
        "self_id": 10000,
        "time": 1,
        "message": prefix + [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "sender": {"nickname": "n", "card": "card", "role": "member"},
    }


class ServiceRoutingTests(unittest.TestCase):
    def _service(self, **overrides) -> OneBotService:
        config = {"enabled": True, "reply_mode": "pet"}
        config.update(overrides)
        return OneBotService(config)

    def test_private_message_is_queued(self) -> None:
        service = self._service()
        service._on_upstream_event(_private_event("你好"), "forward_ws")
        self.assertEqual(service.pending_count, 1)
        self.assertEqual(service.stats["messages_queued"], 1)

    def test_group_message_requires_at_by_default(self) -> None:
        service = self._service()
        service._on_upstream_event(_group_event("hi", at=False), "forward_ws")
        self.assertEqual(service.pending_count, 0)
        self.assertEqual(service.stats["messages_denied"], 1)

        service._on_upstream_event(_group_event("hi", at=True), "forward_ws")
        self.assertEqual(service.pending_count, 1)

    def test_allowlist_blocks_other_users(self) -> None:
        service = self._service(allow={"users": [7]})
        service._on_upstream_event(_private_event("hi", user_id=42), "forward_ws")
        self.assertEqual(service.pending_count, 0)

    def test_empty_text_is_ignored(self) -> None:
        service = self._service()
        service._on_upstream_event(_private_event("   "), "forward_ws")
        self.assertEqual(service.pending_count, 0)


    def test_hub_mode_does_not_queue_for_pet(self) -> None:
        service = self._service(reply_mode="hub", hub={"enabled": True})
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        self.assertEqual(service.pending_count, 0)

    def test_self_id_is_learned_from_events(self) -> None:
        service = self._service()
        self.assertEqual(service.self_ids, [])
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        self.assertEqual(service.self_ids, [10000])

    def test_queue_drops_when_full(self) -> None:
        service = self._service(queue={"max_pending": 1})
        service._on_upstream_event(_private_event("a", message_id=1), "forward_ws")
        service._on_upstream_event(_private_event("b", message_id=2), "forward_ws")
        self.assertEqual(service.pending_count, 1)
        self.assertEqual(service.stats["messages_dropped"], 1)

    def test_take_ready_turn_is_exclusive(self) -> None:
        service = self._service()
        service._on_upstream_event(_private_event("a", message_id=1), "forward_ws")
        service._on_upstream_event(_private_event("b", message_id=2), "forward_ws")
        first = service.take_ready_turn()
        self.assertIsNotNone(first)
        self.assertIsNone(service.take_ready_turn())
        service.fail_turn(first.turn_id, "test")
        second = service.take_ready_turn()
        self.assertIsNotNone(second)
        self.assertNotEqual(first.turn_id, second.turn_id)

    def test_expired_active_turn_is_reaped(self) -> None:
        service = self._service(queue={"turn_timeout_s": 5})
        service._on_upstream_event(_private_event("a"), "forward_ws")
        turn = service.take_ready_turn()
        self.assertIsNotNone(turn)
        turn.deadline = 0.0  # 模拟已超时
        service._on_upstream_event(_private_event("b", message_id=2), "forward_ws")
        nxt = service.take_ready_turn()
        self.assertIsNotNone(nxt)
        self.assertEqual(service.stats["turns_timed_out"], 1)

    def test_group_prompt_includes_speaker(self) -> None:
        service = self._service()
        service._on_upstream_event(_group_event("在吗", at=True), "forward_ws")
        turn = service.take_ready_turn()
        self.assertIn("card", turn.prompt)
        self.assertIn("在吗", turn.prompt)

    def test_fail_turn_for_unknown_id_is_noop(self) -> None:
        service = self._service()
        service.fail_turn("nope", "x")  # 不应抛异常

    def test_status_snapshot_covers_enabled_modes(self) -> None:
        service = self._service(
            forward_ws={"enabled": True},
            http_api={"enabled": True},
            hub={"enabled": True},
        )
        names = {item.name for item in service.status_snapshot()}
        self.assertIn("forward_ws", names)
        self.assertIn("http_api", names)
        self.assertIn("hub", names)

    def test_service_disabled_does_not_start(self) -> None:
        service = OneBotService({"enabled": False})
        service.start()
        self.assertFalse(service.running)
        service.stop()

    def test_stop_is_safe_when_never_started(self) -> None:
        service = self._service()
        service.stop()
        self.assertFalse(service.running)


class ServiceAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_uses_session_source(self) -> None:
        service = OneBotService({"enabled": True, "reply_mode": "pet"})
        sender = FakeSender()
        service.senders.register(sender)
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        turn = service.take_ready_turn()
        ok = await service.send_message(turn.message, "回复")
        self.assertTrue(ok)
        self.assertEqual(sender.calls[0][0], "send_private_msg")
        self.assertEqual(sender.calls[0][1]["user_id"], 42)
        self.assertEqual(service.stats["replies_sent"], 1)

    async def test_send_message_reports_failure(self) -> None:
        service = OneBotService({"enabled": True})
        service.senders.register(FakeSender(ok=False))
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        turn = service.take_ready_turn()
        self.assertFalse(await service.send_message(turn.message, "回复"))
        self.assertEqual(service.stats["send_failures"], 1)

    async def test_send_without_sender_fails_gracefully(self) -> None:
        service = OneBotService({"enabled": True})
        self.assertFalse(await service.send_text_to("private", 1, "x"))

    async def test_complete_turn_releases_queue(self) -> None:
        service = OneBotService({"enabled": True})
        service.senders.register(FakeSender())
        service._on_upstream_event(_private_event("a", message_id=1), "forward_ws")
        turn = service.take_ready_turn()
        service.complete_turn(turn.turn_id, "hi")
        await asyncio.sleep(0)
        self.assertFalse(service.has_active_turn)
        self.assertEqual(service.stats["replies_sent"], 1)

    async def test_hub_call_rejects_non_whitelisted_action(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        result = await service.handle_hub_call("set_group_kick", {})
        self.assertFalse(result.ok)
        self.assertEqual(result.retcode, 103)

    async def test_hub_call_denied_when_send_disabled(self) -> None:
        service = OneBotService(
            {"enabled": True, "hub": {"enabled": True, "allow_send": False}}
        )
        result = await service.handle_hub_call("send_private_msg", {"user_id": 1, "message": "x"})
        self.assertFalse(result.ok)
        self.assertEqual(result.retcode, 103)

    async def test_hub_call_forwards_to_sender(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        sender = FakeSender()
        service.senders.register(sender)
        result = await service.handle_hub_call(
            "send_private_msg", {"user_id": 9, "message": "hi"}
        )
        self.assertTrue(result.ok)
        self.assertEqual(sender.calls[0][0], "send_private_msg")

    async def test_hub_send_msg_auto_detects_group(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        sender = FakeSender()
        service.senders.register(sender)
        await service.handle_hub_call("send_msg", {"group_id": 5, "message": "hi"})
        self.assertEqual(sender.calls[0][0], "send_group_msg")
        self.assertEqual(sender.calls[0][1]["group_id"], 5)

    async def test_hub_send_msg_without_target_fails(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        service.senders.register(FakeSender())
        result = await service.handle_hub_call("send_msg", {"message": "hi"})
        self.assertFalse(result.ok)
        self.assertEqual(result.retcode, 100)

    async def test_local_get_version_info_without_upstream(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        result = await service.handle_hub_call("get_version_info", {})
        self.assertTrue(result.ok)
        self.assertEqual(result.data["protocol_version"], "v11")

    async def test_publish_skipped_when_hub_disabled(self) -> None:
        service = OneBotService({"enabled": True, "reply_mode": "both"})
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        self.assertEqual(service.stats["events_published"], 0)


class _RecordingHubSession:
    def __init__(self, fail: bool = False) -> None:
        self.name = "hub:1"
        self.available = True
        self.fail = fail
        self.pushed = []
        self.closed = False

    async def push(self, payload):
        self.pushed.append(dict(payload))
        return not self.fail

    def close(self, reason: str = "") -> None:
        self.closed = True


class HubPublishTests(unittest.IsolatedAsyncioTestCase):
    async def _drain(self) -> None:
        """让 push 任务与其 done 回调都跑完。"""
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_publish_reaches_hub_sessions(self) -> None:
        service = OneBotService(
            {"enabled": True, "reply_mode": "hub", "hub": {"enabled": True}}
        )
        session = _RecordingHubSession()
        service._on_hub_open(session)
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        await self._drain()
        self.assertEqual(len(session.pushed), 1)
        self.assertEqual(session.pushed[0]["post_type"], "message")

    async def test_failed_publish_detaches_session(self) -> None:
        service = OneBotService(
            {"enabled": True, "reply_mode": "hub", "hub": {"enabled": True}}
        )
        session = _RecordingHubSession(fail=True)
        service._on_hub_open(session)
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        await self._drain()
        self.assertNotIn(session, service.hub_sessions)

    async def test_notice_events_are_published(self) -> None:
        service = OneBotService(
            {"enabled": True, "reply_mode": "hub", "hub": {"enabled": True}}
        )
        session = _RecordingHubSession()
        service._on_hub_open(session)
        service._on_upstream_event(
            {"post_type": "notice", "notice_type": "friend_add", "self_id": 1},
            "forward_ws",
        )
        await self._drain()
        self.assertEqual(session.pushed[0]["notice_type"], "friend_add")


class GroupListenerTests(unittest.TestCase):
    def test_merges_same_host_port(self) -> None:
        cfg = normalize_onebot_config(
            {"reverse_ws": {"enabled": True}, "hub": {"enabled": True}}
        )
        groups = group_listeners(cfg)
        self.assertEqual(len(groups), 1)
        self.assertIsNotNone(groups[0]["reverse_ws"])
        self.assertIsNotNone(groups[0]["hub"])

    def test_separate_ports_produce_separate_listeners(self) -> None:
        cfg = normalize_onebot_config(
            {"reverse_ws": {"enabled": True, "port": 6700},
             "http_post": {"enabled": True, "port": 6720}}
        )
        self.assertEqual(len(group_listeners(cfg)), 2)

    def test_disabled_sections_are_ignored(self) -> None:
        self.assertEqual(group_listeners(normalize_onebot_config({})), [])


def _make_server(service: OneBotService, **kwargs) -> OneBotServer:
    defaults = dict(
        host="127.0.0.1",
        port=6700,
        on_upstream_event=service._on_upstream_event,
        on_hub_call=service.handle_hub_call,
        on_upstream_open=service._on_upstream_open,
        on_upstream_closed=service._on_upstream_closed,
        on_hub_open=service._on_hub_open,
        on_hub_closed=service._on_hub_closed,
    )
    defaults.update(kwargs)
    return OneBotServer(**defaults)


class ServerBodyHardeningTests(unittest.IsolatedAsyncioTestCase):
    """事件接收端的两处硬化：分块体的内存上界、非对象体不再假装成功。"""

    def _client(self, server: OneBotServer) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=server.app)
        return httpx.AsyncClient(transport=transport, base_url="http://onebot")

    async def test_declared_oversize_body_is_rejected(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        from meapet.onebot.transport import server as server_mod

        original = server_mod.MAX_BODY_BYTES
        server_mod.MAX_BODY_BYTES = 64
        try:
            async with self._client(server) as client:
                # bytes 内容 → httpx 会带上 content-length
                response = await client.post("/onebot/v11/http", content=b"x" * 256)
        finally:
            server_mod.MAX_BODY_BYTES = original
        self.assertEqual(response.status_code, 413)
        self.assertEqual(service.pending_count, 0)

    async def test_chunked_oversize_body_is_rejected(self) -> None:
        """分块请求不带 content-length，只看该头会无上限地读进内存。"""
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        from meapet.onebot.transport import server as server_mod

        original = server_mod.MAX_BODY_BYTES
        server_mod.MAX_BODY_BYTES = 64

        async def chunks():
            for _ in range(6):
                yield b"y" * 32  # 共 192 > 64

        try:
            async with self._client(server) as client:
                response = await client.post("/onebot/v11/http", content=chunks())
        finally:
            server_mod.MAX_BODY_BYTES = original
        self.assertEqual(response.status_code, 413)
        self.assertEqual(service.pending_count, 0)

    async def test_chunked_body_within_cap_is_accepted(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        body = json.dumps(_private_event("hi")).encode("utf-8")

        async def chunks():
            for index in range(0, len(body), 8):
                yield body[index:index + 8]

        async with self._client(server) as client:
            response = await client.post("/onebot/v11/http", content=chunks())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(service.pending_count, 1)

    async def test_non_object_json_body_is_rejected_not_acknowledged(self) -> None:
        """数组/字符串不可能是事件；返回 ok 会让对端以为投递成功。"""
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        async with self._client(server) as client:
            array_body = await client.post("/onebot/v11/http", content=b"[1, 2, 3]")
            string_body = await client.post("/onebot/v11/http", content=b'"hello"')
        self.assertEqual(array_body.status_code, 400)
        self.assertEqual(string_body.status_code, 400)
        self.assertEqual(service.pending_count, 0)

    async def test_invalid_json_body_is_rejected(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        async with self._client(server) as client:
            response = await client.post("/onebot/v11/http", content=b"{not json")
        self.assertEqual(response.status_code, 400)


class ServerEndpointTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, server: OneBotServer) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=server.app)
        return httpx.AsyncClient(transport=transport, base_url="http://onebot")

    async def test_http_post_event_is_routed(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        async with self._client(server) as client:
            response = await client.post("/onebot/v11/http", json=_private_event("hi"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(service.pending_count, 1)

    async def test_http_post_requires_token(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(
            service,
            http_post_path="/onebot/v11/http",
            http_post_token="s3cret",
        )
        async with self._client(server) as client:
            denied = await client.post("/onebot/v11/http", json=_private_event("hi"))
            allowed = await client.post(
                "/onebot/v11/http",
                json=_private_event("hi"),
                headers={"Authorization": "Bearer s3cret"},
            )
            query = await client.post(
                "/onebot/v11/http?access_token=s3cret", json=_private_event("hi")
            )
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json()["retcode"], 104)
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(query.status_code, 200)

    async def test_http_post_rejects_malformed_body(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        async with self._client(server) as client:
            response = await client.post(
                "/onebot/v11/http",
                content=b"not json",
                headers={"content-type": "application/json"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(service.pending_count, 0)

    async def test_hub_http_serves_api_call(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        sender = FakeSender()
        service.senders.register(sender)
        server = _make_server(service, hub_http_path="/onebot/v11/hub/http")
        async with self._client(server) as client:
            response = await client.post(
                "/onebot/v11/hub/http",
                json={"action": "send_private_msg",
                      "params": {"user_id": 3, "message": "x"}, "echo": "e1"},
            )
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["echo"], "e1")
        self.assertEqual(sender.calls[0][0], "send_private_msg")

    async def test_hub_http_missing_action_returns_100(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        server = _make_server(service, hub_http_path="/onebot/v11/hub/http")
        async with self._client(server) as client:
            response = await client.post("/onebot/v11/hub/http", json={"params": {}})
        self.assertEqual(response.json()["retcode"], 100)

    async def test_hub_http_requires_token(self) -> None:
        service = OneBotService({"enabled": True, "hub": {"enabled": True}})
        server = _make_server(
            service, hub_http_path="/onebot/v11/hub/http", hub_token="tok"
        )
        async with self._client(server) as client:
            response = await client.post("/onebot/v11/hub/http", json={"action": "get_status"})
        self.assertEqual(response.status_code, 401)

    async def test_unrouted_path_is_404(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(service, http_post_path="/onebot/v11/http")
        async with self._client(server) as client:
            response = await client.post("/nope", json={})
        self.assertEqual(response.status_code, 404)

    async def test_server_reports_endpoint(self) -> None:
        service = OneBotService({"enabled": True})
        server = _make_server(service, port=6799)
        self.assertEqual(server.endpoint, "127.0.0.1:6799")
        self.assertFalse(server.running)
        server.stop()


class WsSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_echo_correlates_api_response(self) -> None:
        from meapet.onebot.transport.ws_session import WsSession

        sent = []

        async def send_text(raw: str) -> None:
            sent.append(raw)

        session = WsSession("t", send_text, call_timeout_s=1.0)
        task = asyncio.ensure_future(session.send("get_status", {}))
        await asyncio.sleep(0)
        payload = __import__("json").loads(sent[0])
        session.feed(
            __import__("json").dumps(
                {"status": "ok", "retcode": 0, "data": {"online": True},
                 "echo": payload["echo"]}
            ),
            lambda *_: None,
        )
        result = await task
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"online": True})

    async def test_close_fails_pending_calls(self) -> None:
        from meapet.onebot.transport.ws_session import WsSession

        async def send_text(raw: str) -> None:
            return None

        session = WsSession("t", send_text, call_timeout_s=5.0)
        task = asyncio.ensure_future(session.send("get_status", {}))
        await asyncio.sleep(0)
        session.close("上游断开")
        result = await task
        self.assertFalse(result.ok)

    async def test_events_are_dispatched_to_callback(self) -> None:
        from meapet.onebot.transport.ws_session import WsSession

        async def send_text(raw: str) -> None:
            return None

        received = []
        session = WsSession("t", send_text)
        session.feed('{"post_type":"meta_event","self_id":1}', lambda p, s: received.append((p, s)))
        self.assertEqual(received[0][1], "t")

    async def test_invalid_json_is_ignored(self) -> None:
        from meapet.onebot.transport.ws_session import WsSession

        async def send_text(raw: str) -> None:
            return None

        session = WsSession("t", send_text)
        session.feed("{{{", lambda *_: self.fail("不应回调"))

    async def test_push_returns_false_after_close(self) -> None:
        from meapet.onebot.transport.ws_session import WsSession

        async def send_text(raw: str) -> None:
            return None

        session = WsSession("t", send_text)
        session.close()
        self.assertFalse(await session.push({"a": 1}))


class WsSessionEchoFallbackTests(unittest.IsolatedAsyncioTestCase):
    """个别实现端的应答不带 ``echo``（如 SnowLuma 文档里的信封）。

    只有一个在途调用时按唯一性归位；有多个就宁可不猜。
    """

    @staticmethod
    def _session(collector: list):
        from meapet.onebot.transport.ws_session import WsSession

        async def send_text(raw: str) -> None:
            collector.append(raw)

        return WsSession("t", send_text, call_timeout_s=2.0)

    async def test_response_without_echo_resolves_the_only_pending_call(self) -> None:
        sent: list = []
        session = self._session(sent)
        task = asyncio.ensure_future(session.send("get_status", {}))
        await asyncio.sleep(0)

        session.feed(
            json.dumps({"status": "ok", "retcode": 0, "data": {"online": True}}),
            lambda *_: self.fail("响应不应被当成事件"),
        )

        result = await task
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"online": True})
        self.assertTrue(result.echo)  # 回填成请求时的 echo

    async def test_response_without_echo_is_ignored_when_ambiguous(self) -> None:
        sent: list = []
        session = self._session(sent)
        first = asyncio.ensure_future(session.send("get_status", {}))
        second = asyncio.ensure_future(session.send("get_login_info", {}))
        await asyncio.sleep(0)

        session.feed(json.dumps({"status": "ok", "retcode": 0, "data": 1}), lambda *_: None)
        await asyncio.sleep(0)

        # 两个都在途 → 不猜，两个都还挂着
        self.assertFalse(first.done())
        self.assertFalse(second.done())

        session.close("cleanup")
        self.assertFalse((await first).ok)
        self.assertFalse((await second).ok)

    async def test_response_with_echo_still_wins(self) -> None:
        sent: list = []
        session = self._session(sent)
        first = asyncio.ensure_future(session.send("get_status", {}))
        await asyncio.sleep(0)
        echo = json.loads(sent[0])["echo"]

        session.feed(
            json.dumps({"status": "ok", "retcode": 0, "data": "x", "echo": echo}),
            lambda *_: None,
        )
        self.assertTrue((await first).ok)


class SenderRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_source_then_falls_back(self) -> None:
        from meapet.onebot.api import SenderRegistry

        registry = SenderRegistry()
        primary = FakeSender()
        primary.name = "forward_ws"
        other = FakeSender()
        other.name = "reverse_ws:1"
        registry.register(primary, fallback=True)
        registry.register(other)

        await registry.send("get_status", {}, source="reverse_ws:1")
        self.assertEqual(len(other.calls), 1)
        self.assertEqual(len(primary.calls), 0)

        await registry.send("get_status", {}, source="missing")
        self.assertEqual(len(primary.calls), 1)

    async def test_no_sender_returns_failure(self) -> None:
        from meapet.onebot.api import SenderRegistry

        result = await SenderRegistry().send("get_status", {})
        self.assertFalse(result.ok)

    async def test_unavailable_sender_is_skipped(self) -> None:
        from meapet.onebot.api import SenderRegistry

        registry = SenderRegistry()
        dead = FakeSender()
        dead.available = False
        registry.register(dead)
        self.assertFalse((await registry.send("get_status", {})).ok)


class ApiResponseTests(unittest.TestCase):
    def test_from_payload_ok(self) -> None:
        result = ApiResponse.from_payload({"status": "ok", "retcode": 0, "data": 1})
        self.assertTrue(result.ok)
        self.assertEqual(result.data, 1)

    def test_from_payload_async_status(self) -> None:
        result = ApiResponse.from_payload({"status": "async", "retcode": 0})
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "async")

    def test_from_payload_garbage_is_failure(self) -> None:
        self.assertFalse(ApiResponse.from_payload({"retcode": "x"}).ok)

    def test_failure_helper(self) -> None:
        result = ApiResponse.failure("boom", echo="e")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "boom")


class HttpPushTests(unittest.IsolatedAsyncioTestCase):
    """``hub.push_urls``：事件 HTTP POST 上报 + 响应里的快速操作（规范 §2.2 / §7）。"""

    def _service(self, **hub_overrides) -> OneBotService:
        hub = {"enabled": True, "publish_events": True, "allow_send": True}
        hub.update(hub_overrides)
        return OneBotService({"enabled": True, "reply_mode": "hub", "hub": hub})

    async def _drain(self) -> None:
        for _ in range(8):
            await asyncio.sleep(0)

    async def _run_with_client(self, handler, service, payload) -> None:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def fake_get_client():
            return client

        try:
            with mock.patch("meapet.http_async.get_client", fake_get_client):
                service._on_upstream_event(payload, "forward_ws")
                await self._drain()
        finally:
            await client.aclose()

    async def test_event_is_posted_with_token(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={})

        service = self._service(
            push_urls=["http://consumer.test/onebot/v11/http"], push_token="ptok"
        )
        await self._run_with_client(handler, service, _private_event("你好"))

        self.assertEqual(seen["url"], "http://consumer.test/onebot/v11/http")
        self.assertEqual(seen["auth"], "Bearer ptok")
        self.assertEqual(seen["body"]["post_type"], "message")
        self.assertEqual(seen["body"]["raw_message"], "你好")
        self.assertEqual(service.stats["events_pushed"], 1)
        self.assertEqual(service.stats["push_failures"], 0)

    async def test_push_uses_hub_token_when_push_token_empty(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json={})

        service = self._service(
            access_token="hubtok", push_urls=["http://consumer.test/hook"]
        )
        await self._run_with_client(handler, service, _private_event("hi"))
        self.assertEqual(seen["auth"], "Bearer hubtok")

    async def test_quick_operation_reply_is_sent_upstream(self) -> None:
        """响应体里带 ``reply`` 时，应按规范 §7 立刻把它发回会话。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"reply": "收到", "auto_escape": False})

        service = self._service(push_urls=["http://consumer.test/hook"])
        sender = FakeSender()
        service.senders.register(sender)
        await self._run_with_client(handler, service, _private_event("在吗"))

        self.assertEqual(service.stats["quick_operations"], 1)
        self.assertEqual(len(sender.calls), 1)
        action, params = sender.calls[0]
        self.assertEqual(action, "send_private_msg")
        self.assertEqual(params["user_id"], 42)
        self.assertEqual(params["message"], "收到")

    async def test_quick_operation_respects_allow_send_false(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"reply": "收到"})

        service = self._service(push_urls=["http://consumer.test/hook"], allow_send=False)
        sender = FakeSender()
        service.senders.register(sender)
        await self._run_with_client(handler, service, _private_event("在吗"))

        self.assertEqual(service.stats["quick_operations"], 0)
        self.assertEqual(sender.calls, [])

    async def test_non_loopback_body_without_reply_is_ignored(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "retcode": 0})

        service = self._service(push_urls=["http://consumer.test/hook"])
        sender = FakeSender()
        service.senders.register(sender)
        await self._run_with_client(handler, service, _private_event("在吗"))
        self.assertEqual(service.stats["events_pushed"], 1)
        self.assertEqual(sender.calls, [])

    async def test_http_error_counts_as_failure(self) -> None:
        service = self._service(push_urls=["http://consumer.test/hook"])
        await self._run_with_client(
            lambda request: httpx.Response(500, text="boom"), service, _private_event("hi")
        )
        self.assertEqual(service.stats["events_pushed"], 0)
        self.assertEqual(service.stats["push_failures"], 1)

    async def test_transport_error_is_swallowed(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        service = self._service(push_urls=["http://consumer.test/hook"])
        await self._run_with_client(handler, service, _private_event("hi"))
        self.assertEqual(service.stats["push_failures"], 1)

    async def test_notice_events_are_also_pushed(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={})

        service = self._service(push_urls=["http://consumer.test/hook"])
        await self._run_with_client(
            handler, service,
            {"post_type": "notice", "notice_type": "friend_add", "self_id": 1},
        )
        self.assertEqual(seen["body"]["notice_type"], "friend_add")
        self.assertEqual(service.stats["quick_operations"], 0)

    async def test_no_push_urls_means_no_counter(self) -> None:
        service = self._service()
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        await self._drain()
        self.assertEqual(service.stats["events_published"], 0)
        self.assertEqual(service.stats["events_pushed"], 0)

    def test_push_urls_do_not_leak_into_defaults(self) -> None:
        normalize_onebot_config({"hub": {"push_urls": ["http://x.test/hook"]}})
        self.assertEqual(DEFAULT_ONEBOT["hub"]["push_urls"], [])


class HttpPushConfigTests(unittest.TestCase):
    def test_push_urls_are_filtered_and_deduplicated(self) -> None:
        cfg = normalize_onebot_config(
            {"hub": {"push_urls": [
                "http://a.test/hook", "ftp://nope", "", "http://a.test/hook",
                "https://b.test/hook", 42,
            ]}}
        )
        self.assertEqual(cfg["hub"]["push_urls"], ["http://a.test/hook", "https://b.test/hook"])

    def test_push_token_and_timeout_normalised(self) -> None:
        cfg = normalize_onebot_config(
            {"hub": {"push_token": "  tok  ", "push_timeout_s": 999}}
        )
        self.assertEqual(cfg["hub"]["push_token"], "tok")
        self.assertEqual(cfg["hub"]["push_timeout_s"], 5.0)  # 越界回落默认

    def test_push_urls_require_hub_enabled(self) -> None:
        cfg = normalize_onebot_config(
            {"enabled": True, "hub": {"push_urls": ["http://a.test/hook"]}}
        )
        self.assertTrue(any("push_urls" in item for item in validate_onebot_config(cfg)))

    def test_push_urls_require_publish_events(self) -> None:
        cfg = normalize_onebot_config(
            {"enabled": True,
             "hub": {"enabled": True, "publish_events": False,
                     "push_urls": ["http://a.test/hook"]}}
        )
        self.assertTrue(any("publish_events" in item for item in validate_onebot_config(cfg)))

    def test_cleartext_non_loopback_push_url_is_flagged(self) -> None:
        cfg = normalize_onebot_config(
            {"enabled": True,
             "hub": {"enabled": True, "push_urls": ["http://10.0.0.5/hook"]}}
        )
        self.assertTrue(any("明文" in item for item in validate_onebot_config(cfg)))

    def test_loopback_http_push_url_is_fine(self) -> None:
        cfg = normalize_onebot_config(
            {"enabled": True,
             "hub": {"enabled": True, "push_urls": ["http://127.0.0.1:9999/hook"]}}
        )
        self.assertEqual(validate_onebot_config(cfg), [])

    def test_scrub_secrets_clears_push_token(self) -> None:
        from meapet.config.store import scrub_secrets

        out = scrub_secrets(
            {"onebot": {"hub": {"access_token": "a", "push_token": "p"}}}
        )
        self.assertEqual(out["onebot"]["hub"]["access_token"], "")
        self.assertEqual(out["onebot"]["hub"]["push_token"], "")

    def test_same_port_path_collision_is_flagged(self) -> None:
        """同端口上两条端点用同一路径时，后注册的会被遮蔽。"""
        cfg = normalize_onebot_config(
            {
                "enabled": True,
                "reverse_ws": {"enabled": True, "host": "127.0.0.1", "port": 14321,
                               "path": "/same"},
                "hub": {"enabled": True, "host": "127.0.0.1", "port": 14321,
                        "ws_path": "/same"},
            }
        )
        issues = validate_onebot_config(cfg)
        self.assertTrue(any("遮蔽" in item for item in issues), issues)

    def test_same_path_on_different_ports_is_fine(self) -> None:
        cfg = normalize_onebot_config(
            {
                "enabled": True,
                "reverse_ws": {"enabled": True, "host": "127.0.0.1", "port": 14321,
                               "path": "/ws"},
                "hub": {"enabled": True, "host": "127.0.0.1", "port": 14322,
                        "ws_path": "/ws"},
            }
        )
        self.assertEqual(validate_onebot_config(cfg), [])

    def test_hub_ws_and_hub_http_paths_may_differ(self) -> None:
        cfg = normalize_onebot_config(
            {
                "enabled": True,
                "hub": {"enabled": True, "host": "127.0.0.1", "port": 14321,
                        "ws_path": "/onebot/v11/hub",
                        "http_path": "/onebot/v11/hub/http"},
            }
        )
        self.assertEqual(validate_onebot_config(cfg), [])


class ServiceStartStopRaceTests(unittest.IsolatedAsyncioTestCase):
    """``stop()`` 抢在 ``_start_all()`` 前面时，不能留下没人负责的监听端口。

    真实场景：用户改配置触发 ``_reload_onebot()``（先 stop 再 start），或退出桌宠时
    ``_quit`` 调 stop，而上一轮的 ``_start_all`` 协程可能还在跑。
    """

    @staticmethod
    def _cfg() -> dict:
        return {
            "enabled": True,
            "reply_mode": "both",
            "http_api": {"enabled": True, "api_base": "http://127.0.0.1:9"},
            "forward_ws": {"enabled": True, "url": "ws://127.0.0.1:9/"},
            "reverse_ws": {"enabled": True, "host": "127.0.0.1", "path": "/x"},
            "hub": {"enabled": True, "host": "127.0.0.1"},
        }

    async def test_start_all_aborts_after_stop(self) -> None:
        service = OneBotService(self._cfg())
        service._started = False  # 模拟 stop() 已经跑过

        await service._start_all()

        self.assertEqual(service.servers, [])
        self.assertEqual(service.forward_clients, [])
        self.assertIsNone(service.http_sender)
        # 发送端注册表也必须保持干净（否则回复会挑到一个并不存在的通道）
        self.assertIsNone(service.senders.pick(""))

    async def test_stop_is_idempotent_and_clears_state(self) -> None:
        service = OneBotService(self._cfg())
        service._started = False
        await service._start_all()
        service.stop()
        service.stop()  # 第二次不应抛异常
        self.assertEqual(service.servers, [])
        self.assertEqual(service.forward_clients, [])
        self.assertEqual(service.hub_sessions, [])
        self.assertEqual(service.upstream_sessions, [])

    def test_started_flag_is_set_before_the_coroutine_runs(self) -> None:
        """``start()`` 必须**先**置位 ``_started`` 再提交协程。

        用"同步跑完协程体"的假 submit 来复现这个竞态：``_start_all`` 里没有 await，
        所以协程体会在 submit 调用内部一次性跑完——正是"事件循环立刻抢到执行权"的极端情形。
        若顺序写反，这里会看到 ``_started`` 还是 False，于是一次正常启动被误判成
        "被 stop 抢先" 而整个不启动。
        """
        service = OneBotService(
            {
                "enabled": True,
                "reply_mode": "both",
                # 只开不需要监听端口的两条通道，避免测试里真的 bind
                "http_api": {"enabled": True, "api_base": "http://127.0.0.1:9"},
                "forward_ws": {"enabled": True, "url": "ws://127.0.0.1:9/"},
            }
        )
        observed: dict = {}
        original = service._start_all

        async def probe() -> None:
            observed["startedAtEntry"] = service._started
            await original()

        service._start_all = probe  # type: ignore[method-assign]

        def immediate_submit(coro):
            try:
                coro.send(None)
            except StopIteration:
                pass
            return None

        with mock.patch("meapet.async_runtime.submit", immediate_submit):
            service.start()

        self.assertTrue(
            observed.get("startedAtEntry"),
            "进入 _start_all() 时 _started 必须已经是 True（否则正常启动会被误判放弃）",
        )
        self.assertIsNotNone(service.http_sender)
        self.assertEqual(len(service.forward_clients), 1)
        service.stop()


class _NullHubSession:
    """占位 hub 消费者：只为让 `hub_sessions` 非空（`_publish` 才会走广播那条路）。

    `push` 是异步的、由 `_spawn` 调度，所以**不要**拿它当同步判据——
    那会变成一条看运气的测试。判据用 `stats["events_published"]`（同步自增）。
    """

    available = True

    async def push(self, payload):
        return True

    async def send(self, action, params):  # pragma: no cover - 准入测试不发消息
        raise AssertionError("不该走到发送")


class AllowListBehaviorTests(unittest.TestCase):
    """`allow.*` 是向导上对外宣称的「准入规则」，逐条钉住它的真实行为。

    这是 `reply_mode` 含 `pet` 时**唯一**的用户可见访问控制。
    """

    def _service(self, **overrides) -> OneBotService:
        config = {"enabled": True, "reply_mode": "pet"}
        config.update(overrides)
        return OneBotService(config)

    # ── 用户名单 ──────────────────────────────────────────────────────
    def test_listed_user_is_queued_and_others_are_denied(self) -> None:
        """正向对照：只测"被拦"不测"被放行"的话，一个恒假的实现也能过。"""
        service = self._service(allow={"users": [7]})
        service._on_upstream_event(_private_event("hi", user_id=7), "forward_ws")
        self.assertEqual(service.pending_count, 1)
        self.assertEqual(service.stats["messages_denied"], 0)

        service._on_upstream_event(_private_event("hi", user_id=8, message_id=9), "forward_ws")
        self.assertEqual(service.pending_count, 1, "名单外的人不该再有新轮次")
        self.assertEqual(service.stats["messages_denied"], 1)

    def test_empty_user_list_means_no_restriction(self) -> None:
        service = self._service(allow={"users": []})
        service._on_upstream_event(_private_event("hi", user_id=12345), "forward_ws")
        self.assertEqual(service.pending_count, 1)

    # ── 群名单 ────────────────────────────────────────────────────────
    def test_group_id_list_blocks_and_allows(self) -> None:
        service = self._service(allow={"group_ids": [888]})
        service._on_upstream_event(_group_event("hi", at=True, group_id=777), "forward_ws")
        self.assertEqual(service.pending_count, 0, "名单外的群应当被拒")
        self.assertEqual(service.stats["messages_denied"], 1)

        service._on_upstream_event(_group_event("hi", at=True, group_id=888), "forward_ws")
        self.assertEqual(service.pending_count, 1, "名单内的群应当放行")

    # ── 会话类型开关 ──────────────────────────────────────────────────
    def test_private_can_be_disabled(self) -> None:
        service = self._service(allow={"private": False})
        service._on_upstream_event(_private_event("hi"), "forward_ws")
        self.assertEqual(service.pending_count, 0)
        self.assertEqual(service.stats["messages_denied"], 1)

    def test_groups_can_be_disabled_even_when_at(self) -> None:
        service = self._service(allow={"groups": False})
        service._on_upstream_event(_group_event("hi", at=True), "forward_ws")
        self.assertEqual(service.pending_count, 0)
        self.assertEqual(service.stats["messages_denied"], 1)

    # ── require_at ────────────────────────────────────────────────────
    def test_require_at_false_lets_unmentioned_group_message_through(self) -> None:
        service = self._service(allow={"require_at": False})
        service._on_upstream_event(_group_event("hi", at=False), "forward_ws")
        self.assertEqual(service.pending_count, 1)

    # ── 与 hub 广播的关系（文档承诺的顺序） ────────────────────────────
    def test_allow_list_does_not_stop_hub_publish(self) -> None:
        """`_publish` 排在 `_allowed` 之前（§6.1）：名单只拦「桌宠自己回复」。

        这条钉住的是**刻意的设计**——hub 消费者应当看到完整上下文。
        谁要是把顺序调过来，这里会立刻红。

        判据用同步计数：`events_published` 在 `_publish()` 里、spawn 之前就自增，
        不依赖事件循环何时真的把帧写出去。
        """
        service = self._service(
            reply_mode="both",
            allow={"users": [7], "private": False, "groups": False, "require_at": True},
            hub={"enabled": True, "publish_events": True},
        )
        service.hub_sessions.append(_NullHubSession())

        # 私聊被名单拒、群聊被开关拒、未 @ 被 require_at 拒 —— 三种都不该入队
        service._on_upstream_event(_private_event("hi", user_id=8), "forward_ws")
        service._on_upstream_event(_private_event("hi", user_id=9, message_id=8), "forward_ws")
        service._on_upstream_event(_group_event("hi", at=False), "forward_ws")

        self.assertEqual(service.pending_count, 0, "名单/开关应当拦住入队")
        self.assertEqual(service.stats["events_published"], 3,
                         "被准入规则拒掉的消息仍然必须广播给 hub")

    def test_allow_list_in_hub_only_mode_is_never_evaluated(self) -> None:
        """`reply_mode=hub` 时 `_allowed()` 一次都不会执行（§6.1 的准入后果）。"""
        service = self._service(
            reply_mode="hub",
            allow={"users": [7], "private": False, "groups": False, "require_at": True},
            hub={"enabled": True, "publish_events": True},
        )
        service.hub_sessions.append(_NullHubSession())

        service._on_upstream_event(_private_event("hi", user_id=8), "forward_ws")
        service._on_upstream_event(_group_event("hi", at=False), "forward_ws")

        self.assertEqual(service.stats["events_published"], 2)
        # 关键：没有任何"被拒"计数——那条路根本没走过滤
        self.assertEqual(service.stats["messages_denied"], 0)
        self.assertEqual(service.pending_count, 0)


class PublishDiagnosticsTests(unittest.TestCase):
    """`_publish` 的两条诊断日志。

    为什么值得钉住：`reply_mode=hub` 时桌宠**不处理**消息（只是转发给 hub 消费者），
    所以成功的一轮在桌宠侧**不会**留下"我处理了一条消息"的日志。而 `_publish` 在
    **没有任何消费者**时原先直接 `return` —— 消息静默消失、两边都不报错。
    最后那条端到端验证（真实 QQ 消息）要能回答"卡在哪一段"，就只能靠这两条线。

    同时钉住**不记正文**这条口径（与探针 `--show-text` 一致）。
    """

    def _service(self) -> OneBotService:
        return OneBotService({
            "enabled": True,
            "reply_mode": "hub",
            "hub": {"enabled": True, "publish_events": True},
        })

    def test_no_consumer_is_a_loud_warning_with_metadata_only(self) -> None:
        service = self._service()
        with self.assertLogs("onebot", level="WARNING") as captured:
            service._on_upstream_event(
                _private_event("这句正文不该出现在日志里"), "forward_ws"
            )
        text = "\n".join(captured.output)
        self.assertIn("没有任何 hub 消费者", text)
        self.assertIn("private:", text)                    # 会话键足以定位
        self.assertNotIn("不该出现在日志里", text)          # 正文绝不入日志
        self.assertEqual(service.stats["events_published"], 0)

    def test_forwarding_is_logged_with_consumer_count(self) -> None:
        service = self._service()
        service.hub_sessions.append(_NullHubSession())
        with self.assertLogs("onebot", level="INFO") as captured:
            service._on_upstream_event(_private_event("正文也不该进日志"), "forward_ws")
        text = "\n".join(captured.output)
        self.assertIn("已转发事件", text)
        self.assertIn("hub 消费者 1 个", text)
        self.assertNotIn("正文也不该进日志", text)
        self.assertEqual(service.stats["events_published"], 1)

    def test_non_message_event_is_summarised_without_pretending_to_have_a_key(self) -> None:
        service = self._service()
        with self.assertLogs("onebot", level="WARNING") as captured:
            service._on_upstream_event(
                {"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 123456789},
                "forward_ws",
            )
        text = "\n".join(captured.output)
        self.assertIn("非消息事件", text)
        # 必须写清是**哪一类**非消息事件：实测踩过一次——用户私聊没反应，日志里
        # 只有一排 `（非消息事件）`，分不出心跳与好友请求，查了很久。
        self.assertIn("meta_event/heartbeat", text)

    def test_friend_request_is_visible_in_the_log(self) -> None:
        """好友请求这类 `request` 事件同样会被转发——日志必须能看出是它。"""
        service = self._service()
        with self.assertLogs("onebot", level="WARNING") as captured:
            service._on_upstream_event(
                {
                    "post_type": "request",
                    "request_type": "friend",
                    "user_id": 10001,
                    "comment": "你好",
                    "flag": "abc",
                    "self_id": 123456789,
                },
                "forward_ws",
            )
        self.assertIn("request/friend", "\n".join(captured.output))


class PreParseVisibilityTests(unittest.TestCase):
    """**解析之前**就要留痕——否则"没上报"和"上报了没解析出来"无法区分。

    实测动机：用户发消息没反应，日志里只有一排心跳，因为转发日志发生在解析成功之后。
    现在：message 事件在解析前就打一条 INFO（只记结构与 id，不记正文）；
    解析失败时再补一条 WARNING。
    """

    def _service(self) -> OneBotService:
        return OneBotService({
            "enabled": True,
            "reply_mode": "hub",
            "hub": {"enabled": True, "publish_events": True},
        })

    def test_message_event_is_visible_before_parsing_without_leaking_text(self) -> None:
        service = self._service()
        with self.assertLogs("onebot", level="INFO") as captured:
            service._on_upstream_event(_private_event("这句正文不该进日志"), "forward_ws")
        text = "\n".join(captured.output)
        self.assertIn("收到 message 事件（解析前）", text)
        self.assertIn("post_type=message", text)
        self.assertIn("message_type=private", text)
        self.assertIn("message=str(", text)            # 结构可见（`_private_event` 是字符串形态）
        self.assertNotIn("不该进日志", text)            # 正文绝不入日志

    def test_parse_failure_is_warned_about_instead_of_being_silent(self) -> None:
        service = self._service()
        payload = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "message": [{"type": "text", "data": {"text": "秘密正文"}}],
        }
        with mock.patch.object(events, "parse_event", return_value=None):
            with self.assertLogs("onebot", level="WARNING") as captured:
                service._on_upstream_event(payload, "forward_ws")
        text = "\n".join(captured.output)
        self.assertIn("事件解析失败", text)
        self.assertIn("message=[text]", text)
        self.assertNotIn("秘密正文", text)


if __name__ == "__main__":
    unittest.main()
