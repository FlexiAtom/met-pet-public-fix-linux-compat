"""共享对话契约：多段回复、流式解析、能力摘要与配置迁移（OpenAI 兼容版）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


VALID_MULTI_SEGMENT = """<MEAPET_SEGMENT>
<DISPLAY>第一段，主人。</DISPLAY>
<META>{"voice_text":"第一段，主人。","voice_language":"zh-CN","mood":"happy","tts_style":"轻声"}</META>
</MEAPET_SEGMENT>
<MEAPET_SEGMENT>
<DISPLAY>おかえりにゃ</DISPLAY>
<META>{"voice_text":"おかえりにゃ","voice_language":"jp","mood":"neutral","tts_style":""}</META>
</MEAPET_SEGMENT>
<MEAPET_DONE />"""


class TestReplyOutputContract(unittest.TestCase):
    def test_parses_multiple_segments_and_normalizes_voice_languages(self):
        from meapet.conversation.output_protocol import parse_reply_output

        result = parse_reply_output(VALID_MULTI_SEGMENT)

        self.assertTrue(result.done)
        self.assertEqual(result.source_format, "meapet")
        self.assertEqual(len(result.segments), 2)
        self.assertEqual(result.segments[0].display_text, "第一段，主人。")
        self.assertEqual(result.segments[0].voice_text, "第一段，主人。")
        self.assertEqual(result.segments[0].voice_language, "zh-CN")
        self.assertEqual(result.segments[0].mood, "happy")
        self.assertEqual(result.segments[0].tts_style, "轻声")
        self.assertEqual(result.segments[1].voice_language, "ja")
        self.assertEqual(result.segments[1].tts_style, "")
        self.assertEqual(result.segments[1].missing_required_fields, ())
        self.assertFalse(result.requires_repair(tts_enabled=True))

    def test_streaming_deltas_survive_single_character_chunks(self):
        from meapet.conversation.output_protocol import (
            MeaPetOutputStreamParser,
            ProtocolCompleted,
            SegmentCompleted,
            SegmentStarted,
            SegmentTextDelta,
        )

        parser = MeaPetOutputStreamParser()
        events = []
        for character in VALID_MULTI_SEGMENT:
            events.extend(parser.feed(character))
        result = parser.close(tts_enabled=False)

        started = [event for event in events if isinstance(event, SegmentStarted)]
        completed = [event for event in events if isinstance(event, SegmentCompleted)]
        protocol_done = [event for event in events if isinstance(event, ProtocolCompleted)]
        deltas = [event for event in events if isinstance(event, SegmentTextDelta)]

        self.assertEqual([event.index for event in started], [0, 1])
        self.assertEqual([event.segment.index for event in completed], [0, 1])
        self.assertEqual(len(protocol_done), 1)
        self.assertEqual(
            "".join(event.delta for event in deltas if event.index == 0),
            "第一段，主人。",
        )
        self.assertEqual(
            "".join(event.delta for event in deltas if event.index == 1),
            "おかえりにゃ",
        )
        self.assertFalse(any("MEAPET" in event.delta for event in deltas))
        self.assertEqual(result.segments[1].voice_language, "ja")

    def test_missing_done_is_recoverable_without_format_repair(self):
        from meapet.conversation.output_protocol import parse_reply_output

        source = VALID_MULTI_SEGMENT.replace("\n<MEAPET_DONE />", "")
        result = parse_reply_output(source)

        self.assertFalse(result.done)
        self.assertEqual(len(result.segments), 2)
        self.assertIn("missing_done", {issue.code for issue in result.issues})
        self.assertFalse(result.requires_repair(tts_enabled=True))

    def test_voice_language_is_required_when_tts_is_enabled(self):
        from meapet.conversation.output_protocol import parse_reply_output

        source = """<MEAPET_SEGMENT>
<DISPLAY>能显示的文字</DISPLAY>
<META>{"voice_text":"能朗读的文字","mood":"neutral","tts_style":"自然"}</META>
</MEAPET_SEGMENT>
<MEAPET_DONE />"""
        result = parse_reply_output(source)

        self.assertEqual(result.segments[0].missing_required_fields, ("voice_language",))
        self.assertTrue(result.segments[0].missing_required_fields)
        self.assertTrue(result.requires_repair(tts_enabled=True))
        self.assertFalse(result.requires_repair(tts_enabled=False))

    def test_two_invalid_required_fields_trigger_repair_even_without_tts(self):
        from meapet.conversation.output_protocol import parse_reply_output

        source = """<MEAPET_SEGMENT>
<DISPLAY>先保住文字</DISPLAY>
<META>{"voice_text":"","voice_language":"","mood":"neutral","tts_style":""}</META>
</MEAPET_SEGMENT>
<MEAPET_DONE />"""
        result = parse_reply_output(source)

        self.assertEqual(
            result.segments[0].missing_required_fields,
            ("voice_language", "voice_text"),
        )
        self.assertTrue(result.requires_repair(tts_enabled=False))

    def test_present_but_empty_tts_style_is_valid(self):
        from meapet.conversation.output_protocol import parse_reply_output

        result = parse_reply_output(VALID_MULTI_SEGMENT)

        second = result.segments[1]
        self.assertEqual(second.tts_style, "")
        self.assertNotIn("tts_style", second.missing_required_fields)

    def test_legacy_three_line_reply_remains_usable(self):
        from meapet.conversation.output_protocol import parse_reply_output

        source = """[happy]主人回来啦
おかえり、主人にゃ
<TTS>{"emotion":"happy","pace":"normal","energy":"medium","volume":"soft","delivery":"句尾收轻"}</TTS>"""
        result = parse_reply_output(source)

        self.assertEqual(result.source_format, "legacy")
        self.assertEqual(len(result.segments), 1)
        segment = result.segments[0]
        self.assertEqual(segment.display_text, "主人回来啦")
        self.assertEqual(segment.voice_text, "おかえり、主人にゃ")
        self.assertEqual(segment.voice_language, "ja")
        self.assertEqual(segment.mood, "happy")
        self.assertIn("句尾收轻", segment.tts_style)
        self.assertFalse(result.requires_repair(tts_enabled=True))

    def test_plain_text_is_kept_as_best_effort_display(self):
        from meapet.conversation.output_protocol import parse_reply_output

        result = parse_reply_output("至少不要吞掉这句话")

        self.assertEqual(result.source_format, "plain")
        self.assertEqual(result.segments[0].display_text, "至少不要吞掉这句话")
        self.assertTrue(result.requires_repair(tts_enabled=True))
        self.assertIn("missing_metadata", {issue.code for issue in result.issues})


class TestFrontendCapabilitySummary(unittest.TestCase):
    def test_capability_summary_is_normalized_bounded_and_read_only(self):
        from meapet.conversation.capabilities import build_agent_frontend_context
        from meapet.conversation.types import CompanionState, FrontendCapabilities

        capabilities = FrontendCapabilities(
            renderer="live2d",
            supported_moods=("happy", "neutral", "happy", ""),
            supported_motions=("wave", "idle", "wave"),
            tts_enabled=True,
            tts_languages=("jp", "zh-cn", "ja"),
            streaming_text=True,
            multi_segment=True,
        )
        state = CompanionState(
            affection_level="熟悉",
            character_state="idle",
            current_mood="happy",
            busy=True,
        )

        payload = build_agent_frontend_context(capabilities, state)

        self.assertEqual(
            payload["frontend_capabilities"],
            {
                "renderer": "live2d",
                "supported_moods": ["happy", "neutral"],
                "supported_motions": ["wave", "idle"],
                "tts_enabled": True,
                "tts_languages": ["ja", "zh-CN"],
                "streaming_text": True,
                "multi_segment": True,
            },
        )
        self.assertEqual(
            payload["companion_state"],
            {
                "affection_level": "熟悉",
                "character_state": "idle",
                "current_mood": "happy",
                "busy": True,
            },
        )
        self.assertNotIn("memory", repr(payload).lower())
        self.assertNotIn("api_key", repr(payload).lower())


class TestConversationConfigMigration(unittest.TestCase):
    def test_legacy_direct_config_populates_explicit_direct_profile(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config(
            {
                "llm": {
                    "backend": "deepseek",
                    "api_base": "https://deepseek.example/v1",
                    "model": "deepseek-test",
                    "api_key": "$DEEPSEEK_API_KEY",
                    "temperature": 0.4,
                }
            }
        )

        self.assertEqual(cfg["llm"]["mode"], "direct")
        # provider 现在是 "custom"（统一 OpenAI 兼容）
        self.assertEqual(
            cfg["llm"]["direct"],
            {
                "provider": "custom",
                "protocol": "openai_chat",
                "api_base": "https://deepseek.example/v1",
                "host": "",
                "model": "deepseek-test",
                "api_key": "$DEEPSEEK_API_KEY",
                "temperature": 0.4,
                "max_tokens": 4096,
            },
        )
        # direct 段不再有 backend 概念
        self.assertNotIn("backend", cfg["llm"]["direct"])
        # 顶层 backend 与 direct.provider 对齐，消除 deepseek/custom 分叉
        self.assertEqual(cfg["llm"]["backend"], "custom")

    def test_explicit_deepseek_provider_collapses_to_custom(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config(
            {
                "llm": {
                    "mode": "direct",
                    "backend": "deepseek",
                    "direct": {
                        "provider": "deepseek",
                        "protocol": "openai_chat",
                        "api_base": "https://api.deepseek.com",
                        "model": "deepseek-chat",
                    },
                }
            }
        )
        self.assertEqual(cfg["llm"]["direct"]["provider"], "custom")
        self.assertEqual(cfg["llm"]["backend"], "custom")
        self.assertEqual(cfg["llm"]["direct"]["protocol"], "openai_chat")

    def test_ollama_and_mimo_providers_also_collapse_to_custom(self):
        from meapet.config.store import normalize_config

        ollama = normalize_config(
            {
                "llm": {
                    "mode": "direct",
                    "backend": "ollama",
                    "host": "http://127.0.0.1:11434",
                    "model": "qwen",
                }
            }
        )
        self.assertEqual(ollama["llm"]["direct"]["provider"], "custom")
        self.assertEqual(ollama["llm"]["backend"], "custom")
        self.assertEqual(ollama["llm"]["direct"]["protocol"], "ollama_chat")

        mimo = normalize_config(
            {
                "llm": {
                    "mode": "direct",
                    "direct": {
                        "provider": "mimo",
                        "api_base": "https://api.xiaomimimo.com/v1",
                        "model": "mimo-v2",
                    },
                }
            }
        )
        self.assertEqual(mimo["llm"]["direct"]["provider"], "custom")
        self.assertEqual(mimo["llm"]["backend"], "custom")
        self.assertEqual(mimo["llm"]["direct"]["protocol"], "openai_chat")

    def test_custom_deepseek_url_reads_deepseek_env_key(self):
        import os
        from unittest import mock

        from meapet.config.store import resolve_direct_api_key

        llm = {
            "backend": "custom",
            "direct": {
                "provider": "custom",
                "api_base": "https://api.deepseek.com",
                "api_key": "",
            },
        }
        env = {
            "DEEPSEEK_API_KEY": "ds-from-env",
            "OPENAI_API_KEY": "",
            "MEAPET_API_KEY": "",
            "MIMO_API_KEY": "",
            "XIAOMIMIMO_API_KEY": "",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual(resolve_direct_api_key(llm), "ds-from-env")

    def test_legacy_openclaw_config_migrates_to_native_websocket_fields(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config(
            {
                "llm": {
                    "mode": "agent",
                    "agent": {
                        "bridge_url": "ws://192.168.1.8:18789",
                    },
                }
            }
        )

        # 配置应该迁移为 agent 模式
        self.assertEqual(cfg["llm"]["mode"], "agent")
        agent = cfg["llm"]["agent"]
        self.assertEqual(agent["kind"], "openclaw")
        # bridge_url 是旧字段，应被清除
        self.assertNotIn("bridge_url", agent)
        self.assertIn("auth_token", agent)
        self.assertIn("session_id", agent)
        self.assertIn("session_key", agent)
        self.assertEqual(
            agent["base_url"],
            "ws://192.168.1.8:18789",
        )

    def test_legacy_http_model_agent_migrates_to_direct_instead_of_fake_ws(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config(
            {
                "llm": {
                    "mode": "agent",
                    "backend": "openai_compatible",
                    "api_base": "https://models.example.test/v1",
                    "model": "stale-top-level-mirror",
                    "agent": {
                        "api_key": "$MODEL_API_KEY",
                        "model": "example-model",
                        "temperature": 0.25,
                        "max_tokens": 2048,
                    },
                }
            }
        )

        self.assertEqual(cfg["llm"]["mode"], "direct")
        direct = cfg["llm"]["direct"]
        self.assertEqual(
            direct["api_base"],
            "https://models.example.test/v1",
        )
        self.assertEqual(direct["api_key"], "$MODEL_API_KEY")
        self.assertEqual(direct["model"], "example-model")
        self.assertEqual(direct["temperature"], 0.25)
        self.assertEqual(direct["max_tokens"], 2048)
        agent = cfg["llm"]["agent"]
        self.assertEqual(agent["kind"], "hermes")
        self.assertEqual(
            agent["base_url"],
            "ws://127.0.0.1:9119/api/ws",
        )
        self.assertEqual(agent["auth_token"], "")

    def test_legacy_hermes_top_level_token_and_model_are_preserved(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config(
            {
                "llm": {
                    "backend": "hermes",
                    "api_key": "$HERMES_API_SERVER_KEY",
                    "model": "hermes-model-override",
                }
            }
        )

        self.assertEqual(cfg["llm"]["mode"], "agent")
        agent = cfg["llm"]["agent"]
        self.assertEqual(agent["auth_token"], "$HERMES_API_SERVER_KEY")
        self.assertEqual(agent["model"], "hermes-model-override")
        self.assertEqual(
            agent["base_url"],
            "ws://127.0.0.1:9119/api/ws",
        )

    def test_agent_control_defaults_are_closed_and_local(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config({})

        self.assertEqual(
            cfg["agent_control"],
            {
                "enabled": False,
                "listen_host": "127.0.0.1",
                "port": 8765,
                "allowed_agent_ip": "127.0.0.1",
                "auth_token": "",
                "allow_insecure_http": False,
                "cert_file": "",
                "key_file": "",
                "ca_file": "",
            },
        )

    def test_scrub_secrets_removes_agent_and_control_tokens(self):
        from meapet.config.store import scrub_secrets

        scrubbed = scrub_secrets(
            {
                "llm": {
                    "api_key": "legacy-secret",
                    "direct": {"api_key": "direct-secret"},
                    "agent": {
                        "api_key": "agent-key-secret",
                        "auth_token": "agent-token-secret",  # 旧字段兼容
                    },
                },
                "agent_control": {"auth_token": "control-secret"},
            }
        )

        self.assertEqual(scrubbed["llm"]["api_key"], "")
        self.assertEqual(scrubbed["llm"]["direct"]["api_key"], "")
        # 新字段 api_key 被清除
        self.assertEqual(scrubbed["llm"]["agent"]["api_key"], "")
        # 旧字段 auth_token 也兼容清除
        self.assertEqual(scrubbed["llm"]["agent"]["auth_token"], "")
        self.assertEqual(scrubbed["agent_control"]["auth_token"], "")


if __name__ == "__main__":
    unittest.main()
