"""httpx 真异步 HTTP 层"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestHttpAsyncPost(unittest.TestCase):
    def test_post_json_uses_httpx_client(self):
        import httpx
        from meapet.async_runtime import run
        from meapet import http_async

        class FakeResp:
            status_code = 200
            text = '{"ok":true}'
            def json(self):
                return {"ok": True}

        class FakeClient:
            is_closed = False
            def __init__(self):
                self.calls = []
            async def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return FakeResp()
            async def aclose(self):
                self.is_closed = True

        fake = FakeClient()

        async def _fake_get_client():
            return fake

        async def _scenario():
            with mock.patch.object(http_async, "get_client", _fake_get_client):
                resp = await http_async.post_json(
                    "https://example.com/v1/chat",
                    headers={"Authorization": "Bearer x"},
                    json={"a": 1},
                    timeout=5,
                )
            return resp.status_code, fake.calls

        code, calls = run(_scenario(), timeout=5)
        self.assertEqual(code, 200)
        self.assertEqual(len(calls), 1)
        self.assertIn("example.com", calls[0][0])

    def test_chat_async_uses_http_async(self):
        """ChatEngine 统一使用 OpenAI 兼容接口，无 backend 参数。"""
        from meapet.async_runtime import run
        from meapet.chat.engine import ChatEngine

        eng = ChatEngine(api_key="k", api_base="https://api.example.com", model="m")
        eng.available = True

        class Resp:
            status_code = 200
            text = "ok"
            def json(self):
                return {"choices": [{"message": {"content": "[happy]httpx喵"}}]}

        async def fake_post(url, headers=None, json_body=None, timeout=30):
            return Resp()

        with mock.patch.object(eng, "_post_json", side_effect=fake_post):
            reply, mood = run(eng.quick_chat_async("hi"), timeout=5)
        self.assertEqual(mood, "happy")
        self.assertEqual(reply, "httpx喵")

    def test_chat_reserves_tokens_for_tts_metadata_line(self):
        """统一 OpenAI 请求体应包含 max_tokens 字段。"""
        import asyncio

        from meapet.chat.engine import ChatEngine

        captured = {}

        class FakeResp:
            status_code = 200
            text = "ok"

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "两行加元数据"}}]}

        async def fake_post(_url, *, json_body=None, **_kwargs):
            captured["max_tokens"] = json_body.get("max_tokens")
            captured["model"] = json_body.get("model")
            captured["has_stream"] = json_body.get("stream", False)
            return FakeResp()

        eng = ChatEngine(
            api_key="k",
            api_base="https://api.example.com",
            model="m",
            max_tokens=4000,
        )
        eng._post_json = fake_post

        asyncio.run(eng._chat_openai_async([]))

        # max_tokens 应被设置（>= 320 是 TTS 元数据预留后的合理值）
        self.assertGreaterEqual(captured.get("max_tokens", 0), 320)
        self.assertEqual(captured.get("model"), "m")
        # 非流式请求不应有 stream=True
        self.assertFalse(captured.get("has_stream", True))


if __name__ == "__main__":
    unittest.main()

