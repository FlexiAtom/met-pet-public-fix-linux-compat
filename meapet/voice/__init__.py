"""语音输入模块 — 录音 + faster-whisper 转文字。

所有功能默认关闭（voice_input.enabled=false）。用户需在配置向导或右键菜单中主动开启。
"""
from .engine import VoiceEngine, create_voice_engine

__all__ = ["VoiceEngine", "create_voice_engine"]
