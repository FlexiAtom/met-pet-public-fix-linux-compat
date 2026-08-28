"""集中加载 System Prompt 配置。

所有硬编码的 System Prompt 统一存放在 ``config/system_prompts.json``。
本模块提供 :func:`load_system_prompt` 按 key 加载对应提示词字符串，
并在文件缺失/解析失败/key 不存在时返回调用方传入的 ``default``，
保证重构不破坏现有行为。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

# config/system_prompts.json 位于仓库根 config/ 目录
_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "system_prompts.json"

_CACHE: Dict[str, str] = {}


def load_system_prompt(key: str, default: str = "") -> str:
    """从 JSON 配置加载指定 key 的 System Prompt 文本。

    - 首次调用时整体读取并缓存 ``config/system_prompts.json``。
    - 每个 key 的解析结果也会被缓存，避免重复字典查找。
    - 任何异常（文件缺失/JSON 解析失败/key 不存在/类型错误）均返回 ``default``。
    """
    if key in _CACHE:
        return _CACHE[key]
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        entry = data.get(key)
        if isinstance(entry, dict):
            prompt = entry.get("prompt", "")
        elif isinstance(entry, str):
            prompt = entry
        else:
            prompt = ""
        if not isinstance(prompt, str):
            prompt = ""
        # 即使 prompt 为空字符串也缓存，避免重复查文件；空值回退由调用方 default 处理
        _CACHE[key] = prompt
        return prompt if prompt else default
    except (FileNotFoundError, json.JSONDecodeError, KeyError, OSError):
        return default


def reload_system_prompts() -> None:
    """清除缓存，强制下次加载时重新读取磁盘文件（便于配置热更新）。"""
    _CACHE.clear()
