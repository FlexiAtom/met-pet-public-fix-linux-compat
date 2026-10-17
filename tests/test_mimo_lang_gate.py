"""MiMo 云端那一路在 ``voice_lang=auto`` 下会不会每句都被静默驳回（不出声）。

人工 2026-10-15 00:44「我没MiMo的没法测，需要假服务来测」——本文件用一台只绑
127.0.0.1 的假 MiMo 供应商把这一格量出来。判据是**假服务器收到几个请求**，
不是日志措辞：`voice_lang=auto` 那一档在发请求之前就被选路驳回（`service.py:981`，
配置的 target `'auto'` 不在 `supported_languages()` 里），
所以"请求数 0"与"请求数 1"分得开"被静默"和"真去合成了"。
最后一支锁机制：驳回发生在那一处 target 成员判定，不是 `language_policy.py:166`
那句 `observed != target`——纯中文的 `observed` 是 `unknown`，那道比较根本不触发。
"""
from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import threading
import unittest
import wave
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from meapet.tts.service import MeaTTS

ZH_SENTENCE = "今天天气不错，一起去晒晒太阳。"


def _tiny_wav_b64() -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\x00\x00" * 240)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class _FakeMiMoHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 的命名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        messages = payload.get("messages") or [{}]
        self.server.requests.append(  # type: ignore[attr-defined]
            {
                "path": self.path,
                "model": payload.get("model", ""),
                "text": (messages[-1] or {}).get("content", ""),
            }
        )
        body = json.dumps(
            {
                "choices": [
                    {"message": {"content": "", "audio": {"data": _tiny_wav_b64()}}}
                ]
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


class FakeMiMoServer:
    """只绑回路的一台假 MiMo；`requests` 是本文件唯一的判据来源。"""

    def __init__(self) -> None:
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "FakeMiMoServer":
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeMiMoHandler)
        self.server.requests = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]  # type: ignore[union-attr]
        return f"http://{host}:{port}/v1"

    @property
    def requests(self) -> list:
        return self.server.requests  # type: ignore[union-attr]

    def __exit__(self, *exc) -> bool:
        self.server.shutdown()  # type: ignore[union-attr]
        self.server.server_close()  # type: ignore[union-attr]
        self.thread.join(timeout=5)  # type: ignore[union-attr]
        return False


class MiMoLanguageGateTest(unittest.TestCase):
    def _make_tts(self, server: FakeMiMoServer, voice_lang: str, out_dir: str) -> MeaTTS:
        cfg = {
            "tts": {
                "engine": "mimo",
                "enabled": True,
                "api_base": server.base_url,
                "api_key": "loopback-fake-key",
                "voice_lang": voice_lang,
                "translate_to_jp": False,
                "timeout": 5,
            }
        }
        tts = MeaTTS(cfg)
        tts.output_dir = out_dir
        return tts

    def test_target_matching_text_reaches_provider(self):
        """对照臂：voice_lang=zh 念中文，闸门放行、假服务器收到一发。"""
        with FakeMiMoServer() as server:
            with tempfile.TemporaryDirectory() as td:
                tts = self._make_tts(server, "zh", td)
                result = tts.speak(ZH_SENTENCE)
                self.assertTrue(
                    result and os.path.isfile(result[0]),
                    f"zh 档本该出声，实际 result={result} requests={server.requests}",
                )
                self.assertEqual(len(server.requests), 1)
                self.assertEqual(server.requests[0]["text"], ZH_SENTENCE)

    def test_auto_target_skips_before_the_network(self):
        """本件那一格：voice_lang=auto 时云端那一档每句都在发请求之前被驳回。
        实测理由码 configured_target_unsupported 出自选路（target 'auto' ∉ supported），
        不是"文本语言与 target 不相等"那一道——见最后一支的机制对照。"""
        with FakeMiMoServer() as server:
            with tempfile.TemporaryDirectory() as td:
                tts = self._make_tts(server, "auto", td)
                with mock.patch("meapet.tts.service.log.warning") as warn:
                    result = tts.speak(ZH_SENTENCE)
                self.assertFalse(
                    result and os.path.isfile(result[0]),
                    f"auto 档本该静默，实际 result={result}",
                )
                self.assertEqual(
                    server.requests,
                    [],
                    "auto 档居然发出去了 ⇒ 选路已不再按 target 成员判定驳回，本件该改判",
                )
                # 那条警告得报出**是哪个配置值**——只写 reason 的话，手输 "auto" 的人
                # 看不出该改哪个键（人工 2026-10-15 那格的处置就落在这里）。
                warned = " ".join(str(a) for c in warn.call_args_list for a in c.args)
                self.assertIn("auto", warned)
                self.assertIn("configured_target_unsupported", warned)

    def test_prefer_source_language_does_not_rescue_auto(self):
        """「念原文」那一档（互动链用）能不能在云端 + auto 下救回来。"""
        with FakeMiMoServer() as server:
            with tempfile.TemporaryDirectory() as td:
                tts = self._make_tts(server, "auto", td)
                result = tts.speak(ZH_SENTENCE, prefer_source_language=True)
                reached = bool(result and os.path.isfile(result[0]))
                self.assertEqual(
                    len(server.requests),
                    1 if reached else 0,
                    f"请求数与是否出声不自洽：result={result} requests={server.requests}",
                )
                # 结论交给读数，不在断言里预设方向：
                print(f"[auto+念原文] 到达供应商={reached} requests={len(server.requests)}")

    def test_the_skip_is_route_target_check_not_the_predicate(self):
        """把机制钉住：`auto` 那一路的驳回来自**选路**里的 target 成员判定，与文本那门
        语言无关；而 `language_policy` 那句 `observed != target` 对纯中文根本不触发
        （纯中文的 `observed` 是 `unknown`，保守放行）——池件原先指认的是后一道。"""
        from meapet.tts.language_policy import (
            detect_script_language,
            should_skip_tts_due_to_language_mismatch,
        )

        cfg = {
            "tts": {
                "engine": "mimo",
                "enabled": True,
                "api_base": "http://127.0.0.1:1/v1",  # 这一支只算选路，不发请求
                "api_key": "loopback-fake-key",
                "voice_lang": "auto",
                "translate_to_jp": False,
                "timeout": 5,
            }
        }
        tts = MeaTTS(cfg)
        self.assertNotIn("auto", tts.supported_languages())
        self.assertEqual(detect_script_language(ZH_SENTENCE), "unknown")
        self.assertFalse(should_skip_tts_due_to_language_mismatch(ZH_SENTENCE, "auto"))
        for text in (
            ZH_SENTENCE,
            "The weather is nice today.",
            "今日はいい天気ですね。",
            "mixed 中文 and English",
        ):
            action, _claimed, _target, reason = tts._select_tts_text_route(text, "auto")
            self.assertEqual(
                (action, reason),
                ("skip", "configured_target_unsupported"),
                f"这段文本的驳回理由换了：{text}",
            )


if __name__ == "__main__":
    unittest.main()
