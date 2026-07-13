"""配置向导各页面"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import urllib.request
from typing import Optional, Dict, Any, List

from PyQt5.QtWidgets import *
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject, QSize, QUrl
from PyQt5.QtGui import *

from wizard.styles import (
    STYLE_INPUT, STYLE_BTN_PRIMARY, STYLE_BTN_SECONDARY,
    COLOR_BG, COLOR_CARD, COLOR_ACCENT, COLOR_TEXT, COLOR_OK, COLOR_WARN, COLOR_ERR,
    STYLE_PAGE_CARD, set_status,
)
from wizard.platform_info import PLATFORM, CONFIG_PATH, platform_checklist, ollama_install_hint, detect_platform
from wizard.env_utils import (
    WorkerSignals, pip_install, check_installed, download_file,
    check_ollama_running, check_ollama_installed, pull_ollama_model,
)

# 兼容页面内可能使用的短名
class LLMPage(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(14)

        title = QLabel("选择 AI 大脑")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        desc = QLabel("桌宠要靠一个 AI 来对话，选一个你有的：")
        desc.setObjectName("PageDescription")
        layout.addWidget(desc)

        # Ollama
        self.radio_ollama = QRadioButton("Ollama（推荐 · 免费、本地运行）")
        self.radio_ollama.setAccessibleDescription("本地运行，不需要 API Key")
        self.radio_ollama.setChecked(True)
        layout.addWidget(self.radio_ollama)
        ollama_detail = QLabel(
            "    • 完全免费，不需要 API Key\n"
            "    • 需要先装 Ollama 并下载模型\n"
            "    • 推荐模型：qwen3.5:4b（多模态，对话+识图一体）"
        )
        ollama_detail.setObjectName("HelperText")
        ollama_detail.setWordWrap(True)
        layout.addWidget(ollama_detail)

        # DeepSeek
        self.radio_ds = QRadioButton("DeepSeek API（在线、速度快）")
        self.radio_ds.setAccessibleDescription("在线服务，按量付费，需要 API Key")
        layout.addWidget(self.radio_ds)
        deepseek_detail = QLabel(
            "    • 需要注册 DeepSeek 获取 API Key\n"
            "    • 按量付费，不需要本地显卡\n"
            "    • 注：屏幕识图仍需要 Ollama（装 qwen3.5:4b 即可，多模态对话+识图一体）"
        )
        deepseek_detail.setObjectName("HelperText")
        deepseek_detail.setWordWrap(True)
        layout.addWidget(deepseek_detail)

        # MiMo V2.5
        self.radio_mimo = QRadioButton("MiMo V2.5（小米多模态 API，在线、可识图）")
        self.radio_mimo.setAccessibleDescription("在线多模态服务，支持对话和识图，需要 API Key")
        layout.addWidget(self.radio_mimo)
        mimo_detail = QLabel(
            "    • 需要注册 xiaomimimo 平台获取 API Key\n"
            "    • 按量付费，不需要本地显卡\n"
            "    • 支持识图（不需要额外装 Ollama）"
        )
        mimo_detail.setObjectName("HelperText")
        mimo_detail.setWordWrap(True)
        layout.addWidget(mimo_detail)

        # Ollama 状态
        self.ollama_status = QLabel("")
        self.ollama_status.setProperty("status", "muted")
        self.ollama_status.setAccessibleName("Ollama 运行状态")
        layout.addWidget(self.ollama_status)

        layout.addStretch()
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(self._refresh_ollama_status)
        self._status_timer.start(100)

    def _refresh_ollama_status(self):
        running, models = check_ollama_running()
        installed = check_ollama_installed()
        if running:
            m = ", ".join(models[:3])
            set_status(self.ollama_status, "success", f"Ollama 运行中（模型：{m}）")
        elif installed:
            set_status(self.ollama_status, "warning", "Ollama 已安装但未运行，启动后再继续")
        else:
            set_status(
                self.ollama_status,
                "muted",
                "还没装 Ollama？可以先选 DeepSeek，或回头再装",
            )

    def get_backend(self):
        if self.radio_ollama.isChecked():
            return "ollama"
        elif self.radio_mimo.isChecked():
            return "mimo"
        return "deepseek"

    def set_backend(self, backend: str):
        """恢复上次选择的 AI 后端。"""
        backend = (backend or "ollama").lower()
        if backend == "mimo":
            self.radio_mimo.setChecked(True)
        elif backend == "deepseek":
            self.radio_ds.setChecked(True)
        else:
            self.radio_ollama.setChecked(True)


# ═══════════════════════════════════════
# 页面：API Key
# ═══════════════════════════════════════
