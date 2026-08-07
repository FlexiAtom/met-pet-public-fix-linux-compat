"""语音输入配置页面 — 向导第 5 标签页。

默认关闭（隐私优先）。启用后支持：
- 本地 sherpa-onnx 离线转文字（中英双语，约 220MB）
- 一键安装依赖 + 模型下载（后台线程，不卡 UI）
"""
from __future__ import annotations

import subprocess
import sys

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from meapet.dependencies import (
    VOICE_INPUT_INSTALL_REQUIREMENTS,
    resolve_pip_index_url,
)
from meapet.paths import (
    VOICE_ASR_MODEL_REPO,
    find_voice_asr_model_dir,
    is_frozen,
    voice_asr_cache_dir,
)
from wizard.styles import (
    STYLE_PAGE_CARD,
    set_status,
)
from wizard.widgets import WheelSafeComboBox


class _InstallWorker(QThread):
    """后台线程：pip install + ModelScope 模型下载。"""
    progress = pyqtSignal(str)       # 阶段文字更新
    finished = pyqtSignal(bool, str)  # (success, detail)

    def run(self):
        cmd = _voice_input_install_command()
        if cmd is None:
            self.finished.emit(
                False,
                "当前是冻结版程序，不支持运行时安装语音依赖或下载模型。"
                "请在构建环境准备依赖和模型后重新打包。",
            )
            return

        # 步骤 1: pip install
        self.progress.emit("正在安装语音识别依赖…")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                self.finished.emit(False, err[:500] if err else "pip 安装失败")
                return
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "安装超时，请检查网络")
            return
        except Exception as exc:
            self.finished.emit(False, str(exc))
            return

        # 步骤 2: 下载 ASR 模型
        self.progress.emit("正在下载语音识别模型（约 220MB）…")
        try:
            from modelscope import snapshot_download

            snapshot_download(
                VOICE_ASR_MODEL_REPO,
                cache_dir=str(voice_asr_cache_dir()),
            )
            self.finished.emit(True, "安装完成！依赖 + 模型已就绪")
        except ImportError:
            self.finished.emit(
                False,
                "模型下载失败：modelscope 安装后仍无法导入，请检查安装日志。",
            )
        except Exception as exc:
            self.finished.emit(False, f"模型下载失败: {exc}")


def _voice_input_install_command() -> list[str] | None:
    """Return the source-runtime pip command; frozen EXEs cannot run pip."""
    if is_frozen():
        return None
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--index-url",
        resolve_pip_index_url(),
        *VOICE_INPUT_INSTALL_REQUIREMENTS,
    ]


class VoiceInputPage(QFrame):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(12)

        title = QLabel("语音输入")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        desc = QLabel(
            "开启语音输入后，打开输入框会多出一个麦克风按钮。\n"
            "点击开始录音，再点停止 → 自动转文字填入输入框。\n"
            "识别引擎为 sherpa-onnx，中英双语，完全离线。"
        )
        desc.setObjectName("PageDescription")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.enable_cb = QCheckBox("启用语音输入（默认关闭）")
        self.enable_cb.setChecked(False)
        self.enable_cb.toggled.connect(self._on_enable_changed)
        layout.addWidget(self.enable_cb)

        progressive = QLabel("可先不启用。需要时再打开，并在下方安装依赖。")
        progressive.setObjectName("HelperText")
        progressive.setWordWrap(True)
        layout.addWidget(progressive)

        self.settings_frame = QFrame()
        self.settings_frame.setObjectName("SectionCard")
        self.settings_layout = QVBoxLayout(self.settings_frame)
        self.settings_layout.setContentsMargins(16, 14, 16, 14)
        self.settings_layout.setSpacing(10)
        self.settings_frame.setVisible(False)
        layout.addWidget(self.settings_frame)

        eng_label = QLabel("识别引擎：")
        eng_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(eng_label)
        self.engine_combo = WheelSafeComboBox()
        self.engine_combo.setObjectName("VoiceEngine")
        self.engine_combo.addItem(
            "sherpa-onnx zipformer（中英双语，约 220MB）", "sherpa_onnx"
        )
        self.settings_layout.addWidget(self.engine_combo)

        lang_label = QLabel("识别语言：")
        lang_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(lang_label)
        self.lang_combo = WheelSafeComboBox()
        self.lang_combo.setObjectName("VoiceLanguage")
        self.lang_combo.addItem("中文", "zh")
        self.lang_combo.addItem("自动检测", "auto")
        self.lang_combo.addItem("英文", "en")
        self.lang_combo.addItem("日文", "ja")
        self.settings_layout.addWidget(self.lang_combo)

        self.auto_send_cb = QCheckBox("识别完成后自动发送（关闭则只填入输入框）")
        self.auto_send_cb.setChecked(False)
        self.auto_send_cb.setToolTip(
            "开启后语音识别结果会直接发送；关闭则先填入输入框等你确认。"
        )
        self.settings_layout.addWidget(self.auto_send_cb)

        deps_label = QLabel("一键安装（依赖 + 模型）")
        deps_label.setObjectName("FieldLabel")
        self.settings_layout.addWidget(deps_label)
        deps_desc = QLabel(
            "安装 sherpa-onnx + pyaudio，并下载中英双语语音识别模型。\n"
            "模型约 220MB，仅需下载一次。"
        )
        deps_desc.setObjectName("HelperText")
        deps_desc.setWordWrap(True)
        self.settings_layout.addWidget(deps_desc)

        self.install_btn = QPushButton("安装语音识别依赖与模型")
        self.install_btn.setProperty("doesNotModifyConfig", True)
        self.install_btn.clicked.connect(self._install_deps)
        self.settings_layout.addWidget(self.install_btn)

        self.deps_status = QLabel("")
        self.deps_status.setWordWrap(True)
        self.settings_layout.addWidget(self.deps_status)

        self.deps_status_detail = QLabel("")
        self.deps_status_detail.setWordWrap(True)
        self.deps_status_detail.setProperty("status", "muted")
        self.settings_layout.addWidget(self.deps_status_detail)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setProperty("status", "muted")
        self.settings_layout.addWidget(self.hint)

        layout.addStretch()
        self._check_deps()
        self._install_worker = None

    def _on_enable_changed(self, enabled: bool):
        self.settings_frame.setVisible(enabled)
        if enabled:
            self._check_deps()

    def _sync_hint(self):
        set_status(
            self.hint, "muted",
            "sherpa-onnx 完全离线运行，无需 API Key。"
        )

    def _model_ok(self) -> bool:
        """检查 ASR 模型文件是否存在。"""
        return find_voice_asr_model_dir() is not None

    def _check_deps(self):
        missing = []
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            missing.append("pyaudio")
        try:
            import sherpa_onnx  # noqa: F401
        except ImportError:
            missing.append("sherpa-onnx")

        model_ok = self._model_ok()
        if not missing and model_ok:
            set_status(self.deps_status, "success", "依赖与模型已就绪")
            self.deps_status_detail.setText("可以正常使用语音输入功能")
            self.install_btn.setEnabled(False)
            self.install_btn.setText("已安装就绪")
        elif is_frozen():
            set_status(
                self.deps_status,
                "warning",
                "冻结版缺少语音识别依赖或模型",
            )
            self.deps_status_detail.setText(
                "当前冻结版不支持运行时安装或下载。"
                "请在构建环境准备依赖和模型后重新打包。"
            )
            self.install_btn.setEnabled(False)
            self.install_btn.setText("需重新打包")
        elif not missing:
            set_status(self.deps_status, "warning", "依赖已安装，但模型文件缺失")
            self.deps_status_detail.setText("点击按钮下载语音识别模型（约 220MB）")
            self.install_btn.setEnabled(True)
            self.install_btn.setText("下载语音识别模型")
        else:
            set_status(self.deps_status, "warning",
                       f"缺少: {', '.join(missing)}")
            self.deps_status_detail.setText(
                "点击按钮一键安装依赖库并下载模型\n"
                "（sherpa-onnx + pyaudio + 220MB 模型）"
            )
            self.install_btn.setEnabled(True)
            self.install_btn.setText("安装语音识别依赖与模型")

    def _install_deps(self):
        if is_frozen():
            self._on_install_finished(
                False,
                "当前是冻结版程序，不支持运行时安装语音依赖或下载模型。"
                "请在构建环境准备依赖和模型后重新打包。",
            )
            return
        self.install_btn.setEnabled(False)
        self.install_btn.setText("安装中…")
        set_status(self.deps_status, "info", "第一步：安装 Python 依赖…")
        self.deps_status_detail.setText("正在后台执行，请稍候")

        self._install_worker = _InstallWorker()
        self._install_worker.progress.connect(self.deps_status_detail.setText)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_finished(self, success: bool, detail: str):
        if success:
            self._check_deps()
        else:
            set_status(self.deps_status, "error", detail)
            frozen = is_frozen()
            self.install_btn.setEnabled(not frozen)
            self.install_btn.setText("需重新打包" if frozen else "重试安装")

    def apply_config(self, voice_cfg: dict):
        voice_cfg = voice_cfg or {}
        self.enable_cb.setChecked(bool(voice_cfg.get("enabled", False)))

        lang = str(voice_cfg.get("language", "zh")).strip().lower()
        lidx = self.lang_combo.findData(lang)
        self.lang_combo.setCurrentIndex(lidx if lidx >= 0 else 0)

        self.auto_send_cb.setChecked(bool(voice_cfg.get("auto_send", False)))
        self._sync_hint()

    def collect(self) -> dict:
        return {
            "voice_input": {
                "enabled": self.enable_cb.isChecked(),
                "engine": "sherpa_onnx",
                "language": self.lang_combo.currentData() or "zh",
                "auto_send": self.auto_send_cb.isChecked(),
            }
        }
