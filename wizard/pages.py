"""向导页面（聚合导出）"""
from wizard.page_env import EnvCheckPage
from wizard.page_llm import LLMPage
from wizard.page_backend import BackendPage
from wizard.page_tts import TTSPage
from wizard.page_vision import VisionPage
from wizard.page_voice_input import VoiceInputPage

__all__ = [
    "EnvCheckPage",
    "LLMPage",
    "BackendPage",
    "TTSPage",
    "VisionPage",
    "VoiceInputPage",
]
