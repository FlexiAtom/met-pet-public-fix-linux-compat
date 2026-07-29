"""语音输入 Mixin — 管理 VoiceEngine 生命周期和 UI 交互。

模式：点击切换（tap-to-toggle）
- 第一次点击麦克风按钮 → 开始录音
- 第二次点击 → 停止录音 → 转文字 → 填入输入框
"""
from __future__ import annotations

from typing import Optional

from meapet.log import get_color_logger

log = get_color_logger("voice")


class PetVoiceMixin:
    """语音输入功能 Mixin。

    需挂载到 MeaPet 主窗口（QWidget 子类），要求 self 具备:
    - self.config (dict): 全局配置
    - self._save_config() 方法
    - self._show_bubble(text, mood=None) 方法
    """

    _voice_engine = None
    _voice_listening = False

    # ------------------------------------------------------------------
    # 初始化 / 销毁
    # ------------------------------------------------------------------

    def _init_voice(self) -> None:
        """在 MeaPet.__init__ 的 _init_watcher 之后调用。"""
        cfg = self.config.get("voice_input") or {}
        if cfg.get("enabled", False):
            self._start_voice_engine(cfg)

    def _start_voice_engine(self, cfg: Optional[dict] = None) -> None:
        """创建 VoiceEngine（不开始录音，仅准备模型）。"""
        from meapet.voice.engine import VoiceEngine

        cfg = cfg or (self.config.get("voice_input") or {})

        self._voice_engine = VoiceEngine(
            language=cfg.get("language", "zh"),
        )
        self._voice_engine.recording_started.connect(self._on_voice_recording_started)
        self._voice_engine.recording_stopped.connect(self._on_voice_recording_stopped)
        self._voice_engine.result_ready.connect(self._on_voice_result)
        self._voice_engine.error.connect(self._on_voice_error)

    def _stop_voice_engine(self) -> None:
        if self._voice_engine is not None:
            try:
                self._voice_engine.stop(0)
            except Exception:
                pass
            self._voice_engine = None
        self._voice_listening = False

    # ------------------------------------------------------------------
    # 开关 + UI 刷新
    # ------------------------------------------------------------------

    def _toggle_voice_input(self) -> None:
        """右键菜单回调：开启/关闭语音输入，并立即刷新按钮可见性。"""
        vi = self.config.setdefault("voice_input", {
            "enabled": False,
            "engine": "faster_whisper",
            "model": "base",
            "language": "zh",
            "device": "cpu",
            "auto_send": False,
        })
        turning_on = not vi.get("enabled", False)
        vi["enabled"] = turning_on
        self._save_config()

        if turning_on:
            self._start_voice_engine(vi)
            self._show_bubble_edge("语音输入已开启")
        else:
            self._stop_voice_engine()
            self._show_bubble_edge("语音输入已关闭")

        self._refresh_voice_button()

    @property
    def voice_enabled(self) -> bool:
        return bool(
            (self.config.get("voice_input") or {}).get("enabled", False)
        )

    def _refresh_voice_button(self) -> None:
        """强制刷新输入框语音按钮的可见性。在 _start_chat 创建输入框后调用。"""
        composer = getattr(self, "_chat_input", None)
        if composer is None:
            return
        try:
            show = self.voice_enabled
            if hasattr(composer, "show_voice_button"):
                composer.show_voice_button(show)
                log.info(f"[voice] button visibility refreshed: show={show}")
        except RuntimeError:
            pass

    # ------------------------------------------------------------------
    # 录音切换
    # ------------------------------------------------------------------

    def _voice_toggle_recording(self) -> str:
        """点击麦克风按钮。返回状态描述字符串。"""
        if self._voice_engine is None:
            return "off"

        if not self._voice_engine.isRunning():
            ok = self._voice_engine.toggle()
            return "recording" if ok else "off"
        else:
            self._voice_engine.toggle()
            return "processing"

    # ------------------------------------------------------------------
    # 信号回调
    # ------------------------------------------------------------------

    def _on_voice_recording_started(self) -> None:
        self._voice_listening = True
        self._refresh_voice_button()
        composer = getattr(self, "_chat_input", None)
        if composer and hasattr(composer, "_on_voice_state_changed"):
            composer._on_voice_state_changed("recording")

    def _on_voice_recording_stopped(self) -> None:
        self._voice_listening = False

    def _on_voice_result(self, text: str) -> None:
        if not text:
            return
        auto_send = bool(
            (self.config.get("voice_input") or {}).get("auto_send", False)
        )
        composer = getattr(self, "_chat_input", None)
        if composer and hasattr(composer, "_on_voice_state_changed"):
            composer._on_voice_state_changed("done")
        if auto_send and hasattr(self, "_on_input_submit"):
            self._on_input_submit(text)
        elif composer:
            try:
                composer.input.setText(text)
            except RuntimeError:
                pass
        else:
            self._show_bubble_edge(
                f"识别结果：{text[:50]}{'…' if len(text) > 50 else ''}"
            )

    def _on_voice_error(self, message: str) -> None:
        composer = getattr(self, "_chat_input", None)
        if composer and hasattr(composer, "_on_voice_state_changed"):
            composer._on_voice_state_changed("error")
        self._show_bubble_edge(message)

    # ------------------------------------------------------------------
    # 便捷气泡
    # ------------------------------------------------------------------

    def _show_bubble_edge(self, text: str):
        try:
            self._show_bubble(text, mood=None)
        except Exception:
            pass
