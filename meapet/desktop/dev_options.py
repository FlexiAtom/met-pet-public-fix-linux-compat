"""开发者选项 —— 版本号彩蛋解锁的本地调试入口（人工 2026-10-06 点定的五项）。

三条口径写在这里，别处不复述：
- 解锁**不跨启动保留、也不落配置键**：彩蛋的语义就是"每次启动重新解锁"，
  新增配置键等于把一次性的手势变成长期状态。
- 各项全部**本地**：只读盘、只写盘、只用已有的录音/合成链路，不新增任何运行时外发。
- 未解锁时「开发者选项」这一支**压根不存在**（不是禁用摆在那儿）。

五项里的「朗读测试」在人工 2026-10-08 的要求下按引擎拆成两个键（GSV / VITS 各自一条
前置与选路，共用一个键就只能测到配置当前选中的那一个），菜单因此是六条。
每个键下面再按语言分三档（人工 2026-10-12 要求加中文与英文：两档此前没有入口，
取句跟着配置 `tts.voice_lang` 走，想换语言得先改产品配置）。

**「语音输入测试」的每一格读数都进终端**（人工 2026-10-14：只看气泡判不出成功与否）：
请求录音、真开录（含模型加载耗时）、停录、识别到几个字、链路报错、两档超时——缺一格
就明写缺哪格。「朗读测试」本来就印 `[dev] 朗读测试 …`，不在这条改动的范围内。
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
from meapet.tts.diagnostics import (
    SPEECH_TEST_LANGUAGES,
    SPEECH_WAIT_NOTES,
    speech_facts,
    speech_test_language_label,
    speech_test_sentence,
    tts_for_engine,
)

log = get_color_logger("dev")

DEV_UNLOCK_CLICKS = 5
DEV_UNLOCK_WINDOW_S = 10.0
#: 导出落点（相对数据目录）——与日志同侧，不往用户没预期的地方写。
EXPORTS_DIR = "exports"

DEV_VOICE_SECONDS = 4
#: 点下去到录音真开始之间的等待上限。模型加载跑在录音线程里（`engine.run` 第一步），
#: 这之前一个字节都录不到，所以"4 秒后停"必须由 `recording_started` 来排，不能按点击排。
DEV_VOICE_LOAD_WAIT_S = 30.0
#: 停录后等回执的上限。CPU 上解码几秒音频远用不满这一格，留的是"没回执"这句话的代价。
DEV_VOICE_RESULT_WAIT_S = 15.0
#: 印进终端的识别文本上限：长句只留这一段，字数照全文报。
DEV_VOICE_PRINT_CHARS = 200
DEV_UNLOCKED_HINT = "开发者选项已解锁（本次运行有效）"
# 日志导出里除了 LOG_DIR，还想要这几个（同属数据目录，散在旁边）
DATA_LOG_FILES = ("chat_errors.log", "meapet_boot.log", "meapet_fault.log")
#: 解锁后「设置与数据」里那一支的六项，顺序即菜单顺序（测试按它断言）。
#: 第 2、3 项现在是**子菜单标题**：`menu.actions()` 里它们是 menuAction，文本照旧，断言不用改。
DEV_MENU_LABELS = (
    f"语音输入测试（录 {DEV_VOICE_SECONDS} 秒）",
    "朗读测试（GPT-SoVITS）",
    "朗读测试（VITS）",
    "打印 fidus 屏幕信息",
    "导出日志（zip）",
    "导出配置（去密钥）",
)


def version_label_text() -> str:
    """「关于」里那一行版本号（彩蛋的点击目标，人工 2026-10-06 矫正的位置）。"""
    return f"版本 {__version__}"


def dev_unlock_hint(left: int) -> str:
    return f"再点 {left} 次解锁开发者选项"


class PetDevOptionsMixin:
    """挂在 MeaPet 上的开发者选项。

    要求 self 具备：`config`、`_show_bubble`、`_save_config`（导出只读 config，不写）。
    """

    _dev_unlocked = False
    _dev_click_times: tuple = ()
    #: 本次语音测试走到哪一格（None=没收口）；读数与超时都按它判，别在槽里另立一份状态。
    _dev_voice_phase = None
    _dev_voice_requested_at = None
    #: 已经挂过读数的引擎，按对象身份认——重复点同一台引擎不该把同一行印两遍。
    _dev_voice_watched = None

    # ------------------------------------------------------------------ 解锁
    def _dev_note_version_click(self) -> str:
        """10 秒窗口内点满 5 次才解锁；返回该在「关于」里显示的那句反馈。

        反馈不走气泡：「关于」是 ApplicationModal，气泡在它后面看不见。
        """
        if getattr(self, "_dev_unlocked", False):
            self._dev_click_times = ()
            return DEV_UNLOCKED_HINT
        now = time.monotonic()
        clicks = [
            stamp for stamp in getattr(self, "_dev_click_times", ())
            if now - float(stamp) < DEV_UNLOCK_WINDOW_S
        ]
        clicks.append(now)
        self._dev_click_times = clicks
        left = DEV_UNLOCK_CLICKS - len(clicks)
        if left <= 0:
            self._dev_unlocked = True
            self._dev_click_times = ()
            return DEV_UNLOCKED_HINT
        return dev_unlock_hint(left)

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
                # 两个引擎各一条：共用一个键就只测到配置当前选中的那一路，另一路的
                # 前置坏在哪一格永远看不见（人工 2026-10-08 点定拆成两个键）。
                # 每条再按语言开三档（人工 2026-10-12T15:34:53「加中文和英文的合成，
                # GSV和VITS都需要，目前都是日语」）：以前那一档跟着配置 `tts.voice_lang`
                # 取句，想听中文得先把配置改掉——调试入口不该要动人家的产品配置。
                ("speech", "gpt_sovits"),
                ("speech", "vits"),
                self._dev_print_screen_info,
                self._dev_export_logs,
                self._dev_export_config,
            ),
        ):
            if isinstance(slot, tuple):
                menu.addMenu(self._dev_speech_submenu(label, slot[1]))
                continue
            action = QAction(label, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        return menu

    def _dev_speech_submenu(self, label: str, engine: str) -> QMenu:
        """一个引擎一条「朗读测试」子菜单，档位与顺序只认 `SPEECH_TEST_LANGUAGES`。"""
        submenu = QMenu(label, self)
        submenu.setObjectName(f"DevSpeechTestMenu::{engine}")
        for text, code in SPEECH_TEST_LANGUAGES:
            action = QAction(text, self)
            action.triggered.connect(
                lambda _checked=False, e=engine, c=code: self._dev_speech_test(e, c)
            )
            submenu.addAction(action)
        return submenu

    # -------------------------------------------------------------- ① 语音输入
    def _dev_voice_input_test(self) -> None:
        """把"这台设备能不能录、录完认不认得出"一次跑完，全程本地。

        每一格都往终端印一行（人工 2026-10-14：只进气泡判不出成功与否）。产品那条链在
        **转写为空串时一个信号都不发**（`voice/engine.py` 里 `if text: emit`），"没反应"
        与"录到了但没认出字"于是长得一样——那一格只能由超时这一支说出口。
        """
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
            log.info(
                f"[dev] 语音输入测试: 没起来——engine={type(engine).__name__ if engine else None} "
                f"isRunning={engine.isRunning() if engine else None}"
            )
            self._dev_say("语音引擎起不来或正在录音")
            return

        self._dev_voice_attach(engine)
        self._dev_voice_phase = "等录音开始"
        self._dev_voice_requested_at = time.monotonic()
        self._dev_say(f"开始录音 {DEV_VOICE_SECONDS} 秒，请说话")
        log.info(
            f"[dev] 语音输入测试: 已请求录音，等模型加载（上限 {DEV_VOICE_LOAD_WAIT_S} s），"
            f"真开录后念 {DEV_VOICE_SECONDS} s"
        )
        engine.toggle()
        QTimer.singleShot(
            int(DEV_VOICE_LOAD_WAIT_S * 1000), self._dev_voice_load_timeout
        )

    def _dev_voice_attach(self, engine) -> None:
        """读数挂在既有信号上，产品那一路的连接与回调一个字不改。

        按对象身份去重：`_start_voice_engine` 每次新建引擎，连接天然是新的；复用同一台时
        不重连，否则同一条读数会印两遍。
        """
        if getattr(self, "_dev_voice_watched", None) is engine:
            return
        self._dev_voice_watched = engine
        engine.recording_started.connect(self._dev_voice_recording_started)
        engine.recording_stopped.connect(self._dev_voice_recording_stopped)
        engine.result_ready.connect(self._dev_voice_transcribed)
        engine.error.connect(self._dev_voice_failure)

    def _dev_voice_recording_started(self) -> None:
        """4 秒倒计时从这里排：模型加载跑在录音线程里，按点击排会把它耗掉、只录到空。"""
        waited = time.monotonic() - (self._dev_voice_requested_at or time.monotonic())
        if self._dev_voice_phase != "等录音开始":
            log.info(
                f"[dev] 语音输入测试: 录音这会儿才开始（等了 {waited:.2f} s），"
                f"本次已收口，不重排倒计时"
            )
            return
        self._dev_voice_phase = "录音中"
        log.info(
            f"[dev] 语音输入测试: 录音已开始（模型+开流耗时 {waited:.2f} s），"
            f"请说 {DEV_VOICE_SECONDS} s"
        )
        QTimer.singleShot(DEV_VOICE_SECONDS * 1000, self._dev_stop_voice_test)

    def _dev_voice_recording_stopped(self) -> None:
        if self._dev_voice_phase not in ("录音中", "等回执"):
            return
        log.info("[dev] 语音输入测试: 录音已停止，等识别回执")

    def _dev_voice_transcribed(self, text: str) -> None:
        self._dev_voice_phase = None
        clipped = text[:DEV_VOICE_PRINT_CHARS]
        log.info(
            f"[dev] 语音输入测试: 识别到 {len(text)} 字 →「{clipped}"
            f"{'…' if len(text) > DEV_VOICE_PRINT_CHARS else ''}」"
        )

    def _dev_voice_failure(self, message: str) -> None:
        self._dev_voice_phase = None
        log.info(f"[dev] 语音输入测试: 链路报错 →{message}")

    def _dev_voice_load_timeout(self) -> None:
        """到点还没开录：模型没加载起来（缺件/加载失败）或设备开不了流，两种都在下面那行里。"""
        if self._dev_voice_phase != "等录音开始":
            return
        self._dev_voice_phase = None
        log.info(
            f"[dev] 语音输入测试: {DEV_VOICE_LOAD_WAIT_S} s 内没开始录音——"
            f"模型没加载起来或那台设备开不了流（看同批 [voice] 行）"
        )

    def _dev_voice_result_timeout(self) -> None:
        if self._dev_voice_phase != "等回执":
            return
        self._dev_voice_phase = None
        log.info(
            f"[dev] 语音输入测试: 停录后 {DEV_VOICE_RESULT_WAIT_S} s 内没有任何回执——"
            f"转写为空串与音频太短在产品链路里都不发信号，所以这一格读不到字"
        )

    def _dev_stop_voice_test(self) -> None:
        """停录 → 识别；结果沿既有链路进输入框或气泡，这里另印一份到终端。"""
        engine = getattr(self, "_voice_engine", None)
        if engine is not None and engine.isRunning():
            engine.toggle()
        if self._dev_voice_phase != "录音中":
            return
        self._dev_voice_phase = "等回执"
        log.info(
            f"[dev] 语音输入测试: 已发停录，等回执（上限 {DEV_VOICE_RESULT_WAIT_S} s）"
        )
        QTimer.singleShot(
            int(DEV_VOICE_RESULT_WAIT_S * 1000), self._dev_voice_result_timeout
        )

    # ---------------------------------------------------------------- ② 朗读
    def _dev_tts_for_engine(self, engine: str):
        """判据本体在 `meapet.tts.diagnostics`（`--doctor` 共用一份），这里只把 `self.config` 递进去。"""
        return tts_for_engine(self.config or {}, engine)

    def _dev_speech_test(self, engine: str, lang: str = "") -> None:
        try:
            tts = self._dev_tts_for_engine(engine)
        except Exception as exc:
            # 建不出测试对象本身也是一种坏法：报出来比让它后面某处 AttributeError 强。
            log.error(f"[dev] {engine} 的 TTS 建不出来: {type(exc).__name__}: {exc}")
            self._dev_say(
                f"朗读不会出声：{engine} 的 TTS 建不出来 {type(exc).__name__}: {exc}"
            )
            return
        health = bool(tts.health_check())
        # 点的是哪一档就念哪门语言；不传（旧调用方）才回落到 `speak()` 自己那格算出的
        # 目标语言——同一个 `_normalize_voice_lang`，不在这里另立一份语言真值。
        target = lang or tts._normalize_voice_lang()
        label = speech_test_language_label(target)
        facts = (
            f"语言={label} "
            f"enabled={getattr(tts, 'enabled', None)} "
            f"engine={getattr(tts, 'engine', None)} "
            f"前置={'齐' if health else '不齐'}"
        )
        detail = speech_facts(engine, tts)
        log.info(f"[dev] 朗读测试 {facts} {detail}")
        if not getattr(tts, "enabled", False) or not health:
            # 这条链在前置不齐时回退成文字气泡，而那形状与"成功但这条没配音"完全一样，
            # 看不出坏在哪一格——开发者选项存在的意义就是把那一格写出来。
            self._dev_say(f"朗读不会出声：{facts} {detail}")
            return
        sentence = speech_test_sentence(target)
        note = SPEECH_WAIT_NOTES.get(engine, "本地合成，不是卡死")
        # 不先说一声，点下去就是一两分钟"没反应"——那形状和没修一模一样。
        # 语言也写进这句：他要确认的是"中文这档真念的是中文"，不只是引擎名。
        self._dev_say(f"开始朗读测试（{engine}，{label}，{note}）：{facts} {detail}")
        self._speak_and_show(sentence, 2500, tts=tts)

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
