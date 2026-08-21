"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import random
import re
import sys
import time
import wave
import subprocess
from typing import Optional

from PyQt5.QtWidgets import QMessageBox, QApplication
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QRegion
from PyQt5.QtCore import QRect

from meapet.utils import safe_print, log_error, cloud_vision_allowed
from meapet.desktop.workers import ChatWorker, TTSWorker
from meapet.desktop.chat_input import ChatInputBox
from meapet.desktop.status_panel import StatusPanel


AUDIO_BUBBLE_TAIL_MS = 500
MIN_REPLY_BUBBLE_MS = 3000


def bubble_duration_for_audio(
    audio_duration_ms: int,
    minimum_duration_ms: int,
) -> int:
    """保证气泡至少比有效音频多保留半秒。"""
    try:
        audio_ms = max(0, int(audio_duration_ms))
    except (TypeError, ValueError):
        audio_ms = 0
    try:
        minimum_ms = max(0, int(minimum_duration_ms))
    except (TypeError, ValueError):
        minimum_ms = 0
    if audio_ms <= 0:
        return minimum_ms
    return max(minimum_ms, audio_ms + AUDIO_BUBBLE_TAIL_MS)


class PetAudioMixin:
    @staticmethod
    def _get_wav_duration_ms(wav_path: str) -> int:
        """读取 wav 文件时长（毫秒）"""
        try:
            import wave
            with wave.open(wav_path, 'rb') as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return int(frames / rate * 1000)
        except Exception:
            pass
        return 0

    def _play_audio(self, wav_path: str, audio_type: str = "sfx"):
        """播放 wav 音频，audio_type 为 'sfx' 或 'tts'，音量从配置读取"""
        if not os.path.exists(wav_path):
            safe_print(f"[audio] 文件不存在: {wav_path}")
            return
        abs_path = os.path.abspath(wav_path)
        size = 0
        try:
            size = os.path.getsize(abs_path)
        except Exception:
            pass
        safe_print(f"[audio] 准备播放: {abs_path} ({size} bytes)")

        # 从配置读取音量
        volume = 100  # 默认
        try:
            config_audio = self.config.get("audio", {})
            if audio_type == "tts":
                volume = int(config_audio.get("tts_volume_percent", 90))
            else:
                volume = int(config_audio.get("sfx_volume_percent", 80))
            volume = max(0, min(100, volume))
        except (TypeError, ValueError):
            volume = 100

        # Windows 原生播放（不支持音量控制）
        try:
            import winsound
            winsound.PlaySound(abs_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            safe_print(f"[audio] winsound 播放: {os.path.basename(abs_path)} (音量设置无效)")
            return
        except Exception as e:
            safe_print(f"[audio] winsound 失败，尝试 Qt: {e}")

        # 备用：PyQt5 QtMultimedia（支持音量）
        try:
            from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
            from PyQt5.QtCore import QUrl
            if not hasattr(self, "_media_player") or self._media_player is None:
                self._media_player = QMediaPlayer(self)
            self._media_player.stop()
            self._media_player.setMedia(QMediaContent(QUrl.fromLocalFile(abs_path)))
            self._media_player.setVolume(volume)
            self._media_player.play()
            safe_print(f"[audio] Qt 播放: {os.path.basename(abs_path)}, volume={volume}")
            return
        except Exception as e:
            safe_print(f"[audio] Qt 播放失败: {e}")

        # 再备用：系统默认播放器（不支持音量控制）
        try:
            import subprocess
            import sys as _sys
            if _sys.platform.startswith("win"):
                os.startfile(abs_path)  # type: ignore[attr-defined]
                safe_print(f"[audio] startfile 打开: {os.path.basename(abs_path)}")
            elif _sys.platform == "darwin":
                subprocess.Popen(["afplay", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                safe_print(f"[audio] afplay 播放: {os.path.basename(abs_path)}")
            else:
                subprocess.Popen(["aplay", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                safe_print(f"[audio] aplay 播放: {os.path.basename(abs_path)}")
        except Exception as e:
            safe_print(f"[audio] 最终播放失败: {e}")

    # ========================
    # 屏幕观察（截屏吐槽）
    # ========================
