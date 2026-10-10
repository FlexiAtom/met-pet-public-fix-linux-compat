"""点击反馈：一次单击可能给出的三种内容（预制语音 / 爬虫资料改写 / 古诗词）。

默认全关。打开任何一路都要先过一次性隐私告知，取文失败一律回退到预制语音链并留日志。
"""
from __future__ import annotations

from meapet.click_feedback.feedback import (
    CRAWLER,
    POETRY,
    PRESET,
    FeedbackResult,
    fetch_feedback_text,
    participating_sources,
    pick_source,
)
from meapet.click_feedback.sources import (
    Deadline,
    Retrieved,
    SourceUnavailable,
    fetch_crawler_material,
    fetch_poetry_line,
)
from meapet.click_feedback.variants import (
    compact,
    is_hit,
    query_variants,
    to_simplified,
    to_traditional,
)

__all__ = [
    "CRAWLER",
    "Deadline",
    "FeedbackResult",
    "POETRY",
    "PRESET",
    "Retrieved",
    "SourceUnavailable",
    "compact",
    "fetch_crawler_material",
    "fetch_feedback_text",
    "fetch_poetry_line",
    "is_hit",
    "participating_sources",
    "pick_source",
    "query_variants",
    "to_simplified",
    "to_traditional",
]
