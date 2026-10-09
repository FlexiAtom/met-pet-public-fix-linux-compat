"""`meapet/tts/diagnostics.py` 里那三枚共用判据自己的形状。

这模块是「菜单与 `--doctor` 共用一份真值」那个裁决的落点，所以这里钉的是：
- **测试词表只有一份**，且菜单读的就是这一份（留在 `desktop.dev_options` 里就得让无头取证去 import 建菜单的模块）；
- **测试词过得了 `speak()` 前面那道语言闸门**（闸门与词表是两处代码，谁改了另一边就静默失配）；
- **`speech_latency` 不是只读**：前置不齐 / `enabled` 关着时一发都不许起（失败读数不是延迟，而起一发要十几秒到两分钟）；
- **秒数必须与所用配置同串出现**——换个选路、权重、CPU 或 GPU 这数翻几倍，脱了配置的秒数没有读法。

全部用桩，不碰真引擎。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from meapet.tts.diagnostics import (  # noqa: E402
    SPEECH_TEST_SENTENCES,
    speech_latency,
    speech_test_sentence,
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


if __name__ == "__main__":
    unittest.main()
