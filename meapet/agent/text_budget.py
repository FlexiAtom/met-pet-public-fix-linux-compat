"""文本附件上下文预算辅助。

核心思想：不按固定文件大小拒绝，而是按"本次请求剩余 token 容量"
动态决定能否上传。调用方在 UI/提交入口估算当前已用 tokens，再据此
判断一组 TextAttachment 是否可附带。
"""

from __future__ import annotations

from typing import Iterable, Tuple

from meapet.agent.base import TextAttachment


def estimate_tokens(text: str) -> int:
    """粗略估计文本所占 token 数。

    策略：ASCII 字符按 1 词 ≈ 1.3 token；CJK 字符按 1 字 ≈ 1.5 token。
    这是一个与具体 tokenizer 无关的保守近似，用于预算判断而非精确计费。
    """
    if not text:
        return 0
    ascii_chars = 0
    cjk_chars = 0
    for ch in text:
        if ord(ch) < 128:
            ascii_chars += 1
        else:
            cjk_chars += 1
    # ASCII：每 4 字符约 1 token（词粒度近似）
    ascii_tokens = ascii_chars / 4.0
    # CJK：每字符约 1.5 token
    cjk_tokens = cjk_chars * 1.5
    return int(ascii_tokens + cjk_tokens) + 1


def attachments_token_cost(attachments: Iterable[TextAttachment]) -> int:
    """计算一组文本附件的总预估 token 开销（含隔离标记开销）。"""
    total = 0
    for att in attachments:
        total += estimate_tokens(att.text_content)
        total += 50  # 分隔符 + <<FILE>>/<<END FILE>> 标记开销
    return total


def can_attach_files(
    user_text: str,
    attachments: Iterable[TextAttachment],
    model_context_window: int,
    currently_used_tokens: int = 0,
    safety_factor: float = 0.85,
    reply_reserve_tokens: int = 320,
) -> Tuple[bool, str]:
    """判断一组附件能否随本次请求发送。

    Parameters
    ----------
    user_text : str
        用户本次输入文本。
    attachments : Iterable[TextAttachment]
        待附带的文本附件列表。
    model_context_window : int
        模型上下文窗口大小（token 数）。
    currently_used_tokens : int
        本次请求已占用的 token 数（system prompt + 历史消息等）。
    safety_factor : float
        安全系数，默认 0.85，预留部分窗口给模型推理。
    reply_reserve_tokens : int
        为模型回复预留的 token 数下限。

    Returns
    -------
    (allowed, reason) : (bool, str)
        allowed=True 表示可上传；否则 reason 为拒绝原因。
    """
    atts = tuple(attachments or ())
    budget = int(model_context_window * safety_factor) - reply_reserve_tokens
    if budget <= 0:
        return False, "上下文窗口过小，无法附加文件"

    user_cost = estimate_tokens(user_text or "")
    att_cost = attachments_token_cost(atts)
    total = currently_used_tokens + user_cost + att_cost

    if total > budget:
        return False, (
            f"文件总大小超出上下文预算"
            f"（预计 {total} tokens > 可用 {budget} tokens）"
        )
    return True, ""
