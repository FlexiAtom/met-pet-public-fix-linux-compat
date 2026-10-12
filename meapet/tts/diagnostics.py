"""语音这条链的判据——开发者选项与 `meapet --doctor` 共用的一份。

放在 tts 侧而不是菜单侧：`--doctor` 不经 Qt，判据若留在 `desktop.dev_options` 里，
无头取证就得去 import 一个建菜单的模块，那是给自己造第二条路。前四枚只读盘，
不写 config、不改 `engine`、不建窗口；`speech_latency` 那枚**不是只读**（它真起推理
子进程并落一个 wav），所以调用方必须显式要到才跑，见 `meapet/doctor.py` 的 `--speech-latency`。
"""
from __future__ import annotations

import os
import time

from meapet.tts.common import is_git_lfs_pointer

#: 等音频的口径：气泡要等音频就绪才显示，不先说一声，点下去就是分钟级"没反应"。
#: GSV 那两个数是本机量到的——暖缓存整句 58.75s，冷启动整句 115.24s（其中 import+模型加载 64.6s）。
#: VITS 那支 2026-10-09 起也写秒数：子进程模式每句新起进程，那 13.5s 里就含 import+模型加载，
#: 不是"暖了以后"的数。同一形状两次读数差一倍（13.5s / 26.1s），所以给区间不给单点。
SPEECH_WAIT_NOTES = {
    "gpt_sovits": "本地冷启动约 1–2 分钟，不是卡死",
    "vits": "本地合成约 13–27 秒（每句都含模型加载），不是卡死",
}


def artifact_label(path: object) -> str:
    """权重路径后面跟一句它到底缺成什么样。

    "前置=不齐"分不出**没水化**和**文件坏了**这两种，而这两种的处置完全不同（前者
    `git lfs pull`，后者重装）。用的还是引擎自己那把判据 `is_git_lfs_pointer`，不另立。
    """
    text = str(path or "")
    if text and is_git_lfs_pointer(text):
        return f"{text}（Git LFS 指针，未水化）"
    return text


def tts_for_engine(config: dict, engine: str):
    """按要测的那一个引擎，用**给定那份配置**另建一份只给这次取证用的 TTS。

    不动产品那一格（`self.tts`）：配置说了算的东西被点一下调试键就改了是越权。
    也不能"临时改完再改回来"——气泡要等音频就绪才显示，那一两分钟里没有安全的还原点
    （期间正常对话会被测到一半的引擎带走）。所以调用方拿的是另一个对象，配置原文不动。
    """
    from meapet.tts.service import MeaTTS

    base = config or {}
    tts_cfg = dict(base.get("tts") or {})
    tts_cfg["engine"] = engine
    if engine != "vits":
        # `MeaTTS` 里 vits 那一路是 `engine == "vits" or tts_cfg["vits_mode"]`，
        # 旧键单独也能把朗读领去 VITS ⇒ 只换 engine 换不掉，两条都得钉。
        tts_cfg.pop("vits_mode", None)
    return MeaTTS({**base, "tts": tts_cfg})


def speech_facts(engine: str, tts) -> str:
    """每个引擎把它自己的前置事实写出来——读的都是引擎真正会用的那些方法，不另立判据。"""
    if engine == "vits":
        model, config, _speaker = tts._vits_knobs()
        return (
            f"{tts._vits_route().describe()} "
            f"model={artifact_label(model)} config={config}"
        )
    return (
        f"python={os.path.basename(str(getattr(tts, 'python_exe', '') or '')) or '未设'} "
        f"GPT={artifact_label(getattr(tts, 'gpt_path', None))} "
        f"SoVITS={artifact_label(getattr(tts, 'sovits_path', None))}"
    )


#: 测试词按语言分档，`speech_test_sentence` 按码取句。
#: 从前这里写的理由是"`speak()` 合成前有语言闸门，非目标语言的文本直接跳过不出声"——
#: 那道闸自 2026-10-12 `c486e50`（VITS）/`99f4626`（两路同档）起在本地链路撤掉了，
#: 只剩 mimo 模式还生效。现在分档是为了能按语言点菜，不是为了绕闸。
#: 2026-10-09 从 `desktop.dev_options` 搬来：`--doctor` 要用同一句，留在菜单侧就得让无头取证
#: 去 import 建菜单的模块，那是本模块存在时要消灭的第二条路。
SPEECH_TEST_SENTENCES = {
    "jp": "開発者オプションのテスト。こんにちは、メールです。",
    "zh": "开发者选项测试：你好，我是梅尔。",
    "en": "Developer options test. Hello, I am Mea.",
}


def speech_test_sentence(lang: object = "") -> str:
    from meapet.config.normalizers import canonical_tts_language

    return (
        SPEECH_TEST_SENTENCES.get(canonical_tts_language(lang) or "zh")
        or SPEECH_TEST_SENTENCES["zh"]
    )


#: 开发者选项「朗读测试」子菜单的语言档：顺序即菜单顺序，标签给人看、码给 `speech_test_sentence`。
#: 人工 2026-10-12T15:34:53「需要在开发者选项加中文和英文的合成（GSV和VITS都需要，目前都是日语）」
#: ——此前那两个键跟着配置 `tts.voice_lang` 取句（他这台是 jp ⇒ 中文/英文两档根本没有入口）。
#: **写死这三档，不跟着 `supported_languages()` 生成**：那玩意量的是**参考音频清单**的形状，
#: 不是合成能力——他这台只有 `jp_normal.wav`/`zh_normal.wav` 两份参考，实测返回 `('zh', 'jp')`，
#: 动态生成会正好把这次点名要的「英文」整档抹掉（GSV 侧非中文缺 `en_*` 时早就收成 jp 档念，见 `engines/gsv.py`）。
#: 次一等的理由：它遍历的是 set（`service.py:776-777` 的 `lowered = {…}` ⇒ 顺序随哈希种子漂，
#: 姊妹案 (B′) 实测 12 个进程 7×('zh','jp') / 5×('jp','zh')）。调试入口必须比产品更稳定，
#: 菜单不能每次点开不一样。
SPEECH_TEST_LANGUAGES = (("中文", "zh"), ("日文", "jp"), ("英文", "en"))

#: `speech_test_sentence` 与菜单标签共用的那一份对照（**码 → 标签**，表本身是标签在前）。
#: 与 `speech_test_sentence` 走同一个 `canonical_tts_language` + 同一个 zh 兜底，
#: 所以气泡里写的语言跟耳朵听到的不会分家。
SPEECH_TEST_LANGUAGE_LABELS = {code: text for text, code in SPEECH_TEST_LANGUAGES}


def speech_test_language_label(lang: object = "") -> str:
    from meapet.config.normalizers import canonical_tts_language

    code = canonical_tts_language(lang) or "zh"
    return SPEECH_TEST_LANGUAGE_LABELS.get(code, code)


#: 延迟那格每引擎烧几发。写两发不是为了取平均，是为了把"模型加载"和"页缓存暖了"这两笔
#: 成本分开看——见 `speech_latency` 的说明。
SPEECH_LATENCY_RUNS = 2


def speech_latency(engine: str, tts) -> str:
    """真合成 `SPEECH_LATENCY_RUNS` 发，逐发报墙钟耗时，并把**这次用的是哪份配置**贴在同一个读数里。

    两发是为了分开两种成本：两个引擎都每句新起推理子进程（`engines/gsv.py:90`、
    `engines/vits.py:181`），所以第二发暖的只是操作系统页缓存，模型那一笔每发都要重付——
    读成一个数就会把"点一下等多久"算错一半。秒数脱离配置没有意义（换选路、换权重、
    换 CPU/GPU 都翻几倍），所以 `speech_facts` 那份事实串跟读数同行，不留到隔壁那格。
    """
    if not getattr(tts, "enabled", False) or not tts.health_check():
        return "未测：前置不齐（那一发只会得到失败读数，不是延迟）"
    lang = tts._normalize_voice_lang()
    sentence = speech_test_sentence(lang)
    rows = []
    for index in range(1, SPEECH_LATENCY_RUNS + 1):
        started = time.monotonic()
        try:
            result = tts.speak(sentence)
        except Exception as exc:  # noqa: BLE001 - 取证面不往外递异常
            rows.append(f"发{index}={time.monotonic() - started:.2f}s 探测自身失败 {type(exc).__name__}")
            continue
        elapsed = time.monotonic() - started
        path = result[0] if isinstance(result, tuple) and result else None
        if path and os.path.isfile(path):
            rows.append(f"发{index}={elapsed:.2f}s→{os.path.basename(path)}({os.path.getsize(path)}B)")
        else:
            rows.append(f"发{index}={elapsed:.2f}s→未产出")
    return (
        " ".join(rows)
        + f" · 配置: 文本={lang}:{len(sentence)}字"
        + f" timeout={getattr(tts, 'timeout', None)}/{getattr(tts, 'gsv_timeout', '—')}"
        + f" · {speech_facts(engine, tts)}"
    )
