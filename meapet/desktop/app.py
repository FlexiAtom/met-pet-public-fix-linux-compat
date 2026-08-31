"""
梅尔桌宠 - 主程序
透明窗口 + 拖拽移动 + 表情切换 + 对话气泡
"""
from __future__ import annotations

import os
import sys
import time

# socket 必须在 PyQt 之前导入（仅 Windows 需要，QtNetwork hook 顺序问题）。
# Linux 上无此要求，但导入本身无害，仅在 Windows 上注释提醒。
if sys.platform == "win32":
    import socket  # must import before PyQt (QtNetwork hook, Windows only)

# 在所有导入之前开启终端 VT 转译支持
# 确保后续的 get_color_logger 和 logging 模块能正确输出彩色日志
try:
    from meapet.log import enable_vt
    enable_vt()
except Exception:
    pass
from meapet.log import get_color_logger, log_text_attachments

log = get_color_logger("app")

from meapet.paths import PROJECT_ROOT
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from typing import Optional

from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QSurfaceFormat

from meapet.utils import (
    safe_print,
    log_error,
    ensure_utf8_stdout,
    cleanup_audio_cache,
)
from meapet.config.store import (
    load_config,
    normalize_config,
    resolve_startup_config_path,
    resolve_vision_api_base,
    resolve_vision_api_key,
    resolve_vision_backend,
)
from meapet.config.defaults import DEFAULT_OLLAMA_VISION_MODEL
from meapet.config.checker import check_config_lines
from meapet.window_state import state_path_for_config

ensure_utf8_stdout()

from meapet.chat.engine import create_engine_from_config
from meapet.memory.db import MeaMemory
from meapet.watcher.screen import ScreenWatcher
from meapet.tts.service import MeaTTS
from meapet.desktop.widgets import DialogueBubbleStack
from meapet.desktop.audio import PetAudioMixin
from meapet.desktop.watch_ctrl import PetWatcherMixin
from meapet.desktop.chat_flow import PetChatFlowMixin
from meapet.desktop.control_bridge import PetControlBridgeMixin
from meapet.desktop.interaction import PetInteractionMixin
from meapet.desktop.window_chrome import PetWindowChromeMixin
from meapet.desktop.render_host import PetRenderHostMixin, calculate_drag_position
from meapet.desktop.config_bridge import PetConfigBridgeMixin
from meapet.desktop.voice_mixin import PetVoiceMixin
from meapet.desktop.splash import StartupSplash
from meapet.desktop import status_language

# Windows 专用多媒体后端偏好：仅 Windows 上设置，Linux/macOS 不需要。
if sys.platform == "win32":
    os.environ.setdefault("QT_MULTIMEDIA_PREFERRED_PLUGINS", "windowsmediafoundation")

_WEBSOCKETS_AVAILABLE = None


def _websockets_available() -> bool:
    """websockets 是否可导入（结果缓存，仅探测不执行模块代码）。"""
    global _WEBSOCKETS_AVAILABLE
    if _WEBSOCKETS_AVAILABLE is None:
        try:
            import importlib.util

            _WEBSOCKETS_AVAILABLE = (
                importlib.util.find_spec("websockets") is not None
            )
        except (ImportError, ValueError):
            _WEBSOCKETS_AVAILABLE = False
    return _WEBSOCKETS_AVAILABLE



def _install_excepthook():
    import traceback

    def _hook(exc_type, exc, tb):
        msg = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error(f"[excepthook] 未捕获异常: {exc_type.__name__}: {exc}\n{msg}")
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _hook


def _abort_failed_startup(app, keepalive, splash=None) -> None:
    """主窗口创建失败时关闭保活对象并结束应用。"""
    for widget in (splash, keepalive):
        if widget is None:
            continue
        try:
            widget.close()
        except Exception:
            pass
    try:
        app.quit()
    except Exception:
        pass


class MeaPet(
    PetAudioMixin,
    PetWatcherMixin,
    PetVoiceMixin,
    PetChatFlowMixin,
    PetControlBridgeMixin,
    PetInteractionMixin,
    PetWindowChromeMixin,
    PetRenderHostMixin,
    PetConfigBridgeMixin,
    QWidget,
):
    """梅尔桌宠主窗口"""

    def __init__(
        self,
        config_path: Optional[str] = None,
        window_state_path: Optional[str] = None,
    ):
        super().__init__()
        config_path = config_path or resolve_startup_config_path(PROJECT_ROOT)
        self.config = self._load_config(config_path)
        self._window_state_path = window_state_path or state_path_for_config(
            self._config_path,
            "pet_window_state.json",
        )
        if not check_config_lines(config_path):
            # bubble not ready yet; defer message
            self._config_broken = True
        else:
            self._config_broken = False

        self.config = normalize_config(self.config)
        from meapet.ui_theme import resolve_reduced_motion, apply_reduced_motion_env, set_ui_font_scale

        set_ui_font_scale(
            (self.config.get("display") or {}).get("font_scale", 1.0)
        )
        self._apply_motion_preference()
        bub = self.config.get("bubble_duration_ms") or {}
        log.info(
            f"[config] 气泡时长配置: "
            f"default={bub.get('default')} reply={bub.get('reply')} "
            f"watch={bub.get('watch')} interaction={bub.get('interaction')} "
            f"sync_with_audio={self.config.get('tts', {}).get('sync_with_audio')}"
        )

        self._awaiting_reply = False
        self._pending_input = None
        self._chat_worker = None
        self._tts_worker = None
        self._dragging = False
        self._drag_pointer_origin = None
        self._drag_window_origin = None
        self._pending_drag_position = None
        self._drag_move_timer = QTimer(self)
        self._drag_move_timer.setSingleShot(True)
        self._drag_move_timer.setTimerType(Qt.PreciseTimer)
        self._drag_move_timer.timeout.connect(self._flush_drag_position)
        self._standby = False
        self._standby_bubble = None
        # Standby click-through (native pass-through + right-click poll).
        from meapet.desktop.click_through import ClickThroughState, RightClickEdgeDetector

        self._click_through_state = ClickThroughState()
        self._standby_rc_timer = None
        self._standby_rc_detector = RightClickEdgeDetector()
        self._standby_menu_open = False
        self._qt_transparent_for_input = False

        self._init_window()

        def _safe(step, fn):
            try:
                fn()
                log.info(f"[init] {step} 初始化完成")
            except Exception as e:
                import traceback
                log.error(f"[init] {step} 初始化失败: {e}")
                log.error(f"[init] {step} 异常堆栈:\n{traceback.format_exc()}")

        _safe("renderer", self._init_renderer)
        _safe("chat", self._init_chat)
        _safe("tts", self._init_tts)
        _safe("control", self._init_control)
        self._cloud_watch_confirmed = False
        _safe("watcher", self._init_watcher)
        _safe("voice", self._init_voice)
        _safe("tray", self._setup_tray)
        _safe("interaction", self._init_interaction)
        _safe("timers", self._init_timers)
        _safe("screen", self._init_screen_guard)

        try:
            self._place_initial_position()
        except Exception as e:
            log.warning(f"[init] 窗口定位失败: {e}")
        self.show()
        self.raise_()
        try:
            self._apply_hit_region()
        except Exception as e:
            log.warning(f"[init] 碰撞区域设置失败: {e}")

        try:
            from meapet.paths import data_path
            cache_dir = data_path("audio_cache")
            stats = cleanup_audio_cache(cache_dir, max_files=40, max_age_hours=48.0)
            if stats.get("removed"):
                log.info(
                    f"[audio_cache] 缓存清理完成: removed={stats['removed']} kept={stats['kept']}"
                )
        except Exception as e:
            log.warning(f"[audio_cache] 缓存清理跳过: {e}")

        if self._config_broken:
            QTimer.singleShot(800, lambda: self._show_bubble(
                status_language.config_corrupt(), 5000))

    def _init_window(self):
        # 注意：不要用 SubWindow（无父窗口时在部分 Windows 上会"存在但不可见/无任务栏"）
        self.setWindowFlags(
            Qt.FramelessWindowHint

            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 允许激活，避免完全无法交互
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setAttribute(Qt.WA_AlwaysStackOnTop, True)

        # _NET_WM_WINDOW_TYPE 是 X11/Wayland 专用的 EWMH 提示，用于 Linux 窗口管理器
        # 正确分类窗口类型。Windows/macOS 上 Qt 会自动忽略，但仅在 Linux 上有意义。
        if sys.platform.startswith("linux"):
            self.setProperty("_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_UTILITY")

        self.setWindowTitle("mea-pet")
        # 桌宠是 Tool 悬浮窗：关闭/隐藏时不要拖垮整个 QApplication
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self._bubble_stack = DialogueBubbleStack(self)
        self._bubble_stack.changed.connect(self._on_bubble_stack_changed)
        self.bubble = None

    def _init_chat(self):
        if getattr(self, "memory", None) is None:
            self.memory = MeaMemory()
            self._schedule_memory_maintenance()
        from meapet.conversation.timeline import ConversationTimeline

        llm_config = self.config.get("llm") or {}
        mode = str(llm_config.get("mode") or "direct").strip().lower()
        if mode == "agent" and not _websockets_available():
            # Agent 模式全靠 WebSocket 传输。发行包漏打 websockets 时降级直连，
            # 至少让桌宠开得起来、聊得动。只改本次运行，绝不回写用户配置。
            log.error(
                "[boot] websockets 不可用，本次运行降级为 direct 模式；"
                "用户配置保持不变"
            )
            mode = "direct"
        ui_config = self.config.get("ui") or {}
        try:
            timeline_turns = int(ui_config.get("timeline_turns", 5))
        except (TypeError, ValueError):
            timeline_turns = 5
        timeline_turns = max(0, min(timeline_turns, 100))

        def persist_turn(turn):
            try:
                # 从 turn 对象中提取 text_attachments（由 chat_flow 附加到 turn 上），
                # 以脱敏方式记录日志，并传递给 save_conversation_turn 持久化
                # （db.py 的 save_conversation_turn 已支持 text_attachments 参数，
                # 仅存储 file_name + sha256，不存全文）。
                atts = getattr(turn, "text_attachments", None) or []
                if atts:
                    # 脱敏日志：仅记录文件名/字符数/sha256 前 8 位，不记录全文
                    log_text_attachments(
                        log, "INFO", atts,
                        extra_msg="timeline 持久化"
                    )
                self.memory.save_conversation_turn(
                    turn,
                    max_turns=timeline_turns,
                    text_attachments=atts,
                )
            except Exception as exc:
                log.warning(
                    f"[timeline] 保存最近对话失败: {type(exc).__name__}"
                )

        if not hasattr(self, "_conversation_timeline"):
            self._conversation_timeline = ConversationTimeline(
                timeline_turns,
                terminal_callback=persist_turn,
            )
        else:
            self._conversation_timeline.set_max_turns(timeline_turns)
            self._conversation_timeline.set_terminal_callback(persist_turn)
        if not getattr(self, "_conversation_timeline_loaded", False):
            try:
                for turn in self.memory.load_conversation_turns():
                    self._conversation_timeline.restore(turn)
                self._conversation_timeline_loaded = True
            except Exception as exc:
                log.warning(
                    f"[timeline] 恢复最近对话失败: {type(exc).__name__}"
                )
        self._agent_history = []
        self._agent_tts_workers = {}
        self._agent_bubbles = {}
        self._active_agent_turn_id = ""

        if mode == "agent":
            from meapet.agent.factory import create_agent_adapter_from_config

            agent_config = llm_config.get("agent") or {}
            previous_scope = (
                str(agent_config.get("session_id") or ""),
                str(agent_config.get("session_key") or ""),
                str(agent_config.get("device_id") or ""),
            )
            self.chat_engine = None
            self.agent_adapter = create_agent_adapter_from_config(self.config)
            current_agent_config = (self.config.get("llm") or {}).get("agent") or {}
            current_scope = (
                str(current_agent_config.get("session_id") or ""),
                str(current_agent_config.get("session_key") or ""),
                str(current_agent_config.get("device_id") or ""),
            )
            if current_scope != previous_scope:
                self._save_config()
        else:
            self.agent_adapter = None
            self.chat_engine = create_engine_from_config(self.config, self.memory)
            if self.chat_engine.available:
                QTimer.singleShot(2000, self._show_warmup_status)
        conversation_key = self._refresh_conversation_key()
        if mode == "agent":
            agent_config = llm_config.get("agent") or {}
            try:
                history_turns = max(
                    0,
                    min(int(agent_config.get("history_turns", 5)), 100),
                )
            except (TypeError, ValueError):
                history_turns = 5
            self._agent_history = list(
                self._conversation_timeline.history(
                    conversation_key,
                    max_turns=history_turns,
                )
            )
        else:
            restored_history = list(
                self._conversation_timeline.history(
                    conversation_key,
                    max_turns=7,
                )
            )
            if restored_history and self.chat_engine is not None:
                lock = getattr(self.chat_engine, "_history_lock", None)
                if lock is None:
                    system = self.chat_engine.history[0]
                    self.chat_engine.history = [system] + restored_history
                else:
                    with lock:
                        system = self.chat_engine.history[0]
                        self.chat_engine.history = [system] + restored_history
        QTimer.singleShot(1200, self._maybe_show_first_run_hint)

    def _apply_motion_preference(self) -> None:
        """合并配置 / 环境 / 系统启发式，同步到 MEA_PET_REDUCED_MOTION。"""
        from meapet.ui_theme import apply_reduced_motion_env, resolve_reduced_motion

        reduced = resolve_reduced_motion(
            (self.config.get("display") or {}).get("reduced_motion", None)
        )
        apply_reduced_motion_env(reduced)

    def _maybe_show_first_run_hint(self) -> None:
        """首次启动一次性提示；写入 config.ui.first_run_hint_shown。"""
        ui = self.config.setdefault("ui", {})
        if ui.get("first_run_hint_shown"):
            return
        self._show_bubble(status_language.first_run_hint(), 4500)
        ui["first_run_hint_shown"] = True
        try:
            self._save_config()
        except Exception:
            pass

    def _schedule_memory_maintenance(self):
        """将生命周期维护放入后台线程，不阻塞启动"""
        if hasattr(self, '_maintenance_done') and self._maintenance_done:
            return
        self._maintenance_done = False
        import threading
        def _run():
            try:
                self.memory.lifecycle_maintenance()
                log.info("[memory] 后台生命周期维护完成")
            except Exception as e:
                log.error(f"[memory] 后台维护异常: {e}")
            finally:
                self._maintenance_done = True
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        log.info("[memory] 后台生命周期维护已调度")

    def _show_warmup_status(self):
        if getattr(self.chat_engine, "_warmed_up", False):
            self._show_bubble(status_language.ready_hint(), 3000)

    def _init_tts(self):
        self.tts = MeaTTS(self.config)

    def _init_watcher(self):
        llm_cfg = self.config.get("llm", {}) or {}
        vision_cfg = self.config.get("vision", {}) or {}
        vision_mode = str(vision_cfg.get("mode") or "disabled").strip().lower()

        api_key = resolve_vision_api_key(vision_cfg, llm_cfg)
        backend = resolve_vision_backend(vision_cfg, llm_cfg)
        # 上传目标与云端确认共用同一解析：ollama 只走 host，mimo 走 api_base
        api_base = resolve_vision_api_base(vision_cfg, llm_cfg)
        vision_model = vision_cfg.get("model") or DEFAULT_OLLAMA_VISION_MODEL

        log.info(
            f"[watcher] 视觉路由: mode={vision_mode} backend={backend} "
            f"model={vision_model} "
            f"allow_cloud={self.config.get('watcher', {}).get('allow_cloud', False)}"
        )
        self._watcher = ScreenWatcher(
            api_base=api_base,
            vision_model=vision_model,
            chat_model=vision_model,
            api_key=api_key,
            backend=backend,
            mode=vision_mode,
            capture_scope="full_screen",
            capture_region=None,
            capture_application="",
        )

        self._watcher.result_ready.connect(self._on_watch_result)
        self._watcher.error.connect(self._on_watch_error)
        self._watcher.silent.connect(self._on_watch_silent)
        self._watcher.progress.connect(self._on_watch_progress)
        self._watcher.search_request.connect(self._on_search_request)
        self._last_interaction_time = time.time()

    def _init_interaction(self):
        self._last_interaction_time = time.time()
        self._head_press_x = None
        self._is_head_touching = False

    def _init_timers(self):
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._idle_action)
        self._idle_timer.start(20000)

        self._watcher_timer = QTimer(self)
        self._watcher_timer.timeout.connect(self._do_screen_watch)
        watcher_cfg = self.config.get("watcher", {})
        if watcher_cfg.get("enabled", False):
            self._start_watcher_timer()
        else:
            log.info("[watcher] 屏幕观察默认关闭（隐私），右键菜单可开启")

    # ── mouse ──────────────────────────────────────────
    def mousePressEvent(self, event):
        # 待机：左键等交互全部忽略（穿透由原生后端负责；右键走轮询菜单）。
        if getattr(self, "_standby", False):
            return
        if event.button() == Qt.LeftButton:
            head_threshold = int(self.height() * 0.35)
            self._is_head_touching = event.y() < head_threshold
            self._head_press_x = event.x() if self._is_head_touching else None
            self._dragging = True
            self._drag_pointer_origin = event.globalPos()
            self._drag_window_origin = self.pos()

    def mouseMoveEvent(self, event):
        if getattr(self, "_standby", False):
            return
        if not (self._dragging and event.buttons() & Qt.LeftButton):
            return
        if self._is_head_touching and self._head_press_x is not None:
            if abs(event.x() - self._head_press_x) > 40:
                self._on_head_patted()
                self._is_head_touching = False
                self._head_press_x = None
                return
        if self._drag_pointer_origin is None or self._drag_window_origin is None:
            return
        self._queue_drag_position(
            calculate_drag_position(
                self._drag_window_origin,
                self._drag_pointer_origin,
                event.globalPos(),
            )
        )

    def _queue_drag_position(self, position):
        """合并高频 move 事件，同时保留首个事件的即时反馈。"""
        self._pending_drag_position = position
        if not self._drag_move_timer.isActive():
            self._flush_drag_position()
            self._drag_move_timer.start(8)

    def _flush_drag_position(self):
        position = self._pending_drag_position
        if position is None:
            return
        self._pending_drag_position = None
        self.move(position)
        self._position_bubble()

    def mouseReleaseEvent(self, event):
        if getattr(self, "_standby", False):
            self._dragging = False
            self._drag_pointer_origin = None
            self._drag_window_origin = None
            self._is_head_touching = False
            self._head_press_x = None
            return
        self._drag_move_timer.stop()
        self._flush_drag_position()
        self._dragging = False
        self._drag_pointer_origin = None
        self._drag_window_origin = None
        self._is_head_touching = False
        self._head_press_x = None
        try:
            self._save_pet_position()
        except Exception:
            pass

    def mouseDoubleClickEvent(self, event):
        if getattr(self, "_standby", False):
            return
        self._start_chat()

    def showEvent(self, event):
        """窗口显示时恢复空闲动画等后台定时器"""
        super().showEvent(event)
        if hasattr(self, '_idle_timer') and self._idle_timer and not self._idle_timer.isActive():
            self._idle_timer.start(20000)
        # 待机穿透可能因 hide/show 或 HWND 重建失效，显示时重新确保。
        if getattr(self, "_standby", False) and not getattr(self, "_standby_menu_open", False):
            ensure = getattr(self, "_ensure_standby_click_through", None)
            if callable(ensure):
                ensure()

    def closeEvent(self, event):
        # 桌宠是常驻悬浮窗：系统/误触关闭只隐藏，真正退出走右键「退出」
        log.info("[pet] 关闭事件触发 -> 隐藏窗口（使用托盘或右键菜单退出）")
        event.ignore()
        if hasattr(self, '_idle_timer') and self._idle_timer:
            self._idle_timer.stop()
        self.hide()

    # ── Standby click-through integration ──────────────────────────────
    # 将重构后的 meapet.desktop.click_through API 接入主窗口：
    #   * enable_click_through 现在接受 QWidget（我们直接传 self）
    #   * set_shape_region 用于 Live2D 角色动画的动态异形命中区
    #   * 右键轮询在 Wayland 下恒返回 False，需改用 Qt 鼠标事件
    #     （RightClickEdgeDetector.update 可在 mousePressEvent 中调用）

    def _apply_hit_region(self) -> None:
        """同步当前窗口几何到原生点击穿透后端。

        在 init 末尾与每次几何变化后调用。现在走重构后的统一 API：
        传 QWidget 对象（不再手动 winId()），让后端自行解析句柄，
        并为 Wayland 后端保留活着的 QWindow 引用。
        """
        state = getattr(self, "_click_through_state", None)
        if state is None or not getattr(state, "active", False):
            return
        # 动态异形：以整个窗口作为"保留输入区"，等价于旧版的 full ShapeInput。
        # 若后续 Live2D 角色需要像素级轮廓跟随，可在此处根据模型包围盒
        # 构造 [(x, y, w, h), ...] 传入 set_shape_region。
        try:
            from meapet.desktop.click_through import set_shape_region
            rects = [(0, 0, max(1, self.width()), max(1, self.height()))]
            set_shape_region(state, rects)
        except Exception as exc:
            log.warning(f"[standby] set_shape_region 失败: {type(exc).__name__}")

    def _ensure_standby_click_through(self) -> None:
        """进入待机时开启 OS 级鼠标穿透；退出时恢复。

        由 showEvent / 待机切换逻辑通过 getattr 可选调用，因此即使未
        接通待机开关也保持安全（不会 AttributeError）。
        """
        from meapet.desktop.click_through import (
            ClickThroughState,
            disable_click_through,
            enable_click_through,
        )

        state = getattr(self, "_click_through_state", None)
        if state is None:
            self._click_through_state = ClickThroughState()
            state = self._click_through_state

        standby = bool(getattr(self, "_standby", False))
        menu_open = bool(getattr(self, "_standby_menu_open", False))

        if standby and not menu_open:
            # 已激活则仅刷新形状；否则启用穿透（传 self = QWidget）
            if not state.active:
                new_state = enable_click_through(
                    self,
                    width=self.width(),
                    height=self.height(),
                )
                self._click_through_state = new_state
                state = new_state
                self._qt_transparent_for_input = state.active
                if state.active:
                    log.info(
                        f"[standby] 点击穿透已启用 backend={state.backend}"
                    )
                else:
                    log.warning("[standby] 点击穿透未启用（后端不可用）")
            self._apply_hit_region()
            self._start_standby_right_click_poll()
        else:
            if state.active:
                disable_click_through(state)
                self._click_through_state = ClickThroughState()
                self._qt_transparent_for_input = False
                log.info("[standby] 点击穿透已恢复")
            self._stop_standby_right_click_poll()

    def _start_standby_right_click_poll(self) -> None:
        """启动右键轮询定时器（win32/x11 后端需要）。

        Wayland 后端下 is_right_button_down 恒返回 False，轮询无效，
        此时应改为在 mousePressEvent 里用 RightClickEdgeDetector.update
        检测右键上升沿。这里仍启动定时器以兼容其他后端。
        """
        if getattr(self, "_standby_rc_timer", None) is not None:
            return
        from PyQt5.QtCore import QTimer

        timer = QTimer(self)
        timer.setTimerType(Qt.PreciseTimer)

        def _poll():
            if not getattr(self, "_standby", False):
                return
            # 函数内导入：保证 mock.patch 在调用时生效
            from meapet.desktop.click_through import (
                is_right_button_down,
                platform_backend_name,
            )
            if platform_backend_name() == "wayland":
                # Wayland 无全局指针查询，交由 Qt 事件流处理
                return
            down = bool(is_right_button_down())
            if down:
                self._on_standby_right_click()
            else:
                # 松开后复位边沿检测，保证下次按下仍能触发
                detector = getattr(self, "_standby_rc_detector", None)
                if detector is not None:
                    detector.reset()

        timer.timeout.connect(_poll)
        timer.start(50)
        self._standby_rc_timer = timer

    def _stop_standby_right_click_poll(self) -> None:
        timer = getattr(self, "_standby_rc_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            self._standby_rc_timer = None

    def _on_standby_right_click(self) -> None:
        """右键上升沿回调：在待机穿透开启时唤起上下文菜单。"""
        detector = getattr(self, "_standby_rc_detector", None)
        if detector is not None:
            # cursor_in_pet 近似为 True（穿透下鼠标本就不在宠物上，
            # 但右键被轮询捕获说明已进入命中区），此处直接视为触发。
            if not detector.update(cursor_in_pet=True, button_down=True):
                return
        self._standby_menu_open = True
        self._ensure_standby_click_through()  # 恢复输入以便菜单交互
        try:
            self._show_context_menu()
        finally:
            self._standby_menu_open = False



def _ensure_jieba():
    """预检 jieba 依赖，缺失时弹出错误提示。

    必须在 QApplication 创建之后调用：show_message_dialog 会构造 QDialog，
    而在 QApplication 之前创建任何 QWidget 会触发 Qt 的 qFatal -> abort()，
    Python 层的 except 拦不住，窗口化打包下表现为静默猝死。
    """
    try:
        import jieba  # noqa: F401
    except ImportError:
        log.error("[boot] jieba 未安装，中文分词功能不可用")
        message = (
            "缺少核心依赖 jieba（中文分词库）。\n\n"
            "请在终端执行：\n"
            "  pip install jieba\n\n"
            "或使用 uv：\n"
            "  uv pip install jieba\n\n"
            "然后重新启动桌宠。"
        )
        shown = False
        try:
            from PyQt5.QtWidgets import QApplication as _QApp, QMessageBox
            from meapet.message_dialog import show_message_dialog

            if _QApp.instance() is not None:
                show_message_dialog(
                    None,
                    title="依赖缺失",
                    text=message,
                    icon=QMessageBox.Critical,
                )
                shown = True
        except Exception:
            pass
        if not shown:
            # Qt 不可用时也要让用户看到原因，而不是静默退出。
            try:
                from meapet.bootstrap import emit_startup_error

                emit_startup_error(f"[MeaPet] 启动失败：\n\n{message}")
            except Exception:
                pass
        raise SystemExit(1)


def main():
    """启动桌宠：托盘 + 屏外保活 + boot 日志。"""
    _install_excepthook()
    import signal
    import traceback
    from datetime import datetime
    from pathlib import Path as _Path

    # Native crash log (OpenGL / Live2D C++ faults).
    try:
        import faulthandler
        from meapet.paths import get_data_dir
        _fault_fp = open(_Path(get_data_dir()) / "meapet_fault.log", "a", encoding="utf-8")
        faulthandler.enable(file=_fault_fp, all_threads=True)
    except Exception:
        pass

    signal.signal(signal.SIGINT, signal.SIG_DFL)

    from meapet.paths import get_data_dir, migrate_legacy_home_data
    boot_log = _Path(get_data_dir()) / "meapet_boot.log"

    try:
        boot_log.write_text(
            f"===== MeaPet boot {datetime.now().isoformat(timespec='seconds')} =====\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    try:
        for note in migrate_legacy_home_data():
            log.info(f"[boot] {note}")
    except Exception as exc:
        log.warning(f"[boot] legacy data migration skipped: {exc}")

    log.info(f"[boot] python={sys.version.split()[0]} exe={sys.executable}")
    log.info(f"[boot] cwd={os.getcwd()} root={PROJECT_ROOT} data={get_data_dir()}")
    log.info(f"[boot] FORCE_PNG={os.environ.get('MEA_PET_FORCE_PNG', '')}")

    # Live2D 透明窗：在 QApplication 之前请求 alpha+stencil，并在 Windows 上优先
    # 桌面 OpenGL，减轻打包后 ANGLE/软 GL 把 QOpenGLWidget 合成成不透明矩形。
    try:
        if sys.platform == "win32":
            QApplication.setAttribute(Qt.AA_UseDesktopOpenGL, True)
        gl_fmt = QSurfaceFormat()
        gl_fmt.setAlphaBufferSize(8)
        gl_fmt.setStencilBufferSize(8)
        gl_fmt.setRenderableType(QSurfaceFormat.OpenGL)
        QSurfaceFormat.setDefaultFormat(gl_fmt)
    except Exception as exc:
        log.warning(f"[boot] OpenGL surface defaults skipped: {exc}")

    try:
        app = QApplication(sys.argv)
    except Exception:
        log.error(f"[boot] QApplication 创建失败:\n{traceback.format_exc()}")
        raise

    # QApplication 就绪后才能安全弹 Qt 对话框。
    _ensure_jieba()

    # 子系统依赖降级只记日志，不阻断启动。
    try:
        from meapet.bootstrap import (
            degraded_dependencies,
            format_degraded_dependencies,
        )

        _degraded = degraded_dependencies()
        if _degraded:
            log.warning(f"[boot] {format_degraded_dependencies(_degraded)}")
    except Exception:
        pass

    from meapet.ui_theme import ensure_application_fonts, resolve_reduced_motion, apply_reduced_motion_env, set_ui_font_scale

    ensure_application_fonts()

    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("MeaPet")
    app.setOrganizationName("MeaPet")
    app.aboutToQuit.connect(lambda: log.info("[boot] 应用即将退出 (aboutToQuit)"))

    holder: dict = {"pet": None}
    app._meapet_holder = holder

    # 屏外保活窗（避免仅托盘时进程被过早回收；不可见）
    keepalive = QWidget()
    keepalive.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.Tool)
    keepalive.setAttribute(Qt.WA_QuitOnClose, False)
    keepalive.setAttribute(Qt.WA_ShowWithoutActivating, True)
    keepalive.setWindowTitle("MeaPet-keepalive")
    keepalive.setFixedSize(1, 1)
    keepalive.move(-10000, -10000)
    keepalive.show()
    app._meapet_keepalive = keepalive
    log.info("[boot] 保活窗口就绪")

    config_path = resolve_startup_config_path(PROJECT_ROOT)
    if os.path.basename(config_path) == "config.example.json":
        log.info("[boot] 使用默认示例配置文件 config.example.json")

    # 可选启动页（失败忽略）
    splash = None
    try:
        splash = StartupSplash()
        splash.setAttribute(Qt.WA_QuitOnClose, False)
        if hasattr(splash, "status"):
            splash.status.setText("正在启动...")
        splash.show()
        app.processEvents()
    except Exception as e:
        log.warning(f"[boot] 启动页跳过: {e}")
        splash = None

    pet = None
    try:
        log.info("[boot] 正在创建 MeaPet 实例...")
        pet = MeaPet(config_path)
        holder["pet"] = pet
        app._meapet_pet = pet
        pet.show()
        pet.raise_()
        log.info(
            f"[boot] MeaPet 创建成功: "
            f"size={pet.width()}x{pet.height()} "
            f"pos=({pet.x()},{pet.y()}) vis={pet.isVisible()} "
            f"live2d={getattr(pet, '_use_live2d', None)} tray={getattr(pet, 'tray', None) is not None}"
        )
    except Exception:
        tb = traceback.format_exc()
        log.error(f"[boot] MeaPet 创建失败:\n{tb}")
        try:
            from PyQt5.QtWidgets import QMessageBox
            from meapet.message_dialog import show_message_dialog

            show_message_dialog(
                None,
                title="MeaPet 启动失败",
                text=tb[-1200:],
                icon=QMessageBox.Critical,
            )
        except Exception:
            pass
        _abort_failed_startup(app, keepalive, splash)
        return 1

    if splash is not None:
        try:
            splash.hide()
        except Exception:
            pass

    def _ensure_visible():
        pet2 = holder.get("pet")
        if pet2 is None:
            return
        try:
            if hasattr(pet2, "_place_bottom_right"):
                pet2._place_bottom_right()
            pet2.show()
            pet2.raise_()
            log.debug(
                f"[boot] 确保窗口可见: "
                f"size={pet2.width()}x{pet2.height()} "
                f"@({pet2.x()},{pet2.y()}) vis={pet2.isVisible()}"
            )
        except Exception as e:
            log.warning(f"[boot] 确保窗口可见失败: {e}")

    def _greet():
        try:
            pet2 = holder.get("pet")
            if pet2 is not None and hasattr(pet2, "show_reply"):
                pet2.show_reply("......", "neutral")
        except Exception as e:
            log.warning(f"[boot] 问候消息跳过: {e}")

    startup_finished = {"done": False}

    def _finish_startup():
        if startup_finished["done"]:
            return
        startup_finished["done"] = True
        if splash is not None:
            try:
                splash.hide()
            except Exception:
                pass
        pet2 = holder.get("pet")
        if pet2 is not None:
            try:
                # 几何位置已在构造阶段确定，这里只显现，不再二次定位。
                pet2.show()
                pet2.raise_()
                log.debug(
                    f"renderer ready size={pet2.width()}x{pet2.height()} "
                    f"@({pet2.x()},{pet2.y()}) vis={pet2.isVisible()} "
                    f"opacity={pet2.windowOpacity():.2f} mapping=continuous"
                )
            except Exception as exc:
                log.error(f"渲染器显示失败: {exc}")
        QTimer.singleShot(600, _greet)

    if hasattr(pet, "when_renderer_ready"):
        pet.when_renderer_ready(_finish_startup)
    else:
        _finish_startup()

    heartbeat = QTimer()
    beats = {"n": 0}

    def _beat():
        beats["n"] += 1
        pet2 = holder.get("pet")
        vis = pet2.isVisible() if pet2 is not None else None
        if beats["n"] <= 5 or beats["n"] % 30 == 0:
            log.debug(f"[heartbeat] #{beats['n']} pet_visible={vis}")

    heartbeat.timeout.connect(_beat)
    heartbeat.start(1000)
    app._meapet_heartbeat = heartbeat

    log.info("[boot] 进入事件循环 app.exec_()")
    code = app.exec_()
    log.info(f"[boot] 事件循环退出 code={code}")
    try:
        from meapet.desktop.live2d_widget import dispose_live2d
        dispose_live2d()
    except Exception:
        pass
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main() or 0)
    except SystemExit:
        raise
    except Exception:
        import traceback
        msg = traceback.format_exc()
        log.error(f"[fatal] 启动阶段未捕获异常:\n{msg}")
        # input() 暂停仅在 Windows 终端打包场景下有意义；
        # Linux/macOS 直接从终端启动，崩溃信息自然可见。
        if sys.platform == "win32":
            try:
                input("启动失败，按回车退出...")
            except Exception:
                pass
        raise


