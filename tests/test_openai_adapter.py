"""Tests for OpenAIAdapter."""

from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from meapet.agent.base import AgentTurnRequest, ImageAttachment
from meapet.agent.openai_adapter import OpenAIAdapter, OpenAIConfig, OpenAICapabilities


class AsyncContextManager:
    """Helper to mock async context managers."""
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        pass


class OpenAIAdapterTests(unittest.IsolatedAsyncioTestCase):

    def _make_adapter(self, **overrides):
        config = OpenAIConfig(
            base_url="http://test.local/v1",
            api_key="test-key",
            model="test-model",
            **overrides,
        )
        adapter = OpenAIAdapter(config)
        return adapter, config

    def _make_request(self, text="hello", attachments=None):
        return AgentTurnRequest(
            turn_id="test-turn",
            user_text=text,
            attachments=attachments or (),
        )

    # ---------- Configuration ----------

    def test_config_from_dict(self):
        cfg = OpenAIConfig.from_dict({
            "base_url": "https://api.custom.com/v1",
            "api_key": "key123",
            "model": "gpt-4",
            "temperature": 0.5,
            "max_tokens": 2048,
            "timeout_seconds": 99,
        })
        self.assertEqual(cfg.base_url, "https://api.custom.com/v1")
        self.assertEqual(cfg.api_key, "key123")
        self.assertEqual(cfg.model, "gpt-4")
        self.assertEqual(cfg.temperature, 0.5)
        self.assertEqual(cfg.max_tokens, 2048)
        self.assertEqual(cfg.timeout_seconds, 99)

    def test_config_defaults(self):
        cfg = OpenAIConfig()
        self.assertEqual(cfg.base_url, "https://api.openai.com/v1")
        self.assertEqual(cfg.api_key, "")
        self.assertEqual(cfg.model, "")
        self.assertEqual(cfg.temperature, 0.7)
        self.assertEqual(cfg.max_tokens, 4096)
        self.assertEqual(cfg.timeout_seconds, 60.0)

    def test_config_from_dict_uses_host_alias(self):
        cfg = OpenAIConfig.from_dict({"host": "https://ollama.local:11434/v1"})
        self.assertEqual(cfg.base_url, "https://ollama.local:11434/v1")

    # ---------- Capabilities ----------

    def test_default_capabilities(self):
        cfg = OpenAIConfig(model="unknown-model")
        caps = OpenAICapabilities.from_config(cfg)
        self.assertTrue(caps.streaming)
        self.assertFalse(caps.vision)
        self.assertFalse(caps.tool_calling)
        self.assertTrue(caps.repair)

    def test_gpt4_vision_capabilities(self):
        cfg = OpenAIConfig(model="gpt-4o")
        caps = OpenAICapabilities.from_config(cfg)
        self.assertTrue(caps.vision)
        self.assertTrue(caps.tool_calling)

    # ---------- Message building ----------

    def test_build_messages_includes_system_and_user(self):
        adapter, _ = self._make_adapter()
        req = self._make_request("Hello world")
        messages = adapter._build_messages(req)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("MEAPET_SEGMENT", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"][0]["text"], "Hello world")

    def test_build_messages_includes_image_attachment(self):
        adapter, _ = self._make_adapter()
        attachment = ImageAttachment(
            media_type="image/jpeg",
            data="YWJj",
            file_name="test.jpg",
        )
        req = self._make_request("See image", attachments=(attachment,))
        messages = adapter._build_messages(req)
        last_content = messages[-1]["content"]
        self.assertEqual(len(last_content), 2)
        self.assertEqual(last_content[0]["type"], "text")
        self.assertEqual(last_content[1]["type"], "image_url")
        self.assertIn("data:image/jpeg;base64,", last_content[1]["image_url"]["url"])

    def test_build_messages_includes_history(self):
        adapter, _ = self._make_adapter()
        history = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        req = AgentTurnRequest(
            turn_id="hist-turn",
            user_text="again",
            history=tuple(history),
        )
        messages = adapter._build_messages(req)
        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "hi")
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "hello")

    # ---------- Streaming ----------

    async def test_chat_stream_successful_response(self):
        adapter, _ = self._make_adapter()

        async def _lines():
            yield 'data: {"choices":[{"delta":{"content":"你好"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"，世界"}}]}'
            yield "data: [DONE]"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = _lines

        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(type(results[0]).__name__, "TurnCompleted")

    async def test_chat_stream_empty_response_yields_failure(self):
        adapter, _ = self._make_adapter()

        async def _lines():
            yield 'data: {"choices":[{}]}'
            yield "data: [DONE]"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = _lines

        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(type(results[0]).__name__, "TurnFailed")
        self.assertEqual(results[0].category, "empty_response")

    async def test_chat_stream_api_error_yields_failure(self):
        adapter, _ = self._make_adapter()

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.aread = AsyncMock(return_value=b'{"error":"unauthorized"}')

        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(type(results[0]).__name__, "TurnFailed")
        self.assertEqual(results[0].category, "api_error")

    async def test_chat_stream_network_error_yields_failure(self):
        import httpx
        adapter, _ = self._make_adapter()

        adapter._client.stream = MagicMock(
            side_effect=httpx.ConnectError("DNS failed")
        )

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(type(results[0]).__name__, "TurnFailed")
        self.assertEqual(results[0].category, "network_error")

    async def test_chat_stream_cancellation(self):
        adapter, _ = self._make_adapter()

        cancel_event = threading.Event()
        cancel_event.set()

        async def _lines():
            yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = _lines

        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        results = []
        async for result in adapter.chat_stream(req, cancel_event=cancel_event):
            results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(type(results[0]).__name__, "TurnCancelled")

    async def test_chat_stream_format_repair_triggered(self):
        adapter, _ = self._make_adapter()

        with patch("meapet.agent.openai_adapter.parse_reply_output", return_value=None):
            async def _lines():
                yield 'data: {"choices":[{"delta":{"content":"any text"}}]}'
                yield "data: [DONE]"

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.aiter_lines = _lines

            adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

            repair_called = False

            def on_repair(req):
                nonlocal repair_called
                repair_called = True

            req = self._make_request()
            results = []
            async for result in adapter.chat_stream(req, on_format_repair=on_repair):
                results.append(result)

        self.assertEqual(len(results), 1)
        self.assertEqual(type(results[0]).__name__, "TurnFailed")
        self.assertEqual(results[0].category, "format_error")
        self.assertTrue(repair_called)

    # ---------- Repair ----------
    # repair_format 内部调用 await self._client.post(url, json=payload)，
    # 然后用 resp.json()（同步方法）获取数据。
    # 因此 mock_response.json 必须是同步返回字典的可调用对象。

    async def test_repair_format_success(self):
        adapter, _ = self._make_adapter()

        mock_response = MagicMock()
        mock_response.status_code = 200
        # 注意：resp.json() 是同步方法，所以用 MagicMock 而非 AsyncMock
        mock_response.json = MagicMock(return_value={
            "choices": [{"message": {"content": "repaired content"}}]
        })

        adapter._client.post = AsyncMock(return_value=mock_response)

        result = await adapter.repair_format("bad content")
        self.assertEqual(result, "repaired content")

    async def test_repair_format_failure(self):
        adapter, _ = self._make_adapter()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json = MagicMock(return_value={})

        adapter._client.post = AsyncMock(return_value=mock_response)

        result = await adapter.repair_format("bad content")
        self.assertIsNone(result)

    # ---------- Close ----------

    async def test_close_calls_client_aclose(self):
        adapter, _ = self._make_adapter()
        adapter._client.aclose = AsyncMock()
        await adapter.close()
        adapter._client.aclose.assert_awaited_once()

    # ---------- stream_turn 分段事件契约（回归守卫：Agent 模式对话链路） ----------

    _SEGMENT_OUTPUT = (
        "<MEAPET_SEGMENT>\n"
        "<DISPLAY>你好呀</DISPLAY>\n"
        '<META>{"voice_text":"你好呀","voice_language":"zh-CN",'
        '"mood":"happy","tts_style":""}</META>\n'
        "</MEAPET_SEGMENT>\n"
        "<MEAPET_DONE />"
    )

    async def test_stream_turn_emits_segment_and_completed(self):
        """stream_turn 必须产出 SegmentCompleted + TurnCompleted，
        否则 AgentTurnPresentation 不显示气泡、TTS 死锁。"""
        adapter, _ = self._make_adapter()

        async def _lines():
            for piece in (self._SEGMENT_OUTPUT[:20], self._SEGMENT_OUTPUT[20:]):
                yield 'data: {"choices":[{"delta":{"content":%s}}]}' % _json_str(piece)
            yield "data: [DONE]"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = _lines
        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        names = []
        completed = None
        async for event in adapter.stream_turn(req):
            names.append(type(event).__name__)
            if type(event).__name__ == "TurnCompleted":
                completed = event

        self.assertNotIn("TurnFailed", names)
        self.assertIn("SegmentCompleted", names)
        self.assertEqual(names[-1], "TurnCompleted")
        self.assertIsNotNone(completed)
        self.assertEqual(completed.result.segments[0].display_text.strip(), "你好呀")

    async def test_stream_turn_cancel_before_start(self):
        adapter, _ = self._make_adapter()
        await adapter.cancel_turn("test-turn")
        req = self._make_request()
        events = [e async for e in adapter.stream_turn(req)]
        self.assertEqual(len(events), 1)
        self.assertEqual(type(events[0]).__name__, "TurnCancelled")

    async def test_stream_turn_api_error_is_reported(self):
        adapter, _ = self._make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.aread = AsyncMock(return_value=b'{"error":"unauthorized"}')
        adapter._client.stream = MagicMock(return_value=AsyncContextManager(mock_response))

        req = self._make_request()
        events = [e async for e in adapter.stream_turn(req)]
        self.assertEqual(len(events), 1)
        self.assertEqual(type(events[0]).__name__, "TurnFailed")
        self.assertEqual(events[0].category, "api_error")

    async def test_probe_returns_true_on_models_ok(self):
        adapter, _ = self._make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 200
        adapter._client.get = AsyncMock(return_value=mock_response)
        self.assertTrue(await adapter.probe())

    async def test_probe_raises_on_auth_failure(self):
        adapter, _ = self._make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 401
        adapter._client.get = AsyncMock(return_value=mock_response)
        with self.assertRaises(RuntimeError):
            await adapter.probe()


def _json_str(text: str) -> str:
    import json
    return json.dumps(text, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()

