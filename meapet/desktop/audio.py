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

from meapet.utils import safe_print, log_error, cloud_vision_allowed, resolve_gst_plugin_dir
from meapet.desktop.workers import ChatWorker, TTSWorker
from meapet.desktop.chat_input import ChatInputBox
from meapet.desktop.status_panel import StatusPanel


AUDIO_BUBBLE_TAIL_MS = 500
MIN_REPLY_BUBBLE_MS = 3000

#: 每进程只探一次：None=没探过，探过后是 (类三元组或 None, 真因)。
#: 底层库缺席（整合包把 libQt5Multimedia.so.5 那几条裁掉了）与"这一发没播成"是两件事，
#: 所以前者印一次「首选 Qt 播放不可用」，后者才配得上「播放失败」四个字。
_QT_MEDIA_STATE = None
_QT_MEDIA_NOTICED = False

#: Qt5 QMediaPlayer::PlayingState
QT_PLAYING_STATE = 1

#: play() 之后多久没进过 Playing 就判「这发没播成」：本机健康那一路 play() 返回时状态已是
#: Playing（0ms 实测），坏那一路 0/120/250/500/1000/2000ms 全程 state=0 且一次信号都不发。
QT_PLAY_VERIFY_MS = 400

def _ensure_gst_plugin_path() -> None:
    """冻结态把 GStreamer 的系统插件目录指回真那一个；源码态与别的平台一律不动。

    PyInstaller 把 `libgstreamer-1.0.so.0` 搬进 `_internal/` 之后，GStreamer 用 dladdr 反推
    「安装目录」拿到 `_internal/`，于是把插件目录算成 `_internal/gstreamer-1.0`——那里一个插件
    也没有，注册表空 ⇒ Qt 那一路只报 `no element "identity"`，**不发 error 信号、不抛异常**，
    于是静音而日志照说「Qt 播放」。三臂实测见
    `~/.Athena/projects/meapet/working/frozen-gst-plugin-dir-relocated.md` §2。
    """
    if not getattr(sys, "frozen", False) or not sys.platform.startswith("linux"):
        return
    if os.environ.get("GST_PLUGIN_SYSTEM_PATH"):
        return  # 他自己指过就不抢
    cand = resolve_gst_plugin_dir()
    if cand:
        os.environ["GST_PLUGIN_SYSTEM_PATH"] = cand
        safe_print(f"[audio] 冻结态 GStreamer 插件目录指回: {cand}")
    else:
        safe_print("[audio] 冻结态没找到 GStreamer 插件目录 ⇒ Qt 那一路多半静音，这一发会落到系统播放器")


def _qt_media_bundle():
    """返回 (QMediaPlayer, QMediaContent, QUrl)，底层库不可用时返回 None 并把真因**只印一次**。"""
    global _QT_MEDIA_STATE, _QT_MEDIA_NOTICED
    if _QT_MEDIA_STATE is None:
        _ensure_gst_plugin_path()
        try:
            from PyQt5.QtMultimedia import QMediaContent, QMediaPlayer
            from PyQt5.QtCore import QUrl
            _QT_MEDIA_STATE = ((QMediaPlayer, QMediaContent, QUrl), "")
        except Exception as exc:  # noqa: BLE001 - 不可用是这条链上的一条读数，不是异常
            _QT_MEDIA_STATE = (None, f"{type(exc).__name__}: {exc}")
    bundle, reason = _QT_MEDIA_STATE
    if bundle is None and not _QT_MEDIA_NOTICED:
        _QT_MEDIA_NOTICED = True
        safe_print(f"[audio] 首选 Qt 播放不可用：{reason} ⇒ 本进程改走系统播放器，音量档不生效")
    return bundle


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

        # 首选：PyQt5 QtMultimedia（跨平台，支持音量控制）
        bundle = _qt_media_bundle()
        if bundle is not None:
            QMediaPlayer, QMediaContent, QUrl = bundle
            try:
                if not hasattr(self, "_media_player") or self._media_player is None:
                    self._media_player = QMediaPlayer(self)
                    state_changed = getattr(self._media_player, "stateChanged", None)
                    if state_changed is not None:
                        state_changed.connect(self._on_qt_state_changed)
                self._media_player.stop()
                self._media_player.setMedia(QMediaContent(QUrl.fromLocalFile(abs_path)))
                self._media_player.setVolume(volume)
                self._qt_saw_playing = False
                self._play_seq = getattr(self, "_play_seq", 0) + 1
                self._media_player.play()
                safe_print(f"[audio] Qt 播放: {os.path.basename(abs_path)}, volume={volume}%")
                seq = self._play_seq
                QTimer.singleShot(
                    QT_PLAY_VERIFY_MS,
                    lambda: self._verify_qt_playback(seq, abs_path, volume),
                )
                return
            except Exception as e:
                # 这一行只留给**真发了却没播成**的那一发；底层库缺席走的是上面那句「不可用」
                safe_print(f"[audio] Qt 播放失败: {e}")

        self._fallback_play(abs_path, volume)

    def _on_qt_state_changed(self, state):
        if int(state) == QT_PLAYING_STATE:
            self._qt_saw_playing = True

    def _verify_qt_playback(self, seq: int, abs_path: str, volume: int):
        """`play()` 不抛**不等于**播成了——注册表空那格既不走异常也不走 error 信号，唯一实读得到的
        是「这一发有没有一次把 state 推进 Playing」。没有就当没出声，落到系统播放器。
        """
        if seq != getattr(self, "_play_seq", 0):
            return  # 已被更新的一发接管，那条自己会验
        if getattr(self, "_qt_saw_playing", False):
            return
        player = getattr(self, "_media_player", None)
        state = int(player.state()) if player is not None and hasattr(player, "state") else -1
        safe_print(
            f"[audio] Qt 未起播（{QT_PLAY_VERIFY_MS}ms 内 state 没进 Playing，读数 state={state}）"
            f"⇒ 这一发改走系统播放器"
        )
        self._fallback_play(abs_path, volume)

    def _fallback_play(self, abs_path: str, volume: int):
        """备用：系统默认播放器（不支持音量控制）"""
        try:
            if sys.platform.startswith("win"):
                os.startfile(abs_path)  # type: ignore[attr-defined]
                played = "startfile 打开"
            elif sys.platform == "darwin":
                subprocess.Popen(["afplay", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                played = "afplay 播放"
            else:
                subprocess.Popen(["aplay", abs_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                played = "aplay 播放"
            safe_print(
                f"[audio] {played}: {os.path.basename(abs_path)}"
                f"（音量档 {volume}% 未生效：这条不带增益）"
            )
        except Exception as e:
            safe_print(f"[audio] 最终播放失败: {e}")

    # ========================
    # 屏幕观察（截屏吐槽）
    # ========================

