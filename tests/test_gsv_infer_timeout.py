"""GSV 推理子进程的等待预算：下界由实测给出，不是猜的数。

2026-10-08 本机（CPU、整合包 runtime）实测：
``import ok (23.4s)`` + ``模型加载完成 (41.2s)`` = 64.6s 时**还没开始合成**，
25 字符整句 115.24s 才落盘。旧的默认 60s 装不下任何一次冷启动，只装得下暖缓存的
运气 ⇒ 同一条链昨天出声、今天不出声，而且日志只留一句 ``TTS timeout (60s)``。
"""

import json
import os
import sys
import threading
import time
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


def _host(**overrides):
    base = {
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
    }
    base.update(overrides)
    return type("Host", (), base)()


def test_payload_pins_the_sampling_seed():
    """人工 2026-10-12T14:07:28「seed要改」：上游 ``seed`` 缺省是 -1，而 ``set_seed()``
    把 -1 当场换成 ``random.randint(0, 2**32-1)``（整合包 TTS_infer_pack/TTS.py:194-196/:1020）
    ⇒ 同一句连 6 发拿到 4 种时长（4.62/4.70/4.74/4.82）、12 个 wav md5 两两不同。
    payload 必须带定值，两趟之间不得漂移。
    """
    import json
    import threading

    from meapet.tts.engines.gsv import GSV_INFER_SEED, TtsGsvMixin

    host = _host()
    seen = []

    class Result:
        returncode = 1
        stdout = b'{"ok": false, "error": "stop here"}'
        stderr = b""

    def fake_run(command, **kwargs):
        seen.append(json.loads(kwargs["input"].decode("utf-8")))
        return Result()

    with mock.patch("meapet.tts.engines.gsv.subprocess.run", side_effect=fake_run):
        for _ in range(2):
            TtsGsvMixin._speak_gsv(
                host, "今天的风好大，喵。", "out.wav", "neutral", "ref.wav", "参考", "中文"
            )
    assert [row["seed"] for row in seen] == [GSV_INFER_SEED, GSV_INFER_SEED]
    # 交付脚本侧也得转发这一格：gsv_infer.py 的 run() 参数字典有逐参数默认值，
    # 不接 args["seed"] 等于把钉住的值丢在子进程门口。
    infer_source = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "meapet",
        "tools",
        "gsv_infer.py",
    )
    with open(infer_source, encoding="utf-8") as f:
        assert '"seed": args.get("seed"' in f.read()


def test_two_requests_are_serialized_not_parallel():
    """人工同刻裁决「要禁止并发」：两个请求同时起两个推理子进程会互相拖慢到双双撞闸
    （实测 14 字句在受压窗口 241.35 s / 240.38 s 被砍）。这里要的是排队，不是并行。
    """
    from meapet.tts.engines.gsv import TtsGsvMixin

    host = _host()
    intervals = []
    record_lock = threading.Lock()

    class Result:
        returncode = 1
        stdout = b'{"ok": false, "error": "stop here"}'
        stderr = b""

    def fake_run(command, **kwargs):
        started = time.monotonic()
        time.sleep(0.1)
        with record_lock:
            intervals.append((started, time.monotonic()))
        return Result()

    threads = [
        threading.Thread(
            target=lambda: TtsGsvMixin._speak_gsv(
                host, "你好", "out.wav", "neutral", "ref.wav", "参考", "中文"
            )
        )
        for _ in range(2)
    ]
    with mock.patch("meapet.tts.engines.gsv.subprocess.run", side_effect=fake_run):
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

    assert len(intervals) == 2
    intervals.sort()
    assert intervals[0][1] <= intervals[1][0], intervals


def test_the_queue_gives_up_with_a_reason_instead_of_hanging_the_caller():
    """排队有界：等满 gsv_timeout 就放弃这一次并留理由——朗读也有 UI 线程上的入口，
    无限期挂住比静默更糟。"""
    from meapet.tts.engines import gsv as gsv_module
    from meapet.tts.engines.gsv import TtsGsvMixin

    host = _host(gsv_timeout=0.05)
    entered = threading.Event()
    release = threading.Event()

    def hold_the_lock():
        gsv_module._GSV_INFER_LOCK.acquire()
        try:
            entered.set()
            release.wait(timeout=5)
        finally:
            gsv_module._GSV_INFER_LOCK.release()

    holder = threading.Thread(target=hold_the_lock, daemon=True)
    holder.start()
    assert entered.wait(timeout=5)
    try:
        with mock.patch("meapet.tts.engines.gsv.subprocess.run") as run:
            with mock.patch.object(gsv_module.log, "warning") as warn:
                got = TtsGsvMixin._speak_gsv(
                    host, "你好", "out.wav", "neutral", "ref.wav", "参考", "中文"
                )
    finally:
        release.set()
        holder.join(timeout=5)
    assert got == (None, "")
    run.assert_not_called()
    assert any("禁止并发" in str(call) for call in warn.call_args_list), warn.call_args_list


if __name__ == "__main__":
    unittest.main()
