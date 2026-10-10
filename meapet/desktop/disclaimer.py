"""付费渠道免责句：加载弹窗与「关于」共用一份。

措辞逐字来自人工 2026-10-10 的原话（见 `~/.Athena/projects/meapet/voice.md`），不改写、不加标点。
两处界面引用同一个常量，是为了让回归测试能钉住"两边说的是同一句话"。
"""
from __future__ import annotations

PAID_CHANNEL_NOTICE = (
    "如果您在除了官方Q群、软件维护者或Github获取此软件且是付费获取，"
    "我们将不会解决您关于此软件的问题及因为此软件导致的任何问题"
)
