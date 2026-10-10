"""「获取渠道」这一节：付费渠道免责句 + 三条认的渠道。

措辞与渠道内容逐字来自人工 2026-10-10 的原话（见 `~/.Athena/projects/meapet/voice.md`），
不改写、不加标点、不换说法。免责句在加载弹窗与「关于」两处引用同一个常量，是为了让
回归测试能钉住"两边说的是同一句话"。
"""
from __future__ import annotations

PAID_CHANNEL_NOTICE = (
    "如果您在除了官方Q群、软件维护者或Github获取此软件且是付费获取，"
    "我们将不会解决您关于此软件的问题及因为此软件导致的任何问题"
)

# (标签, 值)。值以 http 开头的按可点开的链接渲染，其余按原样文本。
# 群号那一格是**审核群**，与仓库根 Q-A 文档里的 1083503687 不是同一个号——两个号各归各的
# 用途，界面这里只认人工点名的这一个，不合并、不"纠正"。
ACQUISITION_CHANNELS = (
    ("QQ群“审核群”", "1128485088"),
    ("主干仓库", "https://github.com/suan-11/mea-pet-public"),
    ("Linux、Wayland适配及其他测试功能", "https://github.com/FlexiAtom/met-pet-public-fix-linux-compat"),
)
