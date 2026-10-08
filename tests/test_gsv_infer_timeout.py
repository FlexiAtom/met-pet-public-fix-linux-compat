"""GSV 推理子进程的等待预算：下界由实测给出，不是猜的数。

2026-10-08 本机（CPU、整合包 runtime）实测：
``import ok (23.4s)`` + ``模型加载完成 (41.2s)`` = 64.6s 时**还没开始合成**，
25 字符整句 115.24s 才落盘。旧的默认 60s 装不下任何一次冷启动，只装得下暖缓存的
运气 ⇒ 同一条链昨天出声、今天不出声，而且日志只留一句 ``TTS timeout (60s)``。
"""

import sys
import unittest
from unittest import mock


# 光"加载完"就要越过的实测线：import + 模型加载。
MEASURED_COLD_LOAD_S = 64.6


def _tts(**tts_cfg):
    from meapet.tts.service import MeaTTS

    cfg = {"tts": {"enabled": True, "engine": "gpt_sovits", **tts_cfg}}
    with mock.patch.dict("os.environ", {}, clear=False):
        return MeaTTS(cfg)


def test_default_budget_clears_the_measured_cold_load_floor():
    """默认值必须大于"加载完"的实测下界，否则每一句冷启动都注定超时。"""
    from meapet.tts.common import GSV_INFER_TIMEOUT_FLOOR_S

    tts = _tts()
    assert tts.gsv_timeout > MEASURED_COLD_LOAD_S
    assert tts.gsv_timeout == float(GSV_INFER_TIMEOUT_FLOOR_S)


def test_tts_timeout_key_still_wins_when_raised():
    """配得比下界大就按配的来——这台机器更慢或句子更长时，那是用户唯一的旋钮。"""
    assert _tts(timeout=600).gsv_timeout == 600.0


def test_tts_timeout_key_smaller_than_the_floor_does_not_guarantee_silence():
    """配得比实测下界小，本地链永远只能得到"没有声音"——那不是一个可用的选择。"""
    assert _tts(timeout=5).gsv_timeout == 240.0


def test_the_cloud_budget_does_not_inherit_the_inference_floor():
    """云端一次 HTTP 请求不该跟着推理机的冷启动一起涨：tts.timeout 原样留给它。"""
    tts = _tts()
    assert tts.timeout == 60
    assert tts.timeout < tts.gsv_timeout


def test_gsv_subprocess_run_uses_the_raised_budget():
    """读点真的换成 gsv_timeout：换错格子时上面几条全绿，这条会红。"""
    from meapet.tts.engines.gsv import TtsGsvMixin

    host = type(
        "Host",
        (),
        {
            "python_exe": sys.executable,
            "gsv_root": "",
            "infer_script": "gsv_infer.py",
            "gpt_path": "gpt.ckpt",
            "sovits_path": "sovits.pth",
            "top_k": 15,
            "top_p": 0.8,
            "temperature": 0.6,
            "speed": 1.0,
            "sample_steps": 8,
            "timeout": 60,
            "gsv_timeout": 240.0,
        },
    )()
    seen = {}

    class Result:
        returncode = 1
        stdout = b'{"ok": false, "error": "stop here"}'
        stderr = b""

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        return Result()

    with mock.patch("meapet.tts.engines.gsv.subprocess.run", side_effect=fake_run):
        TtsGsvMixin._speak_gsv(
            host, "こんにちは", "out.wav", "neutral", "ref.wav", "参考", "日文"
        )
    assert seen["timeout"] == 240.0


if __name__ == "__main__":
    unittest.main()
