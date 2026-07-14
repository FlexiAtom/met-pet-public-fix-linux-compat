"""MeaPet 功能 mixin（从 pet.py 拆出）"""
from __future__ import annotations

import os
import random
import sys  # 新增
import time

from meapet.desktop import status_language
from meapet.desktop.chat_input import set_awaiting_reply_state
from meapet.utils import (
    cloud_vision_allowed,
    debug_enabled,
    is_loopback_url,
)
from meapet.desktop.workers import TTSWorker
from meapet.desktop.dialogs import confirm_cloud_vision
from meapet.config.store import (
    resolve_vision_api_base,
    resolve_vision_backend,
    resolve_vision_host,
)
from meapet.log import get_color_logger
from meapet.vision.policy import resolve_vision_route

log = get_color_logger("watch_ctrl")


def _log_private_text(label: str, text: str) -> None:
    """默认仅记录识图文本长度，调试模式才打印正文。"""
    value = str(text or "")
    if debug_enabled():
        log.debug(f"{label}: chars={len(value)}\n{value}")
    else:
        log.debug(f"{label}: chars={len(value)}")


class PetWatcherMixin:
    def _start_watcher_timer(self):
        """随机间隔，从配置读取范围"""
        print(f"[DEBUG watch_ctrl] _start_watcher_timer called", file=sys.stderr, flush=True)
        interval = (self.config.get("watcher") or {}).get("interval", {"min_ms": 60000, "max_ms": 600000})
        min_ms = interval.get("min_ms", 60000)
        max_ms = interval.get("max_ms", 600000)
        print(f"[DEBUG watch_ctrl] _start_watcher_timer: raw interval min_ms={min_ms}, max_ms={max_ms}", file=sys.stderr, flush=True)
        if min_ms > max_ms:
            min_ms, max_ms = max_ms, min_ms  # 保证最小值不大于最大值
            print(f"[DEBUG watch_ctrl] _start_watcher_timer: swapped min/max, now min_ms={min_ms}, max_ms={max_ms}", file=sys.stderr, flush=True)
        ms = random.randint(min_ms, max_ms)
        print(f"[DEBUG watch_ctrl] _start_watcher_timer: computed delay={ms}ms", file=sys.stderr, flush=True)
        self._watcher_timer.start(ms)
        print(f"[DEBUG watch_ctrl] _start_watcher_timer: timer started", file=sys.stderr, flush=True)

    def _vision_backend(self) -> str:
        print(f"[DEBUG watch_ctrl] _vision_backend called", file=sys.stderr, flush=True)
        vision_cfg = self.config.get("vision", {}) or {}
        llm_cfg = self.config.get("llm", {}) or {}
        result = resolve_vision_backend(vision_cfg, llm_cfg)
        print(f"[DEBUG watch_ctrl] _vision_backend returning: {result!r}", file=sys.stderr, flush=True)
        return result

    def _vision_route(self):
        print(f"[DEBUG watch_ctrl] _vision_route called", file=sys.stderr, flush=True)
        result = resolve_vision_route(
            self.config.get("vision", {}) or {},
            self.config.get("llm", {}) or {},
        )
        print(f"[DEBUG watch_ctrl] _vision_route returning: mode={result.mode}, available={result.available}, reason={result.reason!r}", file=sys.stderr, flush=True)
        return result

    def _vision_endpoint(self) -> str:
        """返回识图请求的实际目标地址。"""
        print(f"[DEBUG watch_ctrl] _vision_endpoint called", file=sys.stderr, flush=True)
        vision_cfg = self.config.get("vision", {}) or {}
        llm_cfg = self.config.get("llm", {}) or {}
        route = self._vision_route()
        print(f"[DEBUG watch_ctrl] _vision_endpoint: route.mode={route.mode}", file=sys.stderr, flush=True)
        if route.mode == "inherit":
            if str(llm_cfg.get("mode") or "direct").lower() == "agent":
                agent = llm_cfg.get("agent") or {}
                result = str(agent.get("base_url") or "")
                print(f"[DEBUG watch_ctrl] _vision_endpoint: inherit/agent, result={result!r}", file=sys.stderr, flush=True)
                return result
            direct = llm_cfg.get("direct") or {}
            protocol = str(direct.get("protocol") or "").lower()
            print(f"[DEBUG watch_ctrl] _vision_endpoint: inherit/direct, protocol={protocol!r}", file=sys.stderr, flush=True)
            if protocol == "ollama_chat":
                result = str(
                    direct.get("host")
                    or llm_cfg.get("host")
                    or "http://127.0.0.1:11434"
                )
                print(f"[DEBUG watch_ctrl] _vision_endpoint: ollama_chat, result={result!r}", file=sys.stderr, flush=True)
                return result
            result = str(direct.get("api_base") or llm_cfg.get("api_base") or "")
            print(f"[DEBUG watch_ctrl] _vision_endpoint: other protocol, result={result!r}", file=sys.stderr, flush=True)
            return result
        if self._vision_backend() == "mimo":
            result = resolve_vision_api_base(vision_cfg, llm_cfg)
            print(f"[DEBUG watch_ctrl] _vision_endpoint: mimo, result={result!r}", file=sys.stderr, flush=True)
            return result
        result = resolve_vision_host(vision_cfg, llm_cfg)
        print(f"[DEBUG watch_ctrl] _vision_endpoint: ollama relay, result={result!r}", file=sys.stderr, flush=True)
        return result

    def _is_cloud_vision(self) -> bool:
        """判断截图是否会离开本机；未知或远程目标按云端处理。"""
        print(f"[DEBUG watch_ctrl] _is_cloud_vision called", file=sys.stderr, flush=True)
        route = self._vision_route()
        print(f"[DEBUG watch_ctrl] _is_cloud_vision: route.mode={route.mode}", file=sys.stderr, flush=True)
        if route.mode == "inherit":
            endpoint = self._vision_endpoint()
            loopback = is_loopback_url(endpoint)
            print(f"[DEBUG watch_ctrl] _is_cloud_vision: inherit, endpoint={endpoint!r}, is_loopback={loopback}", file=sys.stderr, flush=True)
            result = not loopback
            print(f"[DEBUG watch_ctrl] _is_cloud_vision returning: {result}", file=sys.stderr, flush=True)
            return result
        backend = self._vision_backend()
        print(f"[DEBUG watch_ctrl] _is_cloud_vision: backend={backend!r}", file=sys.stderr, flush=True)
        if backend == "mimo":
            print(f"[DEBUG watch_ctrl] _is_cloud_vision: mimo => True", file=sys.stderr, flush=True)
            return True
        if backend == "ollama":
            endpoint = self._vision_endpoint()
            loopback = is_loopback_url(endpoint)
            print(f"[DEBUG watch_ctrl] _is_cloud_vision: ollama, endpoint={endpoint!r}, is_loopback={loopback}", file=sys.stderr, flush=True)
            result = not loopback
            print(f"[DEBUG watch_ctrl] _is_cloud_vision returning: {result}", file=sys.stderr, flush=True)
            return result
        print(f"[DEBUG watch_ctrl] _is_cloud_vision: unknown backend, default True", file=sys.stderr, flush=True)
        return True

    def _confirm_cloud_capture(self, force: bool = False) -> bool:
        """Gate before every cloud screenshot. Always ask; no session skip."""
        print(f"[DEBUG watch_ctrl] _confirm_cloud_capture called: force={force}", file=sys.stderr, flush=True)
        self.config.setdefault("watcher", {})
        if not self._is_cloud_vision():
            print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: not cloud vision, returning True", file=sys.stderr, flush=True)
            return True

        if not cloud_vision_allowed(self.config, True):
            log.info("[watcher] cloud vision disabled (allow_cloud=false)")
            print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: allow_cloud=false, showing disabled bubble", file=sys.stderr, flush=True)
            self._show_bubble(status_language.cloud_vision_disabled(), 4000)
            print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: returning False", file=sys.stderr, flush=True)
            return False

        msg = "\n".join([
            "即将截取当前屏幕，并把截图发送到云端识别。",
            "",
            "图中可能包含聊天、密码、邮件、代码等隐私信息。",
            "每次上传前都必须确认；取消则不会截屏。",
        ])
        print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: showing confirm dialog", file=sys.stderr, flush=True)
        allowed = confirm_cloud_vision(
            self,
            title="允许本次云端识图？",
            message=msg,
            timeout_seconds=5,
            accept_text="允许本次上传",
        )
        print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: user response={allowed}", file=sys.stderr, flush=True)
        if not allowed:
            log.info("[watcher] user denied cloud screenshot upload")
            print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: user denied, showing denied bubble", file=sys.stderr, flush=True)
            self._show_bubble(status_language.watching_denied(), 2500)
            print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: returning False", file=sys.stderr, flush=True)
            return False
        log.info("[watcher] user allowed cloud vision for this capture only")
        print(f"[DEBUG watch_ctrl] _confirm_cloud_capture: user allowed, returning True", file=sys.stderr, flush=True)
        return True

    def _do_screen_watch(self, force: bool = False):
        """Screenshot + vision roast. Cloud path must pass confirmation first."""
        print(f"[DEBUG watch_ctrl] _do_screen_watch called: force={force}", file=sys.stderr, flush=True)
        watcher_cfg = self.config.get("watcher", {})
        route = self._vision_route()
        print(f"[DEBUG watch_ctrl] _do_screen_watch: route mode={route.mode}, available={route.available}, reason={route.reason!r}", file=sys.stderr, flush=True)
        if route.mode == "disabled":
            print(f"[DEBUG watch_ctrl] _do_screen_watch: mode disabled, returning", file=sys.stderr, flush=True)
            return
        if not route.available:
            log.warning(f"[watcher] vision route unavailable: {route.reason}")
            print(f"[DEBUG watch_ctrl] _do_screen_watch: route unavailable, showing bubble", file=sys.stderr, flush=True)
            self._show_bubble(
                status_language.vision_mode_unavailable(route.reason),
                5000,
            )
            if hasattr(self, "_watcher_timer"):
                print(f"[DEBUG watch_ctrl] _do_screen_watch: restarting watcher timer", file=sys.stderr, flush=True)
                self._start_watcher_timer()
            print(f"[DEBUG watch_ctrl] _do_screen_watch: returning after unavailable", file=sys.stderr, flush=True)
            return
        enabled = watcher_cfg.get("enabled", False)
        print(f"[DEBUG watch_ctrl] _do_screen_watch: watcher enabled={enabled}, force={force}", file=sys.stderr, flush=True)
        if not enabled and not force:
            print(f"[DEBUG watch_ctrl] _do_screen_watch: not enabled and not force, returning", file=sys.stderr, flush=True)
            return
        standby = getattr(self, "_standby", False)
        print(f"[DEBUG watch_ctrl] _do_screen_watch: _standby={standby}, force={force}", file=sys.stderr, flush=True)
        if standby and not force:
            print(f"[DEBUG watch_ctrl] _do_screen_watch: standby and not force, returning", file=sys.stderr, flush=True)
            return
        awaiting = getattr(self, "_awaiting_reply", False)
        print(f"[DEBUG watch_ctrl] _do_screen_watch: _awaiting_reply={awaiting}, force={force}", file=sys.stderr, flush=True)
        if awaiting and not force:
            print(f"[DEBUG watch_ctrl] _do_screen_watch: awaiting reply and not force, restarting timer and returning", file=sys.stderr, flush=True)
            self._start_watcher_timer()
            return
        watcher_running = self._watcher.isRunning()
        print(f"[DEBUG watch_ctrl] _do_screen_watch: watcher isRunning={watcher_running}", file=sys.stderr, flush=True)
        if watcher_running:
            if force:
                print(f"[DEBUG watch_ctrl] _do_screen_watch: force=True, trying to stop watcher", file=sys.stderr, flush=True)
                if not self._watcher.stop():
                    log.warning("[watcher] previous capture did not stop in time")
                    print(f"[DEBUG watch_ctrl] _do_screen_watch: watcher stop failed, resetting state", file=sys.stderr, flush=True)
                    set_awaiting_reply_state(self, False)
                    self._start_watcher_timer()
                    return
                print(f"[DEBUG watch_ctrl] _do_screen_watch: watcher stopped successfully", file=sys.stderr, flush=True)
            else:
                print(f"[DEBUG watch_ctrl] _do_screen_watch: watcher running and not force, returning", file=sys.stderr, flush=True)
                return

        is_cloud = self._is_cloud_vision()
        print(f"[DEBUG watch_ctrl] _do_screen_watch: is_cloud_vision={is_cloud}", file=sys.stderr, flush=True)
        if is_cloud:
            if not self._confirm_cloud_capture(force=force):
                print(f"[DEBUG watch_ctrl] _do_screen_watch: cloud capture not confirmed, resetting state", file=sys.stderr, flush=True)
                set_awaiting_reply_state(self, False)
                self._start_watcher_timer()
                return
        else:
            log.info(f"[watcher] local vision backend={self._vision_backend()} (no upload)")
            print(f"[DEBUG watch_ctrl] _do_screen_watch: local vision, no upload needed", file=sys.stderr, flush=True)

        if not self._watcher.prepare_start():
            log.warning("[watcher] capture thread is still running")
            print(f"[DEBUG watch_ctrl] _do_screen_watch: prepare_start failed, resetting state", file=sys.stderr, flush=True)
            set_awaiting_reply_state(self, False)
            self._start_watcher_timer()
            return

        print(f"[DEBUG watch_ctrl] _do_screen_watch: setting awaiting reply state", file=sys.stderr, flush=True)
        set_awaiting_reply_state(
            self,
            True,
            status_language.thinking_busy(),
        )
        idle_s = time.time() - self._last_interaction_time
        idle_minutes = idle_s / 60.0
        print(f"[DEBUG watch_ctrl] _do_screen_watch: idle seconds={idle_s:.2f}, minutes={idle_minutes:.2f}", file=sys.stderr, flush=True)
        self._watcher.set_idle_minutes(idle_minutes)
        reply_adapter = (
            getattr(self, "agent_adapter", None)
            if self._is_agent_mode()
            else getattr(self, "chat_engine", None)
        )
        print(f"[DEBUG watch_ctrl] _do_screen_watch: reply_adapter type={type(reply_adapter).__name__ if reply_adapter else None}", file=sys.stderr, flush=True)
        self._watcher.configure_reply(
            reply_adapter,
            frontend_context=self._build_agent_frontend_context(),
            tts_enabled=bool(
                getattr(getattr(self, "tts", None), "enabled", False)
            ),
        )
        if is_cloud:
            bubble_text = "（已确认）梅尔酱偷看并上传识别中…"
            print(f"[DEBUG watch_ctrl] _do_screen_watch: showing cloud bubble", file=sys.stderr, flush=True)
        else:
            bubble_text = "梅尔酱偷看了一眼……"
            print(f"[DEBUG watch_ctrl] _do_screen_watch: showing local bubble", file=sys.stderr, flush=True)
        self._show_bubble(bubble_text, 30000)
        self._position_bubble()
        print(f"[DEBUG watch_ctrl] _do_screen_watch: starting watcher thread", file=sys.stderr, flush=True)
        self._watcher.start()
        print(f"[DEBUG watch_ctrl] _do_screen_watch: done", file=sys.stderr, flush=True)

    def _on_watch_result(self, text: str, mood: str):
        print(f"[DEBUG watch_ctrl] _on_watch_result called: text_len={len(text)}, mood={mood!r}", file=sys.stderr, flush=True)
        # 清洗 Markdown/引号残留
        import re
        text = re.sub(r'["\'「」『』`]', '', text)
        text = re.sub(r'```', '', text)
        text = text.strip()
        print(f"[DEBUG watch_ctrl] _on_watch_result: cleaned text_len={len(text)}", file=sys.stderr, flush=True)
        try:
            self._pending_reply = (text, mood)
            print(f"[DEBUG watch_ctrl] _on_watch_result: _pending_reply set", file=sys.stderr, flush=True)
            _log_private_text("[watch] _pending_reply 已设置", text)
            voice = ""
            voice_language = ""
            tts_style = ""
            try:
                w = getattr(self, "_watcher", None)
                if w is not None:
                    voice = str(getattr(w, "last_voice_text", "") or "").strip()
                    voice_language = str(
                        getattr(w, "last_voice_language", "") or ""
                    ).strip()
                    tts_style = str(
                        getattr(w, "last_tts_style", "") or ""
                    ).strip()
                    print(f"[DEBUG watch_ctrl] _on_watch_result: read watcher voice fields: voice_len={len(voice)}, lang={voice_language!r}, style_len={len(tts_style)}", file=sys.stderr, flush=True)
                    w.last_voice_text = ""
                    w.last_voice_language = ""
                    w.last_tts_style = ""
                    print(f"[DEBUG watch_ctrl] _on_watch_result: cleared watcher voice fields", file=sys.stderr, flush=True)
            except Exception as e:
                log.error(f"[watch] 取语音元数据失败: {type(e).__name__}")
                print(f"[DEBUG watch_ctrl] _on_watch_result: exception reading voice fields: {type(e).__name__}", file=sys.stderr, flush=True)
            tts = getattr(self, "tts", None)
            tts_enabled = bool(getattr(tts, "enabled", False)) if tts is not None else False
            print(f"[DEBUG watch_ctrl] _on_watch_result: tts={tts is not None}, tts_enabled={tts_enabled}, voice={bool(voice)}, voice_language={bool(voice_language)}", file=sys.stderr, flush=True)
            if (
                tts is None
                or not tts_enabled
                or not voice
                or not voice_language
            ):
                print(f"[DEBUG watch_ctrl] _on_watch_result: showing reply directly (no TTS)", file=sys.stderr, flush=True)
                self.show_reply(
                    text,
                    mood,
                    duration_ms=self.config["bubble_duration_ms"]["watch"],
                )
                set_awaiting_reply_state(self, False)
                self._start_watcher_timer()
                print(f"[DEBUG watch_ctrl] _on_watch_result: done (no TTS)", file=sys.stderr, flush=True)
                return
            print(f"[DEBUG watch_ctrl] _on_watch_result: creating TTSWorker", file=sys.stderr, flush=True)
            self._watch_tts_worker = TTSWorker(
                tts,
                voice,
                mood=mood,
                style=tts_style,
                language=voice_language,
            )
            self._watch_tts_worker.start()
            print(f"[DEBUG watch_ctrl] _on_watch_result: TTSWorker started", file=sys.stderr, flush=True)
            self._ensure_tts_poll()
            print(f"[DEBUG watch_ctrl] _on_watch_result: ensured TTS poll", file=sys.stderr, flush=True)
        except Exception as e:
            log.error(f"[watch] _on_watch_result 异常: {type(e).__name__}" + (f": {e}" if debug_enabled() else ""))
            print(f"[DEBUG watch_ctrl] _on_watch_result: exception in handler: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            self.show_reply(text, mood, duration_ms=self.config["bubble_duration_ms"]["watch"])
            set_awaiting_reply_state(self, False)
            self._start_watcher_timer()
            print(f"[DEBUG watch_ctrl] _on_watch_result: done after exception", file=sys.stderr, flush=True)

    def _on_watch_tts_and_show(self, raw: str, reply: str = None, mood: str = None):
        print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show called: raw={raw is not None}, reply={reply is not None}, mood={mood!r}", file=sys.stderr, flush=True)
        log.info(f"[watch] _on_watch_tts_and_show called, raw={raw is not None}, reply={reply is not None}")
        if raw is None or reply is None:
            log.warning("[TTS] watch tts returned None, skip audio")
            print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: raw or reply is None", file=sys.stderr, flush=True)
            if reply and mood:
                print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: showing reply anyway", file=sys.stderr, flush=True)
                self.show_reply(reply, mood, duration_ms=self.config["bubble_duration_ms"]["watch"])
            else:
                log.warning("[watch] _pending_reply 已丢失!")
                print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: both reply and mood missing", file=sys.stderr, flush=True)
            set_awaiting_reply_state(self, False)
            self._start_watcher_timer()
            print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: done (None case)", file=sys.stderr, flush=True)
            return

        """屏幕吐槽：语音合成完成 → 显示文字 + 播放"""
        wav_path = raw.rsplit("|", 1)[0] if "|" in raw else raw
        print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: wav_path={wav_path!r}", file=sys.stderr, flush=True)
        # reply/mood 由调用方 _poll_tts 直接传入，不再从 _pending_reply 重复读取
        audio_duration_ms = self._get_wav_duration_ms(wav_path) if wav_path else 0
        print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: audio_duration_ms={audio_duration_ms}", file=sys.stderr, flush=True)
        bubble_ms = self.config["bubble_duration_ms"]["watch"]
        if self.config["tts"]["sync_with_audio"]:
            bubble_ms = max(audio_duration_ms + 500, bubble_ms)
            print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: sync_with_audio, adjusted bubble_ms={bubble_ms}", file=sys.stderr, flush=True)
        print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: showing reply with bubble_ms={bubble_ms}", file=sys.stderr, flush=True)
        self.show_reply(reply, mood, duration_ms=bubble_ms)
        set_awaiting_reply_state(self, False)
        self._start_watcher_timer()
        if wav_path and os.path.exists(wav_path):
            print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: playing audio from {wav_path}", file=sys.stderr, flush=True)
            self._play_audio(wav_path)
        else:
            print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: wav_path missing or not exists, skipping play", file=sys.stderr, flush=True)
        print(f"[DEBUG watch_ctrl] _on_watch_tts_and_show: done", file=sys.stderr, flush=True)

    def _on_watch_tts_error(self, err: str):
        """屏幕吐槽 TTS 合成失败 —— 至少显示文字，不卡死"""
        print(f"[DEBUG watch_ctrl] _on_watch_tts_error called: err_len={len(err)}", file=sys.stderr, flush=True)
        _log_private_text("[watch] TTS 合成失败", err)
        log.error(f"[watch] TTS 合成失败: error_chars={len(err or '')}")
        set_awaiting_reply_state(self, False)
        if hasattr(self, '_pending_reply'):
            reply, mood = self._pending_reply
            del self._pending_reply
            print(f"[DEBUG watch_ctrl] _on_watch_tts_error: showing pending reply, text_len={len(reply)}", file=sys.stderr, flush=True)
            self.show_reply(reply, mood, duration_ms=5000)
        else:
            print(f"[DEBUG watch_ctrl] _on_watch_tts_error: no pending reply", file=sys.stderr, flush=True)
        self._start_watcher_timer()
        print(f"[DEBUG watch_ctrl] _on_watch_tts_error: done", file=sys.stderr, flush=True)

    def _on_watch_error(self, err: str):
        print(f"[DEBUG watch_ctrl] _on_watch_error called: err_len={len(err)}", file=sys.stderr, flush=True)
        _log_private_text("[watch] 识图错误", err)
        # 显示简短提示，不打扰主人
        set_awaiting_reply_state(self, False)
        self._show_bubble(f"唔…看不清喵 ({err[:30]})", self.config["bubble_duration_ms"]["default"])
        print(f"[DEBUG watch_ctrl] _on_watch_error: shown bubble", file=sys.stderr, flush=True)
        self._start_watcher_timer()
        print(f"[DEBUG watch_ctrl] _on_watch_error: done", file=sys.stderr, flush=True)

    def _on_watch_silent(self):
        """视觉模型评估后决定不说话——安静恢复"""
        print(f"[DEBUG watch_ctrl] _on_watch_silent called", file=sys.stderr, flush=True)
        set_awaiting_reply_state(self, False)
        self._show_bubble("😼 没什么好说的喵…", self.config["bubble_duration_ms"]["default"])
        print(f"[DEBUG watch_ctrl] _on_watch_silent: shown bubble", file=sys.stderr, flush=True)
        self._start_watcher_timer()
        print(f"[DEBUG watch_ctrl] _on_watch_silent: done", file=sys.stderr, flush=True)

    def _on_watch_progress(self, msg: str):
        """显示识图/评估阶段状态"""
        print(f"[DEBUG watch_ctrl] _on_watch_progress called: msg={msg!r}", file=sys.stderr, flush=True)
        self._show_bubble(msg, 0)  # 持久显示直到下一个阶段
        print(f"[DEBUG watch_ctrl] _on_watch_progress: done", file=sys.stderr, flush=True)

    def _on_search_request(self, query: str):
        """处理 Web 搜索请求（来自 watcher）—— 暂无可用搜索后端"""
        print(f"[DEBUG watch_ctrl] _on_search_request called: query={query!r}", file=sys.stderr, flush=True)
        result = f"（关于「{query}」的搜索结果暂时无法获取喵）"
        if hasattr(self, '_watcher') and self._watcher:
            print(f"[DEBUG watch_ctrl] _on_search_request: setting search result on watcher", file=sys.stderr, flush=True)
            self._watcher.set_search_result(result)
        print(f"[DEBUG watch_ctrl] _on_search_request: done", file=sys.stderr, flush=True)

    def _toggle_watcher_enabled(self):
        """Right-click toggle for screen watch. Cloud needs explicit consent."""
        print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled called", file=sys.stderr, flush=True)
        w = self.config.setdefault("watcher", {
            "enabled": False,
            "allow_cloud": False,
            "require_confirm": True,
        })
        turning_on = not w.get("enabled", False)
        print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: turning_on={turning_on}, current config={w}", file=sys.stderr, flush=True)

        if turning_on and self._is_cloud_vision():
            if not w.get("allow_cloud", False):
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: allow_cloud=False, showing confirm dialog", file=sys.stderr, flush=True)
                q = "\n".join([
                    "当前识图后端会把截图发到云端。",
                    "",
                    "是否授权「允许云端识图」并开启屏幕观察？",
                    "之后每次自动偷看默认仍会再确认一次。",
                ])
                allowed = confirm_cloud_vision(
                    self,
                    title="开启云端屏幕观察？",
                    message=q,
                    timeout_seconds=5,
                    accept_text="允许并开启",
                )
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: user response={allowed}", file=sys.stderr, flush=True)
                if not allowed:
                    self._show_bubble("未开启屏幕观察喵", 2500)
                    print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: user denied, returning", file=sys.stderr, flush=True)
                    return
                w["allow_cloud"] = True
                w["require_confirm"] = True
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: set allow_cloud=True, require_confirm=True", file=sys.stderr, flush=True)
            else:
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: allow_cloud already True, showing confirm dialog", file=sys.stderr, flush=True)
                q = "\n".join([
                    "将定时截屏并上传到云端识别。",
                    "每次上传前默认仍会弹窗确认。",
                    "",
                    "继续开启？",
                ])
                allowed = confirm_cloud_vision(
                    self,
                    title="开启屏幕观察？",
                    message=q,
                    timeout_seconds=5,
                    accept_text="继续开启",
                )
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: user response={allowed}", file=sys.stderr, flush=True)
                if not allowed:
                    self._show_bubble("未开启屏幕观察喵", 2500)
                    print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: user denied, returning", file=sys.stderr, flush=True)
                    return

        w["enabled"] = turning_on
        # Always require per-capture confirm for cloud uploads
        w["require_confirm"] = True
        w["confirm_once_session"] = False
        self._cloud_watch_confirmed = False
        # 统一写入 config.json
        self._save_config()
        print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: config saved, enabled={turning_on}", file=sys.stderr, flush=True)

        if w["enabled"]:
            if self._is_cloud_vision():
                bubble = status_language.watcher_enabled_cloud()
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: showing cloud enabled bubble", file=sys.stderr, flush=True)
            else:
                bubble = status_language.watcher_enabled_local()
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: showing local enabled bubble", file=sys.stderr, flush=True)
            self._show_bubble(bubble, 3500 if self._is_cloud_vision() else 2500)
            self._start_watcher_timer()
            print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: watcher timer started", file=sys.stderr, flush=True)
        else:
            if hasattr(self, "_watcher_timer") and self._watcher_timer:
                self._watcher_timer.stop()
                print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: watcher timer stopped", file=sys.stderr, flush=True)
            self._show_bubble("屏幕观察已关闭喵", 2500)
        print(f"[DEBUG watch_ctrl] _toggle_watcher_enabled: done", file=sys.stderr, flush=True)

