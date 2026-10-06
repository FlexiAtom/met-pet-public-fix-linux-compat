"""开发者选项 —— 版本号彩蛋解锁的本地调试入口（人工 2026-10-06 点定的五项）。

三条口径写在这里，别处不复述：
- 解锁**不跨启动保留、也不落配置键**：彩蛋的语义就是"每次启动重新解锁"，
  新增配置键等于把一次性的手势变成长期状态。
- 五项全部**本地**：只读盘、只写盘、只用已有的录音/合成链路，不新增任何运行时外发。
- 未解锁时「开发者选项」这一支**压根不存在**（不是禁用摆在那儿）。
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
import zipfile
from datetime import datetime

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QAction, QMenu

from meapet import __version__
from meapet.log import get_color_logger

log = get_color_logger("dev")

DEV_UNLOCK_CLICKS = 5
DEV_UNLOCK_WINDOW_S = 10.0
#: 版本号那一发点了不该关窗：菜单一次只活一轮，关窗会把"连点 5 次"变成"开 5 次菜单"。
#: `menu_window.PetMenuWindow` 认这个 objectName。
VERSION_ACTION_NAME = "VersionAction"
#: 导出落点（相对数据目录）——与日志同侧，不往用户没预期的地方写。
EXPORTS_DIR = "exports"
DEV_TEST_SENTENCE = "开发者选项测试：你好，我是梅尔。"
DEV_VOICE_SECONDS = 4
# 日志导出里除了 LOG_DIR，还想要这几个（同属数据目录，散在旁边）
DATA_LOG_FILES = ("chat_errors.log", "meapet_boot.log", "meapet_fault.log")
#: 解锁后「设置与数据」里那一支的五项，顺序即菜单顺序（测试按它断言）。
DEV_MENU_LABELS = (
    f"语音输入测试（录 {DEV_VOICE_SECONDS} 秒）",
    "朗读测试",
    "打印 fidus 屏幕信息",
    "导出日志（zip）",
    "导出配置（去密钥）",
)


def version_action_text() -> str:
    return f"版本 {__version__}"


class PetDevOptionsMixin:
    """挂在 MeaPet 上的开发者选项。

    要求 self 具备：`config`、`_show_bubble`、`_save_config`（导出只读 config，不写）。
    """

    _dev_unlocked = False
    _dev_click_times: tuple = ()

    # ------------------------------------------------------------------ 解锁
    def _dev_build_version_action(self) -> QAction:
        action = QAction(version_action_text(), self)
        action.setObjectName(VERSION_ACTION_NAME)
        action.setToolTip("连点 5 次可解锁开发者选项")
        action.triggered.connect(self._dev_note_version_click)
        return action

    def _dev_note_version_click(self) -> None:
        """10 秒窗口内点满 5 次才解锁；窗口外的点击各算各的。"""
        now = time.monotonic()
        clicks = [
            stamp for stamp in getattr(self, "_dev_click_times", ())
            if now - float(stamp) < DEV_UNLOCK_WINDOW_S
        ]
        if getattr(self, "_dev_unlocked", False):
            self._dev_click_times = ()
            return
        clicks.append(now)
        self._dev_click_times = clicks
        left = DEV_UNLOCK_CLICKS - len(clicks)
        if left <= 0:
            self._dev_unlocked = True
            self._dev_click_times = ()
            self._dev_say("开发者选项已解锁（本次运行有效）")
            return
        self._dev_say(f"再点 {left} 次解锁开发者选项")

    def _dev_say(self, text: str) -> None:
        try:
            self._show_bubble(text, mood=None)
        except Exception:
            pass

    # ------------------------------------------------------------------ 菜单
    def _dev_build_menu(self) -> QMenu:
        menu = QMenu("开发者选项", self)
        menu.setObjectName("DevOptionsMenu")
        menu.setAccessibleName("开发者选项")
        for label, slot in zip(
            DEV_MENU_LABELS,
            (
                self._dev_voice_input_test,
                self._dev_speech_test,
                self._dev_print_screen_info,
                self._dev_export_logs,
                self._dev_export_config,
            ),
        ):
            action = QAction(label, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        return menu

    # -------------------------------------------------------------- ① 语音输入
    def _dev_voice_input_test(self) -> None:
        """把"这台设备能不能录、录完认不认得出"一次跑完，全程本地。"""
        from meapet.voice.engine import VOICE_INPUT_SAMPLE_RATE, list_input_devices

        cfg = self.config.get("voice_input") or {}
        devices = list_input_devices(VOICE_INPUT_SAMPLE_RATE)
        log.info(
            f"[dev] voice_input.enabled={cfg.get('enabled')} "
            f"指定设备={cfg.get('input_device_index')} 精度={cfg.get('precision')} "
            f"采样率={VOICE_INPUT_SAMPLE_RATE} 枚举到 {len(devices)} 台"
        )
        for index, name, channels, usable in devices:
            log.info(
                f"[dev]   设备 {index}: {name}（{channels} 声道，"
                f"{VOICE_INPUT_SAMPLE_RATE}Hz {'可用' if usable else '开不了这个格式'}）"
            )
        if not devices:
            self._dev_say("没枚举到录音设备（pyaudio 缺失或没有输入设备）")
            return

        engine = getattr(self, "_voice_engine", None)
        if engine is None:
            # 只把引擎建出来，不动 voice_input.enabled——那是产品开关，不是调试副作用
            self._start_voice_engine(cfg)
            engine = getattr(self, "_voice_engine", None)
        if engine is None or engine.isRunning():
            self._dev_say("语音引擎起不来或正在录音")
            return
        self._dev_say(f"开始录音 {DEV_VOICE_SECONDS} 秒，请说话")
        engine.toggle()
        QTimer.singleShot(DEV_VOICE_SECONDS * 1000, self._dev_stop_voice_test)

    def _dev_stop_voice_test(self) -> None:
        """停录 → 识别；结果沿既有链路进输入框或气泡，失败也会由那条链出声。"""
        engine = getattr(self, "_voice_engine", None)
        if engine is not None and engine.isRunning():
            engine.toggle()

    # ---------------------------------------------------------------- ② 朗读
    def _dev_speech_test(self) -> None:
        tts = getattr(self, "tts", None)
        log.info(
            f"[dev] tts.enabled={getattr(tts, 'enabled', None)} "
            f"engine={getattr(tts, 'engine', None)} "
            f"output_dir={getattr(tts, 'output_dir', None)}"
        )
        # 复用正式那条链：合成失败/未启用会回退成文字气泡，正好看见是哪一档
        self._speak_and_show(DEV_TEST_SENTENCE, 2500)

    # -------------------------------------------------------- ③ fidus 屏幕信息
    def _dev_print_screen_info(self) -> None:
        from meapet.desktop.fidus_position import have_engine
        from meapet.desktop.screen_geometry import available_geometry_for

        lines = [
            f"[dev] 版本 {__version__} · {platform.system()} {platform.release()} "
            f"· python {platform.python_version()} · sys.platform={sys.platform}",
        ]
        try:
            from PyQt5.QtGui import QGuiApplication

            app = QGuiApplication.instance()
            lines.append(
                f"[dev] Qt platformName={getattr(app, 'platformName', lambda: '?')()}"
                f" · WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')}"
                f" · XDG_SESSION_TYPE={os.environ.get('XDG_SESSION_TYPE')}"
            )
            for screen in (app.screens() if app is not None else []):
                geo = screen.geometry()
                lines.append(
                    f"[dev]   屏幕 {screen.name()}: {geo.width()}x{geo.height()} "
                    f"@({geo.x()},{geo.y()}) ratio={screen.devicePixelRatio()}"
                )
        except Exception as exc:
            lines.append(f"[dev] Qt 屏幕信息取不到: {type(exc).__name__}: {exc}")

        try:
            usable = available_geometry_for(self.frameGeometry())
            lines.append(
                f"[dev] 可用区: {None if usable is None else (usable.width(), usable.height(), usable.x(), usable.y())}"
            )
            lines.append(
                f"[dev] 窗口 {self.x()},{self.y()} {self.width()}x{self.height()}"
                f" · layer 几何 {self._layer_geometry()}"
            )
        except Exception as exc:
            lines.append(f"[dev] 几何取不到: {type(exc).__name__}: {exc}")

        backend = getattr(self, "_layer_backend", None)
        widget = getattr(self, "sprite_label", None)
        lines.append(
            f"[dev] fidus.enabled={bool((self.config.get('fidus') or {}).get('enabled', False))}"
            f" · have_engine={have_engine()}"
            f" · layer 后端={'有' if backend is not None else '无'}"
            f" · logical_size={backend.logical_size() if backend is not None else None}"
            f" · proxy_rect={getattr(widget, '_proxy_rect', None)}"
            f" · 待挂={getattr(self, '_pending_mount', None)}"
            f" · 测量轮次={getattr(self, '_fidus_round', 0)}"
        )
        for line in lines:
            log.info(line)
        self._dev_say("fidus 屏幕信息已打到日志")

    # ---------------------------------------------------------------- ④ 日志
    def _dev_export_logs(self) -> None:
        from meapet.log import LOG_DIR
        from meapet.paths import data_path, get_data_dir

        targets = []
        if os.path.isdir(LOG_DIR):
            for name in sorted(os.listdir(LOG_DIR)):
                path = os.path.join(LOG_DIR, name)
                if os.path.isfile(path):
                    targets.append(path)
        root = get_data_dir()
        for name in DATA_LOG_FILES:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                targets.append(path)
        if not targets:
            self._dev_say(f"没有日志可导出（{LOG_DIR} 是空的）")
            return

        out_dir = data_path(EXPORTS_DIR)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(
            out_dir, f"meapet-logs-{datetime.now():%Y%m%d-%H%M%S}.zip"
        )
        cfg = self.config or {}
        summary = "\n".join(
            [
                f"MeaPet {__version__}",
                f"{platform.system()} {platform.release()} / python {platform.python_version()}",
                f"sys.platform={sys.platform} WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY')}",
                f"数据目录={root} 日志目录={LOG_DIR}",
                f"文件 {len(targets)} 个:",
                *[f"  {path} {os.path.getsize(path)} B" for path in targets],
                # 配置摘要头只取非密开关，密钥面一律不进这里
                "配置摘要: "
                + json.dumps(
                    {
                        "live2d.enabled": (cfg.get("live2d") or {}).get("enabled"),
                        "fidus.enabled": (cfg.get("fidus") or {}).get("enabled"),
                        "voice_input.enabled": (cfg.get("voice_input") or {}).get("enabled"),
                        "vision.backend": (cfg.get("vision") or {}).get("backend"),
                        "tts.enabled": (cfg.get("tts") or {}).get("enabled"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            ]
        )
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("SUMMARY.txt", summary)
            for path in targets:
                # 只按 basename 入包：日志都在同一层，带绝对路径等于把别人的目录结构写进包里
                archive.write(path, os.path.basename(path))
        log.info(f"[dev] 日志导出: {out}（{len(targets)} 个文件）")
        self._dev_say(f"日志已导出：{os.path.basename(out)}")

    # ---------------------------------------------------------------- ⑤ 配置
    def _dev_export_config(self) -> None:
        from meapet.config.store import scrub_secrets
        from meapet.paths import data_path

        clean = scrub_secrets(self.config or {})
        out_dir = data_path(EXPORTS_DIR)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(
            out_dir, f"meapet-config-{datetime.now():%Y%m%d-%H%M%S}.json"
        )
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(clean, handle, ensure_ascii=False, indent=2, sort_keys=True)
        log.info(f"[dev] 配置导出（已过 scrub_secrets）: {out}")
        self._dev_say(f"配置已导出：{os.path.basename(out)}")
