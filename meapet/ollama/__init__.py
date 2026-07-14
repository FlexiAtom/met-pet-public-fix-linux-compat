"""Ollama 后端专用逻辑聚合入口。"""

from .prompts import (
    OLLAMA_SYSTEM_PROMPT,
    build_ollama_messages,
    build_ollama_system_prompt,
)
from .vision import (
    should_use_relay_for_ollama,
    build_ollama_vision_prompt,
    handle_ollama_vision_attachment,
)

