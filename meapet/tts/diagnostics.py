"""语音这条链的**只读**判据——开发者选项与 `meapet --doctor` 共用的一份。

放在 tts 侧而不是菜单侧：`--doctor` 不经 Qt，判据若留在 `desktop.dev_options` 里，
无头取证就得去 import 一个建菜单的模块，那是给自己造第二条路。三枚都只读盘，
不写 config、不改 `engine`、不建窗口。
"""
from __future__ import annotations

import os

from meapet.tts.common import is_git_lfs_pointer

#: 等音频的口径：气泡要等音频就绪才显示，不先说一声，点下去就是分钟级"没反应"。
#: GSV 那两个数是本机量到的——暖缓存整句 58.75s，冷启动整句 115.24s（其中 import+模型加载 64.6s）。
#: VITS 那支不写秒数：本机 2026-10-08 那一路连前置都没过，没量到的数字不上屏幕。
SPEECH_WAIT_NOTES = {
    "gpt_sovits": "本地冷启动约 1–2 分钟，不是卡死",
    "vits": "本地合成，可能要等模型加载，不是卡死",
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
