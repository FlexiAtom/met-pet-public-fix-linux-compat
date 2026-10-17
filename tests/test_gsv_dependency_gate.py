"""GSV 依赖闸门的清单口径：只数推理子进程真要 import 的东西。

出处：Athena 件 meapet/tts-speech-false-missing-deps。``GSV_REQUIRED_PACKAGES``
从前带着 ``av`` 与 ``gradio``，本机那个整合包 runtime 两个都没装，于是
``check_gsv_dependencies()`` 报「缺少 2 个依赖：av, gradio」→ ``health_check``
判依赖不齐 → ``speak()`` 直接 ``return None, ""`` → 回退成文字气泡，看起来像
"这句没配音"。而绕开闸门、拿**同一个** runtime 直跑 ``gsv_infer.py`` 出 wav：
``合成完成 (25.9s, 3.86s 音频)``。闸门说不可用、推理说可用，两边不可能都对。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# webui 侧才 import 的包：整合包里 `import gradio` 只出现在 webui.py /
# inference_webui*.py / tools/my_utils.py / subfix_webui.py，`import av` 全树零命中。
# 它们进闸门等于把"没装 webui 依赖"的可用 env 整体判死。
_WEBUI_ONLY = ("av", "gradio")


def test_webui_only_packages_are_not_required_for_synthesis():
    from meapet.tts.common import (
        GSV_OPTIONAL_PACKAGES,
        GSV_REQUIRED_PACKAGES,
        _get_import_name,
    )

    required = {_get_import_name(pkg) for pkg in GSV_REQUIRED_PACKAGES}
    for banned in _WEBUI_ONLY:
        assert banned not in required, f"{banned} 是 webui 侧依赖，不该出现在推理闸门清单里"
        assert banned not in {_get_import_name(p) for p in GSV_OPTIONAL_PACKAGES}, (
            f"挪进可选清单同样会让 {banned} 参与判定"
        )


def test_the_gate_still_requires_the_audio_stack_the_infer_script_imports():
    """剪枝不是放宽：gsv_infer.py 自己 import 的那几个仍然必须在清单里。"""
    from meapet.tts.common import GSV_REQUIRED_PACKAGES, _get_import_name

    source = (ROOT / "meapet" / "tools" / "gsv_infer.py").read_text(encoding="utf-8")
    required = {_get_import_name(pkg) for pkg in GSV_REQUIRED_PACKAGES}

    for module in ("torchaudio", "numpy", "soundfile"):
        assert f"import {module}" in source, "交付脚本的 import 面变了，下面的断言得跟着改"
        assert module in required, f"{module} 被 gsv_infer.py 直接 import，缺了就是真不可用"
