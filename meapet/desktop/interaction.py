"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import random
import time
from typing import Optional

from PyQt5.QtCore import QTimer

from meapet.config.defaults import bubble_duration_ms
from meapet.desktop import status_language
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


#: 分区抽中爬虫/古诗词却取不到文本时的说法，也是喂给模型的那半句情境。
#: 两边都要中文：日志里出现 "upper" 人读不出是哪里，模型同理。
_ZONE_CLICK_LABELS = {
    "upper": "摸了摸你的头",
    "lower_left": "碰了你左侧一下",
    "lower_right": "碰了你右侧一下",
}

#: 同一个时刻只许有一趟在飞的取文：连点不该把后台 loop 排成一串延迟气泡。
_CLICK_FEEDBACK_POLL_MS = 200


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

    def _start_click_feedback(self, zone: str, preset_fallback) -> bool:
        """抽中爬虫/古诗词就排一趟异步取文，返回 True 表示这一下已交给新链（含它的回退）。

        返回 False 的情形都**立刻**走既有预制链，不排队：抽中预制、那一路没开或没点过隐私告知、
        上一趟还没回来（连点攒出一串延迟气泡比少说一句更糟）、爬虫没配 LLM。
        """
        from meapet.click_feedback.feedback import CRAWLER, POETRY, pick_source

        try:
            source = pick_source(self.config)
            if source not in (CRAWLER, POETRY):
                return False
            if getattr(self, "_click_feedback_worker", None) is not None:
                return False
            one_shot = None
            if source == CRAWLER:
                one_shot = getattr(getattr(self, "chat_engine", None), "one_shot_async", None)
                if one_shot is None:
                    log_error(
                        "click_feedback",
                        f"{_ZONE_CLICK_LABELS.get(zone, zone)} · 抽中爬虫但没配 LLM，回退预制语音",
                    )
                    return False
            from meapet.desktop.workers import ClickFeedbackWorker

            worker = ClickFeedbackWorker(
                self.config,
                source,
                one_shot=one_shot,
                zone_label=_ZONE_CLICK_LABELS.get(zone, zone),
            )
            worker.start()
            self._click_feedback_worker = worker
            self._click_feedback_fallback = preset_fallback
            self._ensure_click_feedback_poll()
            return True
        except Exception as e:
            log_error("click_feedback", f"排程失败: {type(e).__name__}: {e}")
            return False

    def _ensure_click_feedback_poll(self) -> None:
        timer = getattr(self, "_click_feedback_poll", None)
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(self._poll_click_feedback)
            self._click_feedback_poll = timer
        timer.start(_CLICK_FEEDBACK_POLL_MS)

    def _stop_click_feedback_poll(self) -> None:
        timer = getattr(self, "_click_feedback_poll", None)
        if timer is not None:
            timer.stop()

    def _poll_click_feedback(self) -> None:
        """取文回来：成功就念新文本，失败**必须留日志**再回退预制链（人工 2026-10 口径）。"""
        worker = getattr(self, "_click_feedback_worker", None)
        if worker is None:
            self._stop_click_feedback_poll()
            return
        if not worker.done:
            return
        result = worker.get_result()
        fallback = getattr(self, "_click_feedback_fallback", None)
        self._click_feedback_worker = None
        self._click_feedback_fallback = None
        self._stop_click_feedback_poll()
        if result is None or not getattr(result, "ok", False):
            reason = getattr(result, "error", "") or "取文任务没有返回结果"
            log_error("click_feedback", f"{reason} · 回退预制语音")
            if callable(fallback):
                try:
                    fallback()
                except Exception as e:
                    log_error("click_feedback", f"回退预制链失败: {type(e).__name__}: {e}")
            return
        try:
            self._record_interaction()
            self._safe_set_mood(result.mood)
            dur = bubble_duration_ms(self.config, "interaction")
            self._interaction_speak(result.text, dur, result.mood)
            QTimer.singleShot(3000, lambda: self._safe_set_mood("neutral"))
        except Exception as e:
            log_error("click_feedback", f"显示取回文本失败: {type(e).__name__}: {e}")

    def _toggle_click_feedback_source(self, name: str) -> None:
        """点击反馈某一路的拨杆：第一次开启先弹一次性隐私告知（人工口径：只在启用设置时弹）。

        「一生只弹一次」由 `privacy_acknowledged` 那一格自己承担，所以不再配「不再提示」复选框
        ——那是同一件事的第二处真值。关闭只清 `enabled`：确认过的告知不因关闭而作废。
        """
        from PyQt5.QtWidgets import QMessageBox
        from meapet.click_feedback import feedback
        from meapet.message_dialog import MeaMessageDialog

        title = feedback.SOURCE_TITLES.get(name, name)
        duration = bubble_duration_ms(self.config, "interaction")
        section = self.config.setdefault("click_feedback", {}).setdefault(name, {})
        turning_on = not bool(section.get("enabled", False))

        if turning_on and not section.get("privacy_acknowledged", False):
            dialog = MeaMessageDialog(
                self,
                title=f"开启{title}？",
                text=feedback.PRIVACY_NOTICES[name],
                icon=QMessageBox.Question,
                buttons=int(QMessageBox.Yes | QMessageBox.No),
                # 默认落点给「不」：授权不该是回车键的副产品。
                default_button=int(QMessageBox.No),
            )
            if dialog.exec_() != int(QMessageBox.Yes):
                self._show_bubble(
                    status_language.click_feedback_not_enabled(title), duration
                )
                return
            section["privacy_acknowledged"] = True

        section["enabled"] = turning_on
        self._save_config()

        # 落盘后按规范化写回的 config 回读再报状态（与屏幕观察同一口径：保存前那个字典会被换掉）。
        if not feedback.is_source_live(self.config, name):
            self._show_bubble(status_language.click_feedback_disabled(title), duration)
            return
        self._show_bubble(
            status_language.click_feedback_enabled(
                title, feedback.participation_gap(self.config, name)
            ),
            duration,
        )

    def _set_click_feedback_poetry_source(self, source: str, enabled: bool) -> None:
        """古诗词的源勾选。两档全取消也留着——抽不中的理由由菜单那行灰字讲，不在这儿替人改主意。"""
        from meapet.click_feedback.feedback import POETRY

        poetry = self.config.setdefault("click_feedback", {}).setdefault(POETRY, {})
        current = [str(item) for item in (poetry.get("sources") or ())]
        if enabled:
            if source not in current:
                current.append(source)
        else:
            current = [item for item in current if item != source]
        poetry["sources"] = current
        self._save_config()

    def _open_click_feedback_keywords(self, name: str) -> None:
        """某一源的关键词表；保存才写回，写回什么形状由归一化定（对话框不自己数）。"""
        from PyQt5.QtWidgets import QDialog

        from meapet.desktop.dialogs import ClickFeedbackKeywordsDialog

        dialog = ClickFeedbackKeywordsDialog(self.config, name, self)
        if dialog.exec_() == QDialog.Accepted:
            self._save_config()

    def _open_click_feedback_weights(self) -> None:
        """三路的抽中权重。"""
        from PyQt5.QtWidgets import QDialog

        from meapet.desktop.dialogs import ClickFeedbackWeightDialog

        dialog = ClickFeedbackWeightDialog(self.config, self)
        if dialog.exec_() == QDialog.Accepted:
            self._save_config()

    def _on_head_patted(self):
        """上半区：先按权重抽一路（爬虫/古诗词走异步），落回预制时才是原来那一链。"""
        if self._start_click_feedback("upper", self._head_patted_preset):
            return
        self._head_patted_preset()

    def _head_patted_preset(self):
        """既有那一链：优先用户/内置预制语音，目录为空时回退到反应句 + 缓存/TTS。"""
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
        if self._start_click_feedback("lower_left", self._lower_left_preset):
            return
        self._lower_left_preset()

    def _lower_left_preset(self):
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
        if self._start_click_feedback("lower_right", self._lower_right_preset):
            return
        self._lower_right_preset()

    def _lower_right_preset(self):
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
