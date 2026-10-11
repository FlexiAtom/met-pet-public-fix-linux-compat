"""按回复段语言路由 TTS 与固定参考音频。"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class TestReferenceAudioConfig(unittest.TestCase):
    def test_normalizes_language_map_and_migrates_legacy_single_reference(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "tts": {
                    "gsv_ref_wav": " ./legacy-ja.wav ",
                    "gsv_ref_lang": "日语",
                    "reference_audios": {
                        "zh-CN": {"path": " ./refs/zh.wav ", "text": "你好"},
                        "en": " ./refs/en.wav ",
                    },
                }
            }
        )

        refs = config["tts"]["reference_audios"]
        self.assertEqual(refs["zh"], {"path": "./refs/zh.wav", "text": "你好"})
        self.assertEqual(refs["en"], {"path": "./refs/en.wav", "text": ""})
        self.assertEqual(
            refs["jp"],
            {"path": "./legacy-ja.wav", "text": ""},
        )

    def test_normalizes_translation_target_and_supported_languages(self):
        from meapet.config.store import normalize_config

        config = normalize_config(
            {
                "tts": {
                    "translate_to_jp": True,
                    "translate_target_language": "ja-JP",
                    "supported_languages": ["zh-CN", "jp", "zh"],
                }
            }
        )

        self.assertTrue(config["tts"]["translate_to_jp"])
        self.assertEqual(config["tts"]["translate_target_language"], "jp")
        self.assertEqual(config["tts"]["supported_languages"], ["zh", "jp"])


class TestGsvReferenceRouting(unittest.TestCase):
    def test_explicit_segment_language_selects_its_fixed_reference(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            zh = root / "zh.wav"
            jp = root / "jp.wav"
            zh.write_bytes(b"RIFF" + b"\x00" * 64)
            jp.write_bytes(b"RIFF" + b"\x00" * 64)

            class Host(TtsGsvMixin):
                ref_dir = str(root / "automatic")
                voice_lang = "jp"
                gsv_ref_wav = ""
                gsv_ref_lang = "jp"
                reference_audios = {
                    "zh": {"path": str(zh), "text": "中文参考"},
                    "jp": {"path": str(jp), "text": "日本語の参照"},
                }

            host = Host()
            zh_result = host._get_ref_paths("neutral", voice_language="zh-CN")
            jp_result = host._get_ref_paths("neutral", voice_language="ja")

        self.assertEqual(zh_result, (str(zh), "中文参考", "中文"))
        self.assertEqual(jp_result, (str(jp), "日本語の参照", "日文"))

    def test_explicit_segment_language_never_falls_back_to_another_language(self):
        from meapet.tts.engines.gsv import TtsGsvMixin

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            folder = root / "normal"
            folder.mkdir()
            (folder / "jp_normal.wav").write_bytes(b"RIFF" + b"\x00" * 64)
            (folder / "jp_normal.txt").write_text("こんにちは", encoding="utf-8")

            class Host(TtsGsvMixin):
                ref_dir = str(root)
                voice_lang = "jp"
                gsv_ref_wav = ""
                gsv_ref_lang = "jp"
                reference_audios = {}

            result = Host()._get_ref_paths(
                "neutral",
                voice_language="zh-CN",
            )

        self.assertEqual(result, (None, None, None))


class TestTtsLanguagePolicy(unittest.TestCase):
    def test_uses_original_text_when_output_language_is_supported(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "zh-CN",
            supported_languages=("ja", "zh"),
            translation_enabled=True,
            translation_available=True,
            preferred_translation_language="ja",
        )

        self.assertEqual(plan.action, "direct")
        self.assertEqual(plan.requested_language, "zh")
        self.assertEqual(plan.synthesis_language, "zh")
        self.assertFalse(plan.requires_translation)

    def test_translates_only_to_an_available_preferred_language(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "fr-FR",
            supported_languages=("zh-CN", "jp"),
            translation_enabled=True,
            translation_available=True,
            preferred_translation_language="ja-JP",
        )

        self.assertEqual(plan.action, "translate")
        self.assertEqual(plan.requested_language, "fr")
        self.assertEqual(plan.synthesis_language, "jp")
        self.assertTrue(plan.requires_translation)

    def test_skips_voice_when_translation_component_is_unavailable(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "fr",
            supported_languages=("jp",),
            translation_enabled=True,
            translation_available=False,
            preferred_translation_language="jp",
        )

        self.assertEqual(plan.action, "skip")
        self.assertEqual(plan.synthesis_language, "")
        self.assertIn("translation_unavailable", plan.reason)

    def test_uses_first_available_language_if_preference_is_unavailable(self):
        from meapet.tts.language_policy import plan_tts_language

        plan = plan_tts_language(
            "fr",
            supported_languages=("zh-CN", "en-US"),
            translation_enabled=True,
            translation_available=True,
            preferred_translation_language="jp",
        )

        self.assertEqual(plan.action, "translate")
        self.assertEqual(plan.synthesis_language, "zh")


class TestTtsWorkerLanguage(unittest.TestCase):
    def test_worker_forwards_segment_language_without_mutating_global_language(self):
        from meapet.desktop.workers import TTSWorker

        captured = {}

        class TTS:
            voice_lang = "jp"

            async def speak_async(
                self,
                text,
                mood="neutral",
                style="",
                language="",
            ):
                captured.update(
                    text=text,
                    mood=mood,
                    style=style,
                    language=language,
                    global_language=self.voice_lang,
                )
                return ("/tmp/language.wav", "zh")

        worker = TTSWorker(
            TTS(),
            "你好",
            mood="happy",
            style="轻声",
            language="zh-CN",
        )
        result = asyncio.run(worker._run())

        self.assertEqual(result, "/tmp/language.wav|zh")
        self.assertEqual(captured["language"], "zh-CN")
        self.assertEqual(captured["global_language"], "jp")


class TestMeaTtsLanguageOverride(unittest.TestCase):
    def test_mimo_async_uses_per_call_language_instead_of_configured_default(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            with mock.patch.dict(
                os.environ,
                {
                    "MEA_PET_MIMO_API_KEY": "",
                    "XIAOMIMEA_PET_MIMO_API_KEY": "",
                    "MEA_PET_API_KEY": "",
                },
                clear=False,
            ):
                tts = MeaTTS(
                    {
                        "tts": {
                            "enabled": True,
                            "engine": "mimo",
                            "api_key": "test-key-not-real",
                            "voice_lang": "jp",
                            "translate_to_jp": True,
                            # 本用例只验证「逐次 language 覆盖默认 voice_lang」，
                            # 不启用 prefer（prefer 会把中文统一译到 jp，见 TestPrefer*）。
                            "prefer_model_voice_translation": False,
                            "output_dir": td,
                        }
                    }
                )

            with mock.patch.object(
                tts,
                "_speak_mimo_async",
                new_callable=mock.AsyncMock,
                return_value=(str(Path(td) / "zh.wav"), "zh"),
            ) as speak:
                result = asyncio.run(
                    tts.speak_async("你好", language="zh-CN")
                )

        self.assertEqual(result[1], "zh")
        self.assertEqual(speak.await_args.kwargs["lang_tag"], "zh")
        self.assertEqual(speak.await_args.kwargs["voice_language"], "zh")

    def test_unsupported_language_is_translated_before_synthesis(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "test-key-not-real",
                        "voice_lang": "jp",
                        "supported_languages": ["jp"],
                        "translate_to_jp": True,
                        "output_dir": td,
                    }
                }
            )
            tts.translation_service = mock.Mock(available=True)

            with (
                mock.patch.object(
                    tts,
                    "_translate_text_async",
                    new_callable=mock.AsyncMock,
                    return_value="bonjour の翻訳",
                ) as translate,
                mock.patch.object(
                    tts,
                    "_speak_mimo_async",
                    new_callable=mock.AsyncMock,
                    return_value=(str(Path(td) / "jp.wav"), "jp"),
                ) as speak,
            ):
                result = asyncio.run(
                    tts.speak_async("bonjour", language="fr-FR")
                )

        self.assertEqual(result[1], "jp")
        translate.assert_awaited_once_with("bonjour", "fr", "jp")
        self.assertEqual(speak.await_args.args[0], "bonjour の翻訳")
        self.assertEqual(speak.await_args.kwargs["lang_tag"], "jp")

    def test_translation_failure_skips_audio_instead_of_synthesizing_original(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "test-key-not-real",
                        "voice_lang": "jp",
                        "supported_languages": ["jp"],
                        "translate_to_jp": True,
                        "output_dir": td,
                    }
                }
            )
            tts.translation_service = mock.Mock(available=True)

            with (
                mock.patch.object(
                    tts,
                    "_translate_text_async",
                    new_callable=mock.AsyncMock,
                    return_value="",
                ),
                mock.patch.object(
                    tts,
                    "_speak_mimo_async",
                    new_callable=mock.AsyncMock,
                ) as speak,
            ):
                result = asyncio.run(
                    tts.speak_async("bonjour", language="fr")
                )

        self.assertIsNone(result)
        speak.assert_not_awaited()

    def test_unavailable_translation_component_skips_unsupported_language(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "test-key-not-real",
                        "voice_lang": "jp",
                        "supported_languages": ["jp"],
                        "translate_to_jp": True,
                        "output_dir": td,
                    }
                }
            )
            tts.translation_service = mock.Mock(available=False)

            with mock.patch.object(
                tts,
                "_translate_text_async",
                new=mock.AsyncMock(return_value="不应调用"),
            ) as translate, mock.patch.object(
                tts,
                "_speak_mimo_async",
                new_callable=mock.AsyncMock,
            ) as speak:
                result = asyncio.run(
                    tts.speak_async("bonjour", language="fr")
                )

        self.assertIsNone(result)
        translate.assert_not_awaited()
        speak.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class TestVoiceTextLanguageMatch(unittest.TestCase):
    def test_detects_script_languages(self):
        from meapet.tts.language_policy import detect_script_language

        self.assertEqual(detect_script_language("ご主人さま"), "jp")
        self.assertEqual(detect_script_language("主人今天很闲喵？"), "zh")
        self.assertEqual(detect_script_language("Hello there"), "en")
        self.assertEqual(detect_script_language("東京"), "unknown")
        self.assertEqual(detect_script_language("2026"), "unknown")
        self.assertEqual(detect_script_language("OpenAI"), "unknown")
        self.assertEqual(detect_script_language("..."), "unknown")

    def test_voice_text_must_match_claimed_language(self):
        from meapet.tts.language_policy import voice_text_matches_language

        self.assertFalse(
            voice_text_matches_language(
                "主人今天很闲喵？需要我讲解量子力学打发时间吗？",
                "ja-JP",
            )
        )
        self.assertTrue(
            voice_text_matches_language(
                "ご主人、今日は暇なのかにゃ？",
                "ja-JP",
            )
        )
        self.assertTrue(voice_text_matches_language("你好呀", "zh-CN"))
        self.assertFalse(voice_text_matches_language("你好呀", "en"))

    def test_ambiguous_text_is_not_treated_as_a_language_mismatch(self):
        from meapet.tts.language_policy import voice_text_language_relation

        for text in ("東京", "最高！", "2026", "OpenAI"):
            with self.subTest(text=text):
                self.assertEqual(
                    voice_text_language_relation(text, "ja-JP"),
                    "ambiguous",
                )


class TestPreferModelVoiceTranslation(unittest.IsolatedAsyncioTestCase):
    async def test_matched_model_japanese_skips_translation_api(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "x",
                        "voice_lang": "jp",
                        "translate_to_jp": True,
                        "prefer_model_voice_translation": True,
                        "gpt_weights_dir": td,
                        "sovits_weights_dir": td,
                        "ref_dir": td,
                    }
                }
            )
            tts._mimo_mode = True
            tts.translation_service = mock.Mock(available=True)
            with mock.patch.object(tts, "supported_languages", return_value=("jp",)):
                with mock.patch.object(
                    tts,
                    "_translate_text_async",
                    new=mock.AsyncMock(return_value="不该调用"),
                ) as tr:
                    prepared = await tts._prepare_tts_text_async(
                        "ご主人、今日は暇なのかにゃ？",
                        "ja-JP",
                    )
            self.assertEqual(
                prepared,
                ("ご主人、今日は暇なのかにゃ？", "jp"),
            )
            tr.assert_not_awaited()

    async def test_chinese_marked_as_japanese_falls_back_to_api(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "x",
                        "voice_lang": "jp",
                        "translate_to_jp": True,
                        "prefer_model_voice_translation": True,
                        "gpt_weights_dir": td,
                        "sovits_weights_dir": td,
                        "ref_dir": td,
                    }
                }
            )
            tts._mimo_mode = True
            tts.translation_service = mock.Mock(available=True)
            with mock.patch.object(tts, "supported_languages", return_value=("jp",)):
                with mock.patch.object(
                    tts,
                    "_translate_text_async",
                    new=mock.AsyncMock(return_value="こんにちは"),
                ) as tr:
                    prepared = await tts._prepare_tts_text_async(
                        "主人今天很闲喵？",
                        "ja-JP",
                    )
            self.assertEqual(prepared, ("こんにちは", "jp"))
            tr.assert_awaited()
            args = tr.await_args.args
            self.assertEqual(args[0], "主人今天很闲喵？")
            self.assertEqual(args[1], "zh")
            self.assertEqual(args[2], "jp")

    async def test_honest_chinese_fallback_is_translated_to_configured_target(self):
        """目标语言是日语时，zh-CN 即使可直接合成也不能绕过目标语言。"""
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "x",
                        "voice_lang": "jp",
                        "translate_target_language": "jp",
                        "translate_to_jp": False,
                        "prefer_model_voice_translation": True,
                        "gpt_weights_dir": td,
                        "sovits_weights_dir": td,
                        "ref_dir": td,
                    }
                }
            )
            tts.translation_service = mock.Mock(available=True)
            with mock.patch.object(
                tts,
                "supported_languages",
                return_value=("zh", "en", "jp"),
            ), mock.patch.object(
                tts,
                "_translate_text_async",
                new=mock.AsyncMock(return_value="こんにちは"),
            ) as translate:
                prepared = await tts._prepare_tts_text_async("你好呀", "zh-CN")

        self.assertEqual(prepared, ("こんにちは", "jp"))
        translate.assert_awaited_once_with("你好呀", "zh", "jp")

    async def test_ambiguous_japanese_text_uses_target_without_translation(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "x",
                        "voice_lang": "jp",
                        "translate_target_language": "jp",
                        "prefer_model_voice_translation": True,
                        "gpt_weights_dir": td,
                        "sovits_weights_dir": td,
                        "ref_dir": td,
                    }
                }
            )
            tts.translation_service = mock.Mock(available=True)
            with mock.patch.object(
                tts,
                "_translate_text_async",
                new=mock.AsyncMock(return_value="不应调用"),
            ) as translate:
                prepared = await tts._prepare_tts_text_async("東京", "ja-JP")

        self.assertEqual(prepared, ("東京", "jp"))
        translate.assert_not_awaited()

    async def test_target_text_with_wrong_metadata_is_normalized_without_translation(self):
        from meapet.tts.service import MeaTTS

        with tempfile.TemporaryDirectory() as td:
            tts = MeaTTS(
                {
                    "tts": {
                        "enabled": True,
                        "engine": "mimo",
                        "api_key": "x",
                        "voice_lang": "jp",
                        "translate_target_language": "jp",
                        "prefer_model_voice_translation": True,
                        "gpt_weights_dir": td,
                        "sovits_weights_dir": td,
                        "ref_dir": td,
                    }
                }
            )
            tts.translation_service = mock.Mock(available=True)
            with mock.patch.object(
                tts,
                "_translate_text_async",
                new=mock.AsyncMock(return_value="不应调用"),
            ) as translate:
                prepared = await tts._prepare_tts_text_async(
                    "ご主人、おかえりなさい。",
                    "zh-CN",
                )

        self.assertEqual(prepared, ("ご主人、おかえりなさい。", "jp"))
        translate.assert_not_awaited()

    def test_normalize_preserves_preference_without_legacy_translate_key(self):
        from meapet.config.store import normalize_config

        cfg = normalize_config(
            {"tts": {"prefer_model_voice_translation": True, "translate_api_key": ""}}
        )
        self.assertTrue(cfg["tts"]["prefer_model_voice_translation"])


class TestVoiceTranslationPrompt(unittest.TestCase):
    @staticmethod
    def _request(*, tts_enabled=True, prefer=True, available=True):
        from meapet.agent.base import AgentTurnRequest

        return AgentTurnRequest(
            turn_id="voice-translation-prompt",
            user_text="你好",
            tts_enabled=tts_enabled,
            frontend_context={
                "frontend_capabilities": {
                    "prefer_model_voice_translation": prefer,
                    # 既有协议字段；当前含义是非 LLM 机器翻译组件可用。
                    "translation_api_available": available,
                    "voice_target_language": "ja-JP",
                }
            },
        )

    def test_output_and_repair_prompts_request_configured_target_language(self):
        from meapet.agent.prompts import (
            build_output_instruction,
            build_repair_instruction,
        )

        request = self._request()
        for instruction in (
            build_output_instruction(request),
            build_repair_instruction(request),
        ):
            with self.subTest(instruction=instruction[:24]):
                self.assertIn("优先模型输出目标语朗读", instruction)
                self.assertIn("voice_target_language", instruction)
                self.assertIn("非 LLM 机器翻译", instruction)

    def test_dynamic_voice_instruction_requires_tts_preference_and_fallback(self):
        from meapet.agent.prompts import build_output_instruction

        for request in (
            self._request(tts_enabled=False),
            self._request(prefer=False),
            self._request(available=False),
        ):
            with self.subTest(request=request):
                self.assertNotIn(
                    "优先模型输出目标语朗读",
                    build_output_instruction(request),
                )

    def test_base_and_repair_prompts_always_require_voice_language_to_match_text(self):
        from meapet.agent.prompts import (
            build_output_instruction,
            build_repair_instruction,
        )

        request = self._request(prefer=False, available=False)
        for instruction in (
            build_output_instruction(request),
            build_repair_instruction(request),
        ):
            with self.subTest(instruction=instruction[:24]):
                self.assertIn("voice_text 实际使用的语言", instruction)
                self.assertIn("voice_text 与 voice_language 必须一致", instruction)


class TestPreferSourceLanguage(unittest.TestCase):
    """互动那一链念**文本自己那门语言**（人工裁乙）。

    目标语言配成 jp 时，点击反馈取回的古诗词/改写句与预制回退句都得出声：既不 machine
    translation 成日语，也不被草案 §五 那道闸门掐成静默。对话回复那一链不开这一档，
    仍按 `prefer_model_voice_translation` 翻到目标语言——下面最后一条把两支钉在同
    一份配置上比对，防止以后有人把这一档当成全局开关推开。
    """

    @staticmethod
    def _tts(td, **overrides):
        from meapet.tts.service import MeaTTS

        tts_cfg = {
            "enabled": True,
            "engine": "gpt_sovits",
            "api_key": "test-key-not-real",
            "voice_lang": "jp",
            "supported_languages": ["jp", "zh"],
            "translate_to_jp": False,
            "prefer_model_voice_translation": True,
            "output_dir": td,
            "gpt_weights_dir": td,
            "sovits_weights_dir": td,
            "ref_dir": td,
        }
        tts_cfg.update(overrides)
        return MeaTTS({"tts": tts_cfg})

    def test_supported_source_language_is_read_without_translation_or_silence(self):
        with tempfile.TemporaryDirectory() as td:
            tts = self._tts(td)
            route = tts._select_tts_text_route(
                "床前明月光，喵。", "jp", prefer_source_language=True
            )

        self.assertEqual(route, ("direct", "zh", "zh", "source_language_supported"))

    def test_confident_but_unsupported_source_language_skips_with_a_reason(self):
        """引擎念不了英文时静默并留下理由，而不是悄悄换成目标语言去念。"""
        with tempfile.TemporaryDirectory() as td:
            tts = self._tts(td)
            route = tts._select_tts_text_route(
                "hello my dear friend", "jp", prefer_source_language=True
            )

        self.assertEqual(route, ("skip", "en", "jp", "source_language_unsupported"))

    def test_undetectable_text_falls_through_to_the_existing_route(self):
        """「哼。」这类判不出来源语言的文本，开不开这一档结果都一样。"""
        with tempfile.TemporaryDirectory() as td:
            for prefer in (False, True):
                with self.subTest(prefer_source_language=prefer):
                    tts = self._tts(td, prefer_model_voice_translation=False)
                    tts.translation_service = mock.Mock(available=False)
                    route = tts._select_tts_text_route(
                        "哼。", "jp", prefer_source_language=prefer
                    )
                    self.assertEqual(
                        route, ("direct", "jp", "jp", "declared_text_ambiguous")
                    )

    def test_language_gate_lets_the_source_language_route_through(self):
        """闸门那条早退是这次静默的真凶：不开这一档它照旧拦，开了就得让位给路由。"""
        from meapet.tts.language_policy import (
            should_skip_tts_due_to_language_mismatch,
        )

        text = "主人今天很闲喵？"
        self.assertTrue(should_skip_tts_due_to_language_mismatch(text, "jp"))

        with tempfile.TemporaryDirectory() as td:
            # mimo 只为了让 speak 跳过本地权重文件那两道前置检查——闸门与引擎无关。
            tts = self._tts(td, engine="mimo")
            with mock.patch.object(tts, "_ensure_deps", return_value=True):
                with mock.patch.object(
                    tts, "_prepare_tts_text", return_value=None
                ) as prepare:
                    self.assertIsNone(tts.speak(text, "happy"))
                prepare.assert_not_called()

                with mock.patch.object(
                    tts, "_prepare_tts_text", return_value=None
                ) as prepare:
                    self.assertIsNone(
                        tts.speak(text, "happy", prefer_source_language=True)
                    )
                prepare.assert_called_once()
                self.assertEqual(
                    prepare.call_args.kwargs, {"prefer_source_language": True}
                )
                self.assertEqual(prepare.call_args.args[0], text)

    def test_async_local_path_hands_the_preference_to_speak(self):
        with tempfile.TemporaryDirectory() as td:
            tts = self._tts(td)
            text = "主人今天很闲喵？"

            with mock.patch.object(tts, "speak", return_value=None) as speak:
                self.assertIsNone(
                    asyncio.run(
                        tts.speak_async(
                            text, mood="happy", prefer_source_language=True
                        )
                    )
                )
                self.assertTrue(speak.call_args.kwargs["prefer_source_language"])

            with mock.patch.object(tts, "speak", return_value=None) as speak:
                self.assertIsNone(asyncio.run(tts.speak_async(text, mood="happy")))
                speak.assert_not_called()

    def test_the_two_chains_diverge_on_one_config(self):
        """同一份配置：对话回复翻到 jp，互动念 zh 原文，且后者不碰翻译。"""
        with tempfile.TemporaryDirectory() as td:
            tts = self._tts(
                td,
                engine="mimo",
                supported_languages=["zh", "en", "jp"],
                translate_target_language="jp",
            )
            tts.translation_service = mock.Mock(available=True)
            with mock.patch.object(
                tts,
                "_translate_text_async",
                new=mock.AsyncMock(return_value="こんにちは"),
            ) as translate:
                dialogue = asyncio.run(tts._prepare_tts_text_async("你好呀", "zh-CN"))
                interaction = asyncio.run(
                    tts._prepare_tts_text_async(
                        "你好呀", "zh-CN", prefer_source_language=True
                    )
                )

            translate.assert_awaited_once_with("你好呀", "zh", "jp")

        self.assertEqual(dialogue, ("こんにちは", "jp"))
        self.assertEqual(interaction, ("你好呀", "zh"))

    def test_worker_forwards_the_preference_only_when_asked(self):
        from meapet.desktop.workers import TTSWorker

        class WideTTS:
            voice_lang = "jp"

            def __init__(self):
                self.kwargs = []

            async def speak_async(
                self, text, mood="neutral", style="", language="", **kwargs
            ):
                self.kwargs.append(kwargs)
                return ("/tmp/source.wav", "zh")

        wide = WideTTS()
        result = asyncio.run(
            TTSWorker(
                wide, "床前明月光", mood="happy", prefer_source_language=True
            )._run()
        )
        self.assertEqual(result, "/tmp/source.wav|zh")
        self.assertEqual(wide.kwargs, [{"prefer_source_language": True}])

        class NarrowTTS:
            """产品里替 tts 造的那些假对象只声明到 (text, mood)——多一枚关键字就 TypeError。"""

            async def speak_async(self, text, mood="neutral"):
                return ("/tmp/narrow.wav", "jp")

        result = asyncio.run(TTSWorker(NarrowTTS(), "哼。", mood="happy")._run())
        self.assertEqual(result, "/tmp/narrow.wav|jp")


class TestVitsLanguageChecksBypassed(unittest.TestCase):
    """VITS 那一路**不做**语言校验（人工 2026-10-12T00:36:57 裁决「去除校验」）。

    他那份 config 的形状是 engine=vits + voice_lang=jp +
    `translate_target_language=zh` + `prefer_model_voice_translation=true`，
    实测六格（三本文 × 两链）全被 skip ⇒ 桌宠每句不出声。推理前端拿到什么文本都
    照音素化，只按语言换标记（engines/vits.py:vits_frontend_text），所以这一路要的
    是"照念"，念出来可懂与否由耳朵裁（提案件 §待测第一条），不是在这里换成静默的理由。
    """

    @staticmethod
    def _vits_tts(td, **overrides):
        from meapet.tts.service import MeaTTS

        root = Path(td)
        model = root / "G_latest.pth"
        model.write_bytes(b"fake-vits-weights-not-an-lfs-pointer")
        config = root / "finetune_speaker.json"
        config.write_text("{}", encoding="utf-8")
        tts_cfg = {
            "enabled": True,
            "engine": "vits",
            "api_key": "test-key-not-real",
            "voice_lang": "jp",
            "translate_target_language": "zh",
            "translate_to_jp": False,
            "prefer_model_voice_translation": True,
            "output_dir": td,
            "ref_dir": td,
            "vits_model": str(model),
            "vits_config": str(config),
        }
        tts_cfg.update(overrides)
        return MeaTTS({"tts": tts_cfg})

    def test_three_gates_all_yield_on_the_same_config(self):
        """来源语言档、目标语言档、_language_plan 那档一起让位——早退只有一条真相。

        target 槽带的是**文本自己那门语言**，它只被引擎拿去挑 ``[ZH]``/``[JA]``
        标记；脚本判不出的纯汉字（古诗词那一类 unknown）仍按 jp 走音读——
        那句的听感不在这条裁决的改动范围内。
        """
        cases = (
            ("床前明月光，疑是地上霜。", "jp"),  # 脚本判 unknown（纯汉字无假名）
            ("今天的风好大，我想吃点热的。", "zh"),  # 脚本判 zh —— 他贴那条命中的就是它
            ("今日はいい天気ですね。", "jp"),  # 脚本判 jp
        )
        with tempfile.TemporaryDirectory() as td:
            tts = self._vits_tts(td)
            self.assertEqual(tts.supported_languages(), ("jp",))
            for text, frontend_lang in cases:
                for prefer in (False, True):
                    with self.subTest(text=text[:4], prefer=prefer):
                        self.assertEqual(
                            tts._select_tts_text_route(
                                text, "jp", prefer_source_language=prefer
                            ),
                            (
                                "direct",
                                "jp",
                                frontend_lang,
                                "language_checks_bypassed",
                            ),
                        )

    def test_no_translation_is_bought_in_on_the_way(self):
        """裁决里"不机翻"那一半：原文直接交给引擎，翻译一次都不叫。"""
        with tempfile.TemporaryDirectory() as td:
            tts = self._vits_tts(td)
            tts.translation_service = mock.Mock(available=True)
            with mock.patch.object(tts, "_translate_text") as translate:
                prepared = tts._prepare_tts_text("今天的风好大，喵。", "jp")
            translate.assert_not_called()
        self.assertEqual(prepared, ("今天的风好大，喵。", "zh"))

    def test_speak_hands_the_raw_text_to_the_engine(self):
        """外层那道 `should_skip_tts_due_to_language_mismatch` 也得让位，否则中文在
        voice_lang=jp 下仍被掐成静默，而且是一条与路由 reason 不同名的静默。

        `language` 一起断言：前端用哪一个标记（``[ZH]``/``[JA]``）只看这一个入参，
        它是路由与引擎之间唯一的口径通道。
        """
        with tempfile.TemporaryDirectory() as td:
            tts = self._vits_tts(td)
            with mock.patch.object(tts, "_ensure_deps", return_value=True):
                with mock.patch.object(
                    tts, "_speak_vits", return_value=("/tmp/vits.wav", "zh")
                ) as speak_vits:
                    result = tts.speak("今天的风好大，喵。", "happy")
        self.assertEqual(result, ("/tmp/vits.wav", "zh"))
        self.assertEqual(speak_vits.call_args.args[0], "今天的风好大，喵。")
        self.assertEqual(speak_vits.call_args.kwargs["language"], "zh")

    def test_async_entry_reaches_the_engine_too(self):
        with tempfile.TemporaryDirectory() as td:
            tts = self._vits_tts(td)
            with mock.patch.object(tts, "speak", return_value=None) as speak:
                self.assertIsNone(
                    asyncio.run(tts.speak_async("今天的风好大，喵。", mood="happy"))
                )
        self.assertEqual(speak.call_args.args[0], "今天的风好大，喵。")

    def test_the_other_two_engines_keep_their_checks(self):
        """作用域只在 VITS：GSV 的 supported 是从参考音频/配置推的真集合，闸留着。"""
        with tempfile.TemporaryDirectory() as td:
            vits = self._vits_tts(td)
            self.assertTrue(vits._language_checks_bypassed())

            gsv = TestPreferSourceLanguage._tts(td, supported_languages=["jp"])
            self.assertFalse(gsv._language_checks_bypassed())
            self.assertEqual(
                gsv._select_tts_text_route(
                    "hello my dear friend", "jp", prefer_source_language=True
                ),
                ("skip", "en", "jp", "source_language_unsupported"),
            )

            mimo = TestPreferSourceLanguage._tts(td, engine="mimo")
            self.assertFalse(mimo._language_checks_bypassed())

    def test_vits_mode_flag_takes_the_same_branch(self):
        """`vits_mode: true` 与 `engine: vits` 在 :389 是同一个判据，别两处两答。"""
        with tempfile.TemporaryDirectory() as td:
            tts = self._vits_tts(td, engine="gpt_sovits", vits_mode=True)
            self.assertTrue(tts._language_checks_bypassed())
            self.assertEqual(
                tts._select_tts_text_route("hello my dear friend", "jp"),
                ("direct", "jp", "jp", "language_checks_bypassed"),
            )

    def test_the_scope_follows_the_engine_that_actually_speaks(self):
        """`vits_mode` 与 `engine: mimo` 同给时两个 mode 位都真，但 MiMo 那一支在
        speak 里排在 VITS 前面（service.py:1205 早于 :1235）——所以去校验只跟着**真正出声**的
        引擎走，不跟着标志走，否则这条遗留组合会把 MiMo 的语言核对一并摘掉。"""
        with tempfile.TemporaryDirectory() as td:
            tts = self._vits_tts(td, engine="mimo", vits_mode=True)
            self.assertTrue(tts._vits_mode)
            self.assertTrue(tts._mimo_mode)
            self.assertFalse(tts._language_checks_bypassed())
            self.assertNotEqual(
                tts._select_tts_text_route("hello my dear friend", "jp")[0],
                "direct",
            )
