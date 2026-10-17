"""`meapet/tts/diagnostics.py` 里那三枚共用判据自己的形状。

这模块是「菜单与 `--doctor` 共用一份真值」那个裁决的落点，所以这里钉的是：
- **测试词表只有一份**，且菜单读的就是这一份（留在 `desktop.dev_options` 里就得让无头取证去 import 建菜单的模块）；
- **测试词过得了 `speak()` 前面那道语言闸门**（闸门与词表是两处代码，谁改了另一边就静默失配）；
- **`speech_latency` 不是只读**：前置不齐 / `enabled` 关着时一发都不许起（失败读数不是延迟，而起一发要十几秒到两分钟）；
- **秒数必须与所用配置同串出现**——换个选路、权重、CPU 或 GPU 这数翻几倍，脱了配置的秒数没有读法；
- **`speech_repetition` 的两道闸都排在合成之前**（仪器起不来时那一发白烧 60–135 s），
  且它**钉中文句**——仓里那台 ASR 只认 zh/en，跟着 `voice_lang` 走就是拿失读的耳朵去判清白。

全部用桩，不碰真引擎。
"""
import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meapet.tts.diagnostics import (  # noqa: E402
    REPETITION_PROBE_SENTENCE,
    SPEECH_TEST_SENTENCES,
    repetition_facts,
    speech_latency,
    speech_repetition,
    speech_test_sentence,
    wav_duration_seconds,
)
from meapet.tts.language_policy import should_skip_tts_due_to_language_mismatch  # noqa: E402


class StubTTS:
    """只给 `speech_latency` 用的假引擎：它读什么，这里就给什么，多的不给。"""

    def __init__(self, results=(), healthy=True, enabled=True, raises=()):
        self.enabled = enabled
        self.engine = "gpt_sovits"
        self.timeout = 90
        self.gsv_timeout = 180
        self.python_exe = "/opt/gsv/bin/python"
        self.gpt_path = "/weights/GPT.pth"
        self.sovits_path = "/weights/SoVITS.pth"
        self._healthy = healthy
        self._results = list(results)
        self._raises = set(raises)
        self.spoken = []
        self.health_checks = 0

    def health_check(self):
        self.health_checks += 1
        return self._healthy

    def _normalize_voice_lang(self):
        return "jp"

    def speak(self, text):
        index = len(self.spoken)
        self.spoken.append(text)
        if index in self._raises:
            raise RuntimeError("子进程炸了")
        return self._results[index] if index < len(self._results) else None


class TestSentenceTable(unittest.TestCase):
    def test_the_sentence_follows_the_configured_voice_language(self):
        """写死中文句配 jp 声线会被 speak() 里的语言闸门跳过 ⇒ 测试词跟着 voice_lang 走。"""
        self.assertIn("開発者", speech_test_sentence("jp"))
        self.assertIn("开发者选项测试", speech_test_sentence("zh"))
        self.assertIn("Developer", speech_test_sentence("en"))
        # 认不出的语言（含空/`auto`）回到中文那句，而不是抛
        self.assertIn("开发者选项测试", speech_test_sentence("auto"))
        self.assertIn("开发者选项测试", speech_test_sentence(""))

    def test_every_test_sentence_passes_the_language_gate(self):
        """闸门与测试词是两处代码，这里钉住它们不打架。"""
        for lang, sentence in SPEECH_TEST_SENTENCES.items():
            with self.subTest(lang=lang):
                self.assertFalse(
                    should_skip_tts_due_to_language_mismatch(sentence, lang),
                    f"{lang} 的测试词被自己的语言闸门跳过了",
                )

    def test_the_menu_reads_this_same_table_not_its_own_copy(self):
        """「共用一份真值」的机器版：菜单里再留一份表，这行就红。"""
        from meapet.desktop import dev_options

        self.assertIs(dev_options.speech_test_sentence, speech_test_sentence)
        self.assertFalse(
            hasattr(dev_options, "DEV_TEST_SENTENCES"),
            "菜单侧不许再存一份测试词表",
        )


class TestSpeechLatency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _wav(self, name, size=4096):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as handle:
            handle.write(b"\0" * size)
        return path

    def test_it_times_each_run_and_prints_the_config_it_used(self):
        first, second = self._wav("a.wav"), self._wav("b.wav")
        tts = StubTTS(results=[(first, "jp"), (second, "jp")])

        row = speech_latency("gpt_sovits", tts)

        self.assertIn("发1=", row)
        self.assertIn("发2=", row)
        self.assertIn("a.wav(4096B)", row)
        self.assertIn("b.wav(4096B)", row)
        self.assertEqual(tts.spoken, [SPEECH_TEST_SENTENCES["jp"]] * 2, "两发都该用同一个测试词")
        # 秒数不许裸奔：同一串里得能看见这次用的是哪份配置
        self.assertIn("配置: 文本=jp:", row)
        self.assertIn("timeout=90/180", row)
        self.assertIn("python=python", row)
        self.assertIn("GPT=/weights/GPT.pth", row)

    def test_unhealthy_prerequisite_starts_nothing(self):
        """前置不齐时那一发只会得到失败读数，那不是延迟，还白付十几秒。"""
        tts = StubTTS(healthy=False)

        row = speech_latency("gpt_sovits", tts)

        self.assertTrue(row.startswith("未测：前置不齐"), row)
        self.assertEqual(tts.spoken, [])

    def test_disabled_tts_starts_nothing_and_does_not_even_ask_health(self):
        tts = StubTTS(enabled=False)

        row = speech_latency("gpt_sovits", tts)

        self.assertTrue(row.startswith("未测：前置不齐"), row)
        self.assertEqual(tts.spoken, [])
        self.assertEqual(tts.health_checks, 0)

    def test_a_run_that_produces_no_wav_is_named_not_hidden(self):
        """`speak()` 有若干条路返回 None/空串（语言闸门、无参考音频），那必须写成"未产出"。"""
        tts = StubTTS(results=[None, (None, "")])

        row = speech_latency("gpt_sovits", tts)

        self.assertEqual(row.count("未产出"), 2, row)
        self.assertIn("发1=", row)

    def test_a_missing_file_is_not_reported_as_output(self):
        """返回了路径但文件不在＝那一发没成，不能报成 wav(0B)。"""
        tts = StubTTS(results=[(os.path.join(self.tmp.name, "ghost.wav"), "jp")] * 2)

        row = speech_latency("gpt_sovits", tts)

        self.assertEqual(row.count("未产出"), 2, row)

    def test_a_raising_speak_becomes_text_not_an_exception(self):
        """取证面不许把异常递给调用方——doctor 那一侧还要靠这行写完报告。"""
        tts = StubTTS(raises={0})

        row = speech_latency("gpt_sovits", tts)

        self.assertIn("探测自身失败 RuntimeError", row)
        self.assertIn("发2=", row, "第一发炸了不该连累第二发")


class TestRepetitionFacts(unittest.TestCase):
    """`repetition_facts` 那两列**按池项那张表自己那几行复现**。

    表在 `~/.Athena/projects/meapet/pool/gsv-chinese-output-token-repetition.md` §2，
    最后两列是人工逐字点出来的。这里钉的是"函数与那张表算的是同一件事"——
    换一批转写串它就是新读数，不新证任何东西。
    """

    def test_the_two_columns_reproduce_the_pools_own_rows(self):
        for transcript, expected in (
            ("开发发者这个显相向测测试", "字种 10/12 · 最长单字连读 2"),
            ("你好我这个没有影儿", "字种 9/9 · 最长单字连读 1"),
            ("唉哒哒哒哒哒哒哒哒哒哒哒哒哒哒哒哒哒哒", "字种 2/19 · 最长单字连读 18"),
        ):
            with self.subTest(transcript=transcript):
                self.assertIn(expected, repetition_facts(transcript))

    def test_empty_transcript_says_so_instead_of_zero_distinct(self):
        """塌到 1 s 那三发的 ASR 读数是**空串**——"零复读"会被读成模型的清白，必须写成"转写为空"。"""
        for blank in ("", "   ", None, "\n\t"):
            with self.subTest(blank=blank):
                self.assertEqual(
                    repetition_facts(blank), "字种 空（转写为空）· 最长单字连读 0"
                )


class TestWavDurationSeconds(unittest.TestCase):
    """本案那张表的**第一列**：塌到 1.02 s 那一族判据全靠它；读不出来给 0.0 而不是抛。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_it_reads_frames_over_rate(self):
        path = self._wav("t.wav", frames=32640, rate=32000)  # 本案那个确定长度
        self.assertAlmostEqual(wav_duration_seconds(path), 1.02, places=2)

    def test_a_file_that_is_not_a_wav_is_zero_not_an_exception(self):
        junk = os.path.join(self.tmp.name, "junk.wav")
        with open(junk, "wb") as handle:
            handle.write(b"not a wav at all")
        self.assertEqual(wav_duration_seconds(junk), 0.0)
        self.assertEqual(wav_duration_seconds(os.path.join(self.tmp.name, "ghost.wav")), 0.0)

    def _wav(self, name, frames, rate):
        import wave

        path = os.path.join(self.tmp.name, name)
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\0\0" * frames)
        return path


class TestSpeechRepetition(unittest.TestCase):
    """`--speech-repetition` 那一格的读法，与**排在合成之前**的那两道闸。

    这一发在这台 CPU 上是 60–135 s，所以红线是"没仪器就别付合成的钱"：前置不齐不发、
    仓内那台 ASR 起不来也不发。全部用桩——不起真合成、不加载真模型。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transcribed = []

    def _wav(self, name, frames=32000, rate=32000):
        import wave

        path = os.path.join(self.tmp.name, name)
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(b"\0\0" * frames)
        return path

    def _transcribe(self, path, recognizer, sample_rate=16000):
        self.transcribed.append(path)
        return "开发发者这个显相向测测试"

    def _instrument(self, stack, *, build=None, transcribe=None):
        """把 `meapet.voice.engine` 那两个函数换掉。

        diagnostics 里是**函数内 import**（取证面不许因为语音模块缺席就整条起不来），
        所以 patch 模块属性就够——那份 import 每次调用都重新取一次属性，取的正是桩。
        """
        from meapet.voice import engine as voice_engine

        stack.enter_context(
            mock.patch.object(
                voice_engine, "build_recognizer", build or (lambda precision=None: object())
            )
        )
        stack.enter_context(
            mock.patch.object(
                voice_engine, "transcribe_wav", transcribe or self._transcribe
            )
        )

    def test_it_reports_both_columns_with_the_transcript_and_artifact(self):
        tts = StubTTS(results=[(self._wav("dup.wav"), "zh")])
        with contextlib.ExitStack() as stack:
            self._instrument(stack)
            row = speech_repetition("gpt_sovits", tts, {})
        self.assertIn("字种 10/12", row)
        self.assertIn("最长单字连读 2", row)
        self.assertIn("转写=「开发发者这个显相向测测试」", row)
        self.assertIn("dup.wav", row)
        self.assertIn("1.00s", row, "产物时长要跟着读数走：塌到 1.02 s 那一族判据就是它")
        self.assertIn("配置: 文本=zh:", row)
        self.assertEqual(tts.spoken, [REPETITION_PROBE_SENTENCE])
        self.assertEqual(self.transcribed, [os.path.join(self.tmp.name, "dup.wav")])

    def test_the_probe_sentence_is_pinned_to_chinese_not_to_the_configured_voice(self):
        """仓里这台是 **zh-en** 双语 zipformer，日文产物上它的"零复读"是仪器失读（池项 §6.4）。

        `StubTTS._normalize_voice_lang()` 故意返回 `jp`：读数若跟着它取句，这一格就是拿一只
        读不了日文的耳朵去给日文判清白。
        """
        self.assertIn("开发者选项测试", REPETITION_PROBE_SENTENCE)
        tts = StubTTS(results=[(self._wav("pin.wav"), "zh")])
        with contextlib.ExitStack() as stack:
            self._instrument(stack)
            row = speech_repetition("gpt_sovits", tts, {})
        self.assertNotIn("開発者", tts.spoken[0], "钉死的那句必须是中文句")
        self.assertIn("文本=zh:", row)

    def test_unhealthy_prerequisite_starts_nothing(self):
        """这一发比延迟那格还贵（多一次模型加载），注定失败的那一发更不该起。"""
        tts = StubTTS(healthy=False)
        row = speech_repetition("gpt_sovits", tts, {})
        self.assertTrue(row.startswith("未测：前置不齐"), row)
        self.assertEqual(tts.spoken, [])

    def test_disabled_tts_starts_nothing_and_does_not_even_ask_health(self):
        tts = StubTTS(enabled=False)
        row = speech_repetition("gpt_sovits", tts, {})
        self.assertTrue(row.startswith("未测：前置不齐"), row)
        self.assertEqual(tts.spoken, [])
        self.assertEqual(tts.health_checks, 0)

    def test_missing_asr_model_burns_no_synthesis(self):
        """仪器缺席时先付合成钱 = 拿 135 s 换一个"没仪器"的读数。

        `AsrModelMissing` 要和别的起不来分开报：处置是下模型 vs 重装，不是同一句话。
        """
        from meapet.voice.engine import AsrModelMissing

        tts = StubTTS(results=[(self._wav("w.wav"), "zh")])

        def missing(precision=None):
            raise AsrModelMissing("语音识别模型缺失（int8 档）")

        with contextlib.ExitStack() as stack:
            self._instrument(stack, build=missing)
            row = speech_repetition("gpt_sovits", tts, {})
        self.assertIn("未测：语音识别模型缺失", row)
        self.assertIn("一发合成都没烧", row)
        self.assertEqual(tts.spoken, [])

    def test_a_recognizer_that_cannot_build_is_text_not_an_exception(self):
        """sherpa 没装/模型坏了那一档：报"起不来"，别把异常递给取证面。"""
        tts = StubTTS(results=[(self._wav("w.wav"), "zh")])

        def boom(precision=None):
            raise ImportError("sherpa-onnx 未安装")

        with contextlib.ExitStack() as stack:
            self._instrument(stack, build=boom)
            row = speech_repetition("gpt_sovits", tts, {})
        self.assertIn("ASR 起不来 ImportError", row)
        self.assertIn("一发合成都没烧", row)
        self.assertEqual(tts.spoken, [])

    def test_a_run_that_produced_no_wav_says_the_reading_does_not_hold(self):
        """没产物就没有可转写的东西——那一格必须写成"读数不成立"，不能留空让他以为是零复读。"""
        tts = StubTTS(results=[None])
        with contextlib.ExitStack() as stack:
            self._instrument(stack)
            row = speech_repetition("gpt_sovits", tts, {})
        self.assertIn("未产出", row)
        self.assertIn("复读读数不成立", row)
        self.assertEqual(self.transcribed, [])

    def test_a_transcribe_failure_still_names_the_artifact(self):
        """转写炸了而那一发已经付过：产物名与时长要留着，他可以直接去听那一个文件。"""
        tts = StubTTS(results=[(self._wav("bad.wav"), "zh")])

        def boom(path, recognizer, sample_rate=16000):
            raise RuntimeError("sherpa 崩了")

        with contextlib.ExitStack() as stack:
            self._instrument(stack, transcribe=boom)
            row = speech_repetition("gpt_sovits", tts, {})
        self.assertIn("转写失败 RuntimeError", row)
        self.assertIn("bad.wav", row)

    def test_the_precision_comes_from_the_config_it_is_given(self):
        """档位走传进来的那份 config，不在这里另立默认：换档位就是换一只耳朵。"""
        seen = []
        tts = StubTTS(results=[(self._wav("p.wav"), "zh")])

        def spy(precision=None):
            seen.append(precision)
            return object()

        with contextlib.ExitStack() as stack:
            self._instrument(stack, build=spy)
            speech_repetition("gpt_sovits", tts, {"voice_input": {"precision": "fp32"}})
        self.assertEqual(seen, ["fp32"])


if __name__ == "__main__":
    unittest.main()
