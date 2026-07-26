"""视觉路由必须显式区分继承、独立中转与关闭。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestVisionRoutePolicy(unittest.TestCase):
    def test_inherit_requires_a_known_or_explicit_main_model_capability(self):
        from meapet.vision.policy import resolve_vision_route

        mimo = resolve_vision_route(
            {"mode": "inherit"},
            {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                    "api_base": "https://api.xiaomimimo.com/v1",
                    "model": "mimo-v2.5",
                },
            },
        )
        custom = resolve_vision_route(
            {"mode": "inherit"},
            {
                "mode": "direct",
                "direct": {
                    "provider": "custom",
                    "protocol": "openai_chat",
                    "api_base": "https://private.example/v1",
                    "model": "private-model",
                },
            },
        )
        explicit = resolve_vision_route(
            {"mode": "inherit", "main_model_supports_images": True},
            {
                "mode": "direct",
                "direct": {"provider": "custom", "model": "private-model"},
            },
        )

        self.assertTrue(mimo.available)
        self.assertFalse(custom.available)
        self.assertEqual(custom.reason, "main_model_vision_not_confirmed")
        self.assertTrue(explicit.available)

    def test_agent_never_uses_a_separate_relay_model(self):
        from meapet.vision.policy import resolve_vision_route

        relay = resolve_vision_route(
            {"mode": "relay", "backend": "mimo"},
            {"mode": "agent", "agent": {"kind": "openclaw"}},
        )
        inherit_unknown = resolve_vision_route(
            {"mode": "inherit"},
            {"mode": "agent", "agent": {"kind": "openclaw"}},
        )
        inherit_confirmed = resolve_vision_route(
            {"mode": "inherit", "main_model_supports_images": True},
            {"mode": "agent", "agent": {"kind": "openclaw"}},
        )

        self.assertFalse(relay.available)
        self.assertEqual(relay.reason, "agent_relay_forbidden")
        self.assertFalse(inherit_unknown.available)
        self.assertTrue(inherit_confirmed.available)

    def test_disabled_is_always_available_and_does_not_probe_models(self):
        from meapet.vision.policy import resolve_vision_route

        route = resolve_vision_route(
            {"mode": "disabled"},
            {"mode": "agent", "agent": {}},
        )

        self.assertTrue(route.available)
        self.assertEqual(route.mode, "disabled")


class TestVisionConfigMigration(unittest.TestCase):
    def test_legacy_active_watcher_migrates_to_relay_without_assuming_vision(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "vision": {"backend": "ollama", "enabled": True},
                "watcher": {"enabled": True},
            }
        )

        self.assertEqual(config["vision"]["mode"], "relay")
        self.assertTrue(config["watcher"]["enabled"])

    def test_disabled_mode_forces_watcher_off(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "vision": {"mode": "disabled"},
                "watcher": {"enabled": True},
            }
        )

        self.assertFalse(config["vision"]["enabled"])
        self.assertFalse(config["watcher"]["enabled"])

    def test_explicit_inherit_mode_and_capability_are_preserved(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "vision": {
                    "mode": "inherit",
                    "main_model_supports_images": True,
                }
            }
        )

        self.assertEqual(config["vision"]["mode"], "inherit")
        self.assertTrue(config["vision"]["enabled"])
        self.assertTrue(config["vision"]["main_model_supports_images"])


class TestVisionObservation(unittest.TestCase):
    def test_structured_observation_is_bounded_and_serializable(self):
        from meapet.vision.observation import parse_vision_observation

        observation = parse_vision_observation(
            '{"summary":"正在 VS Code 修改 Python",'
            '"application":"Visual Studio Code",'
            '"activity":"coding",'
            '"notable_text":["test.py","pytest"],'
            '"sensitive":false}'
        )

        self.assertEqual(observation.activity, "coding")
        self.assertEqual(observation.notable_text, ("test.py", "pytest"))
        self.assertIn('"sensitive":false', observation.to_json())

    def test_unstructured_or_empty_output_does_not_become_fake_facts(self):
        from meapet.vision.observation import parse_vision_observation

        fallback = parse_vision_observation("似乎是一个终端窗口")
        empty = parse_vision_observation("")

        self.assertEqual(fallback.summary, "似乎是一个终端窗口")
        self.assertEqual(fallback.activity, "unknown")
        self.assertIsNone(empty)


class TestVisionCoordinator(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _result(text="看到你在写代码了喵"):
        from meapet.conversation.output_protocol import ParseResult
        from meapet.conversation.types import ReplySegment

        return ParseResult(
            (
                ReplySegment(
                    index=0,
                    display_text=text,
                    voice_text=text,
                    voice_language="zh",
                    mood="curious",
                    tts_style="轻声",
                ),
            ),
            (),
            True,
            "meapet",
        )

    async def test_inherit_sends_image_to_main_backend_in_one_turn(self):
        from meapet.agent.base import ImageAttachment, TurnCompleted
        from meapet.vision.coordinator import VisionCoordinator

        class Adapter:
            def __init__(self):
                self.requests = []

            async def stream_turn(self, request):
                self.requests.append(request)
                yield TurnCompleted(request.turn_id, TestVisionCoordinator._result())

        adapter = Adapter()
        coordinator = VisionCoordinator(adapter)
        attachment = ImageAttachment("image/jpeg", "YWJj")

        reply = await coordinator.inherit(
            attachment,
            idle_minutes=12,
            frontend_context={"frontend_capabilities": {}},
            tts_enabled=True,
        )

        self.assertFalse(reply.silent)
        self.assertEqual(len(adapter.requests), 1)
        self.assertEqual(adapter.requests[0].attachments, (attachment,))
        self.assertIn("直接查看本轮附带的截图", adapter.requests[0].user_text)

    async def test_relay_passes_only_structured_observation_to_main_backend(self):
        from meapet.agent.base import TurnCompleted
        from meapet.vision.coordinator import VisionCoordinator
        from meapet.vision.observation import parse_vision_observation

        class Adapter:
            def __init__(self):
                self.requests = []

            async def stream_turn(self, request):
                self.requests.append(request)
                yield TurnCompleted(request.turn_id, TestVisionCoordinator._result())

        adapter = Adapter()
        observation = parse_vision_observation(
            '{"summary":"浏览器打开文档",'
            '"application":"Firefox","activity":"reading",'
            '"notable_text":[],"sensitive":false}'
        )

        reply = await VisionCoordinator(adapter).relay(
            observation,
            idle_minutes=3,
            frontend_context={},
            tts_enabled=False,
        )

        self.assertFalse(reply.silent)
        request = adapter.requests[0]
        self.assertEqual(request.attachments, ())
        self.assertIn('"activity":"reading"', request.user_text)

    async def test_silent_token_is_not_exposed_as_a_bubble(self):
        from meapet.agent.base import ImageAttachment, TurnCompleted
        from meapet.vision.coordinator import SILENT_DISPLAY_TOKEN, VisionCoordinator

        class Adapter:
            async def stream_turn(self, request):
                yield TurnCompleted(
                    request.turn_id,
                    TestVisionCoordinator._result(SILENT_DISPLAY_TOKEN),
                )

        reply = await VisionCoordinator(Adapter()).inherit(
            ImageAttachment("image/jpeg", "YWJj"),
            idle_minutes=0,
            frontend_context={},
            tts_enabled=False,
        )

        self.assertTrue(reply.silent)
        self.assertEqual(reply.segments, ())

    async def test_typed_main_backend_failures_and_invalid_inputs_are_preserved(self):
        from meapet.agent.base import (
            ImageAttachment,
            TurnCancelled,
            TurnFailed,
        )
        from meapet.vision.coordinator import VisionCoordinator, VisionTurnError

        with self.assertRaises(ValueError):
            VisionCoordinator(object())

        class FailedAdapter:
            async def stream_turn(self, request):
                yield TurnFailed(
                    request.turn_id,
                    "rate_limit",
                    "模型请求过于频繁。",
                    True,
                )

        with self.assertRaises(VisionTurnError) as failed:
            await VisionCoordinator(FailedAdapter()).inherit(
                ImageAttachment("image/jpeg", "YWJj"),
                idle_minutes=0,
                frontend_context={},
                tts_enabled=False,
            )
        self.assertEqual(failed.exception.category, "rate_limit")
        self.assertTrue(failed.exception.retryable)

        class CancelledAdapter:
            async def stream_turn(self, request):
                yield TurnCancelled(request.turn_id)

        with self.assertRaises(VisionTurnError) as cancelled:
            await VisionCoordinator(CancelledAdapter()).inherit(
                ImageAttachment("image/jpeg", "YWJj"),
                idle_minutes=0,
                frontend_context={},
                tts_enabled=False,
            )
        self.assertEqual(cancelled.exception.category, "cancelled")

        class EmptyAdapter:
            async def stream_turn(self, _request):
                if False:
                    yield None

        with self.assertRaises(VisionTurnError) as empty:
            await VisionCoordinator(EmptyAdapter()).inherit(
                ImageAttachment("image/jpeg", "YWJj"),
                idle_minutes=0,
                frontend_context={},
                tts_enabled=False,
            )
        self.assertEqual(empty.exception.category, "protocol")

        with self.assertRaises(ValueError):
            await VisionCoordinator(EmptyAdapter()).relay(
                None,
                idle_minutes=0,
                frontend_context={},
                tts_enabled=False,
            )


if __name__ == "__main__":
    unittest.main()
