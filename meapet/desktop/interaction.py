"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import random
import time
from typing import Optional

from PyQt5.QtCore import QTimer

from meapet.config.defaults import bubble_duration_ms
from meapet.utils import (
    audio_cache_key,
    legacy_audio_cache_name,
    safe_print,
    log_error,
)
from meapet.desktop.audio import bubble_duration_for_audio


# Shipped interaction clips live in package assets. User-generated clips and
# TTS output stay in the writable runtime voice_cache directory.
_ZONE_DIRS = {
    "upper": "upper",
    "lower_left": "lower_left",
    "lower_right": "lower_right",
}


def _text_from_filename(name: str) -> str:
    """``jp_别摸了.wav`` → ``"别摸了"``"""
    stem = name.rsplit(".", 1)[0]
    return stem.split("_", 1)[-1] if "_" in stem else stem


class PetInteractionMixin:
    def _pick_zone_audio(self, zone: str) -> tuple[str, str] | None:
        """Pick a zone clip, preferring user overrides over bundled assets."""
        relative = _ZONE_DIRS.get(zone)
        if not relative:
            return None

        from meapet.paths import data_path, project_path

        directories = (
            data_path("voice_cache", relative),
            project_path("meapet", "assets", "interaction_voices", relative),
        )
        for directory in directories:
            if not os.path.isdir(directory):
                continue
            files = [
                name
                for name in os.listdir(directory)
                if name.lower().endswith(".wav")
            ]
            if not files:
                continue
            chosen = random.choice(files)
            return os.path.join(directory, chosen), _text_from_filename(chosen)
        return None

    def _on_zone_triggered(self, zone: str) -> bool:
        """从分区目录随机播一条语音并显示文字气泡。

        Returns:
            True 若成功播了预制语音；False 若分区目录不存在或为空。
        """
        picked = self._pick_zone_audio(zone)
        if not picked:
            return False
        path, text = picked
        self._record_interaction()
        self._safe_set_mood("neutral")
        dur = bubble_duration_ms(self.config, "interaction")
        wav_dur = self._get_wav_duration_ms(path)
        bubble_ms = bubble_duration_for_audio(wav_dur, dur)
        self.show_reply(text, "neutral", duration_ms=bubble_ms)
        self._play_audio(path, audio_type="sfx")
        QTimer.singleShot(4000, lambda: self._safe_set_mood("neutral"))
        return True

    def _on_head_patted(self):
        """上半区：优先用户/内置预制语音，目录为空时回退到 TTS。"""
        try:
            if self._on_zone_triggered("upper"):
                return
            # 分区无文件时的回退：文案 + 扁平缓存/TTS
            self._record_interaction()
            reactions = [
                ("……别摸我头发。", "annoyed"),
                ("……有事吗？", "curious"),
                ("哼。", "melancholy"),
                ("……", "shy"),
                ("别摸了……", "annoyed"),
            ]
            text, mood = random.choice(reactions)
            self._safe_set_mood(mood)
            dur = bubble_duration_ms(self.config, "interaction")
            self._interaction_speak(text, dur, mood)
            QTimer.singleShot(3000, lambda: self._safe_set_mood("neutral"))
        except Exception as e:
            log_error("head_patted", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] head_patted error: {e}")
            try:
                self._show_bubble(
                    "唔…摸头出了点问题喵",
                    bubble_duration_ms(self.config, "interaction"),
                )
            except Exception:
                pass

    def _on_lower_left_patted(self):
        try:
            self._on_zone_triggered("lower_left")
        except Exception as e:
            log_error("lower_left_patted", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] lower_left error: {e}")
            try:
                self._show_bubble(
                    "唔…出错了喵",
                    bubble_duration_ms(self.config, "interaction"),
                )
            except Exception:
                pass

    def _on_lower_right_patted(self):
        try:
            self._on_zone_triggered("lower_right")
        except Exception as e:
            log_error("lower_right_patted", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] lower_right error: {e}")
            try:
                self._show_bubble(
                    "唔…出错了喵",
                    bubble_duration_ms(self.config, "interaction"),
                )
            except Exception:
                pass

    def _interaction_speak(self, text: str, duration_ms: int, mood: str):
        """互动语音：优先缓存，否则走 TTS 合成（失败不抛到 Qt 事件循环）"""
        try:
            cache_file = self._get_cached_interaction(text, "jp")
            if cache_file:
                bubble_ms = bubble_duration_for_audio(
                    self._get_wav_duration_ms(cache_file),
                    duration_ms,
                )
                self.show_reply(text, mood, duration_ms=bubble_ms)
                self._play_audio(cache_file, audio_type="sfx")
            else:
                self._speak_and_show(text, duration_ms, mood)
        except Exception as e:
            log_error("interaction_speak", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] interaction_speak error: {e}")
            try:
                # 至少显示文字，不因 TTS/缓存失败而静默或崩溃
                self.show_reply(text, mood, duration_ms=duration_ms)
            except Exception:
                try:
                    self._show_bubble(
                        text,
                        duration_ms
                        or bubble_duration_ms(self.config, "interaction"),
                    )
                except Exception:
                    pass

    def _safe_name(self, text: str) -> str:
        """文本 → 不暴露原文的稳定缓存键。"""
        return audio_cache_key(text)

    def _get_cached_interaction(self, text: str, lang: str = "jp") -> Optional[str]:
        """获取运行时互动语音缓存（根目录扁平命名）。"""
        safe = self._safe_name(text)
        if not safe:
            return None
        from meapet.paths import data_path, project_path
        cache_dirs: list[str] = []
        for candidate in (
            data_path("voice_cache"),
            project_path("voice_cache"),
        ):
            if candidate not in cache_dirs:
                cache_dirs.append(candidate)
        prefixes: list[str] = []
        for candidate in (
            lang,
            getattr(getattr(self, "tts", None), "voice_lang", None),
            "jp",
        ):
            prefix = str(candidate or "").strip()
            if prefix and prefix not in prefixes:
                prefixes.append(prefix)
        for cache_dir in cache_dirs:
            for prefix in prefixes:
                path = os.path.join(cache_dir, f"{prefix}_{safe}.wav")
                if os.path.exists(path):
                    return path
            legacy = legacy_audio_cache_name(text)
            if not legacy:
                continue
            for prefix in prefixes:
                legacy_path = os.path.join(cache_dir, f"{prefix}_{legacy}.wav")
                if os.path.exists(legacy_path):
                    return legacy_path
        return None

    def _idle_action(self):
        """随机空闲表情变化"""
        if random.random() < 0.4:
            return
        moods = ["neutral", "happy", "curious", "melancholy"]
        self._safe_set_mood(random.choice(moods))

    # ========================
    # 对话气泡
    # ========================
    def _on_bubble_stack_changed(self) -> None:
        stack = getattr(self, "_bubble_stack", None)
        if stack is None:
            return
        self.bubble = stack.latest
        if hasattr(self, "_position_bubble"):
            self._position_bubble(animate=True)
        # 待机穿透开启时，新气泡也要立即不抢鼠标。
        if getattr(self, "_standby", False):
            set_pass = getattr(self, "_set_bubbles_mouse_passthrough", None)
            if callable(set_pass):
                set_pass(True)

    def _clear_bubbles(self) -> None:
        stack = getattr(self, "_bubble_stack", None)
        if stack is not None:
            stack.hide_all()
            self.bubble = None
            return
        bubble = getattr(self, "bubble", None)
        if bubble is not None:
            try:
                bubble.hide()
            except RuntimeError:
                pass

    def _show_bubble(self, text: str, duration_ms: int = None, mood: str | None = None):
        try:
            if duration_ms is None:
                duration_ms = bubble_duration_ms(self.config, "default")
            stack = getattr(self, "_bubble_stack", None)
            if stack is not None:
                self.bubble = stack.show_message(text, duration_ms, mood=mood)
            elif getattr(self, "bubble", None) is not None:
                self.bubble.show_text(text, duration_ms, mood=mood)
                if hasattr(self, "_position_bubble"):
                    self._position_bubble()
            if getattr(self, "_standby", False):
                set_pass = getattr(self, "_set_bubbles_mouse_passthrough", None)
                if callable(set_pass):
                    set_pass(True)
        except Exception as e:
            log_error("show_bubble", f"{type(e).__name__}: {e}")
            safe_print(f"[pet] show_bubble error: {e}")

    def _show_random_bubble(self, text: str):
        self._show_bubble(
            text,
            bubble_duration_ms(self.config, "interaction"),
        )

    def _record_interaction(self):
        """记录互动时间（聊天、摸头等触发）"""
        self._last_interaction_time = time.time()

    # ========================
    # 关闭事件
    # ========================
