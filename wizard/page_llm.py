"""Unified OpenAI-compatible LLM connection configuration page."""
from __future__ import annotations

import json
import threading
import urllib.request
import urllib.error
from urllib.parse import urljoin

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from wizard.styles import (
    STYLE_PAGE_CARD,
    set_status,
)
from wizard.widgets import WheelSafeComboBox

from meapet.config.providers import CUSTOM_ID, all_presets, preset_by_id


def _normalize_base_url(url: str) -> str:
    """Strip common chat endpoint suffixes so /models can be appended."""
    lowered = url.strip().lower()
    # 改：只保留 /v1/models 所需的清理
    suffixes = (
        "/v1/chat/completions",
        "/v1/completions",
        "/chat/completions",
        "/completions",
    )
    for suffix in suffixes:
        if lowered.endswith(suffix):
            base = url[: -len(suffix)]
            return base.rstrip("/") + "/"
    return url.rstrip("/") + "/"


class LLMPage(QFrame):
    """Unified OpenAI-compatible connection configuration page."""

    models_fetched = pyqtSignal(list, str)  # model_names, error_message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._fetch_running = False
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(14)

        # Title
        title = QLabel("AI 模型连接")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        desc = QLabel(
            "从下拉里选一个服务商即可自动填好 API 地址；"
            "也可以选「自动识别 / 自定义」手填任意 OpenAI 兼容地址。"
            "配置一律保存为 custom，协议与密钥环境变量按供应商或地址自动确定。"
        )
        desc.setObjectName("PageDescription")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # --- Connection section ---
        conn_card = QFrame()
        conn_card.setObjectName("SectionCard")
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(16, 14, 16, 16)
        conn_layout.setSpacing(10)

        # 服务商：选预设即自动填入其 API 地址与协议；也可留在「自动识别」手填。
        # provider 字段仍恒为 custom，只是把地址/协议填好，省去手打。
        provider_label = QLabel("服务商：")
        provider_label.setObjectName("FieldLabel")
        conn_layout.addWidget(provider_label)
        self.provider_combo = WheelSafeComboBox()
        self.provider_combo.setObjectName("ProviderSelector")
        self.provider_combo.setAccessibleName("服务商")
        self.provider_combo.addItem("自动识别 / 自定义（手填 API 地址）", CUSTOM_ID)
        for _preset in all_presets():
            self.provider_combo.addItem(_preset.name, _preset.id)
        self.provider_combo.setToolTip(
            "选择服务商会自动填入其 API 地址，并按该厂商的协议发起请求；"
            "选「自动识别」则由程序按你填写的地址推断协议与密钥环境变量。"
        )
        conn_layout.addWidget(self.provider_combo)

        self.provider_hint = QLabel("")
        self.provider_hint.setObjectName("HelperText")
        self.provider_hint.setWordWrap(True)
        conn_layout.addWidget(self.provider_hint)

        # Base URL
        base_url_label = QLabel("API 地址：")
        base_url_label.setObjectName("FieldLabel")
        conn_layout.addWidget(base_url_label)
        self.endpoint_input = QLineEdit("https://api.openai.com/v1")
        self.endpoint_input.setObjectName("ApiBaseUrl")
        self.endpoint_input.setAccessibleName("API 地址")
        self.endpoint_input.setPlaceholderText("https://api.openai.com/v1")
        self.endpoint_input.setStyleSheet(
            "QLineEdit {"
            "  background: #1B1D2E;"
            "  color: #E8EAF0;"
            "  border: 1px solid #3A3D52;"
            "  border-radius: 6px;"
            "  padding: 0px 12px;"
            "  font-size: 14px;"
            "}"
            "QLineEdit:focus {"
            "  border: 2px solid #7C8AFF;"
            "}"
        )
        conn_layout.addWidget(self.endpoint_input)

        # Model ID with fetch button
        model_row = QHBoxLayout()
        model_col = QVBoxLayout()
        model_label = QLabel("模型名称：")
        model_label.setObjectName("FieldLabel")
        model_col.addWidget(model_label)
        self.model_combo = WheelSafeComboBox()
        self.model_combo.setObjectName("ModelSelector")
        self.model_combo.setAccessibleName("模型名称")
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(WheelSafeComboBox.NoInsert)
        self.model_combo.lineEdit().setPlaceholderText("gpt-4o")
        from PyQt5.QtCore import Qt
        from meapet.ui_theme import MIN_TARGET_SIZE

        le = self.model_combo.lineEdit()
        if le is not None:
            le.setAlignment(Qt.AlignVCenter)
            le.setContentsMargins(4, 0, 0, 0)
            le.setStyleSheet(
                "QLineEdit {"
                "  background: transparent;"
                "  border: none;"
                "  margin: 0px;"
                "  padding: 0px;"
                "}"
            )
        self.model_combo.setMinimumHeight(MIN_TARGET_SIZE)
        self.model_combo.setStyleSheet(
            "WheelSafeComboBox {"
            "  padding: 0px 34px 0px 0px;"
            "}"
        )
        self.model_combo.setMinimumContentsLength(30)
        model_col.addWidget(self.model_combo)
        model_row.addLayout(model_col, 1)

        fetch_col = QVBoxLayout()
        fetch_col.addWidget(QLabel(""))  # spacer to align with input row
        self.fetch_models_btn = QPushButton("获取模型列表")
        self.fetch_models_btn.setObjectName("FetchModelsButton")
        self.fetch_models_btn.setAccessibleName("从接口地址获取可用模型列表")
        self.fetch_models_btn.setProperty("doesNotModifyConfig", True)
        self.fetch_models_btn.setToolTip("向接口地址查询可用模型 ID")
        fetch_col.addWidget(self.fetch_models_btn)
        model_row.addLayout(fetch_col)
        conn_layout.addLayout(model_row)

        self.models_fetch_status = QLabel("")
        self.models_fetch_status.setProperty("status", "muted")
        self.models_fetch_status.setAccessibleName("模型列表获取状态")
        conn_layout.addWidget(self.models_fetch_status)

        # API Key
        api_key_label = QLabel("API Key / 环境变量占位符：")
        api_key_label.setObjectName("FieldLabel")
        conn_layout.addWidget(api_key_label)
        self.direct_api_key_input = QLineEdit()
        self.direct_api_key_input.setObjectName("ApiKey")
        self.direct_api_key_input.setStyleSheet(
            "QLineEdit {"
            "  background: #1B1D2E;"
            "  color: #E8EAF0;"
            "  border: 1px solid #3A3D52;"
            "  border-radius: 6px;"
            "  padding: 0px 12px;"
            "  font-size: 14px;"
            "}"
            "QLineEdit:focus {"
            "  border: 2px solid #7C8AFF;"
            "}"
        )
        self.direct_api_key_input.setEchoMode(QLineEdit.Password)
        self.direct_api_key_input.setAccessibleName("API Key")
        self.direct_api_key_input.setAccessibleDescription(
            "凭据只会保存到本机配置文件，也可填写环境变量占位符"
        )
        self.direct_api_key_input.setPlaceholderText("例如 $MEAPET_API_KEY 或 sk-...")
        key_row = QHBoxLayout()
        key_row.addWidget(self.direct_api_key_input, 1)
        self.api_key_visibility = QPushButton("显示 Key")
        self.api_key_visibility.setCheckable(True)
        self.api_key_visibility.setAccessibleName("切换 API Key 可见性")
        self.api_key_visibility.setProperty("doesNotModifyConfig", True)
        self.api_key_visibility.toggled.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.api_key_visibility)
        conn_layout.addLayout(key_row)

        # Tuning parameters
        tuning = QHBoxLayout()
        temperature_label = QLabel("回复随机性：")
        temperature_label.setObjectName("FieldLabel")
        tuning.addWidget(temperature_label)
        self.temperature_input = QDoubleSpinBox()
        self.temperature_input.setObjectName("Temperature")
        self.temperature_input.setAccessibleName("回复随机性")
        self.temperature_input.setRange(0.0, 2.0)
        self.temperature_input.setDecimals(2)
        self.temperature_input.setSingleStep(0.05)
        self.temperature_input.setValue(0.7)
        self.temperature_input.setStyleSheet(
            "QDoubleSpinBox { padding: 0px 34px 0px 8px; }"
        )
        tuning.addWidget(self.temperature_input)
        max_tokens_label = QLabel("最大回复长度（tokens）：")
        max_tokens_label.setObjectName("FieldLabel")
        tuning.addWidget(max_tokens_label)
        self.max_tokens_input = QSpinBox()
        self.max_tokens_input.setObjectName("MaxTokens")
        self.max_tokens_input.setAccessibleName("最大回复长度")
        self.max_tokens_input.setRange(1, 1_000_000)
        self.max_tokens_input.setValue(4096)
        self.max_tokens_input.setStyleSheet(
            "QSpinBox { padding: 0px 34px 0px 8px; }"
        )
        tuning.addWidget(self.max_tokens_input)
        tuning.addStretch()
        conn_layout.addLayout(tuning)
        tuning_hint = QLabel(
            "回复随机性越高，措辞越多变；最大回复长度会直接写入模型请求，"
            "数值越大越不容易截断，也可能增加耗时与费用。"
        )
        tuning_hint.setObjectName("HelperText")
        tuning_hint.setWordWrap(True)
        conn_layout.addWidget(tuning_hint)

        # Test connection
        test_row = QHBoxLayout()
        self.test_connection_btn = QPushButton("测试连接")
        self.test_connection_btn.setAccessibleName("测试模型连接")
        self.test_connection_btn.setProperty("doesNotModifyConfig", True)
        test_row.addWidget(self.test_connection_btn)
        self.connection_status = QLabel("尚未测试")
        self.connection_status.setProperty("status", "muted")
        self.connection_status.setAccessibleName("连接测试状态")
        self.connection_status.setWordWrap(True)
        test_row.addWidget(self.connection_status, 1)
        conn_layout.addLayout(test_row)

        layout.addWidget(conn_card)
        layout.addStretch()

        # Signals
        self.fetch_models_btn.clicked.connect(self._start_fetch_models)
        self.models_fetched.connect(self._apply_fetched_models)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)

    # ── Provider presets ─────────────────────────────────────

    def _selected_preset(self):
        """当前下拉选中的预设；选「自动识别」时返回 None。"""
        return preset_by_id(self.provider_combo.currentData() or CUSTOM_ID)

    def _on_provider_changed(self, _index: int) -> None:
        """选中预设时填入其 API 地址与默认模型，并给出密钥提示。"""
        preset = self._selected_preset()
        if preset is None:
            self.provider_hint.setText("")
            return
        if preset.api_base:
            self.endpoint_input.setText(preset.api_base)
        # 同步模型：用该厂商候选填充下拉，默认选中第一个；
        # 聚合/本地供应商无固定清单时清空，提示去「获取模型列表」或手填。
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model_id in preset.models:
            self.model_combo.addItem(model_id)
        self.model_combo.setEditText(preset.default_model)
        self.model_combo.blockSignals(False)
        set_status(self.models_fetch_status, "muted", "")
        hints = []
        if not preset.requires_key:
            hints.append("本地服务，无需 API Key。")
        elif preset.env_keys:
            hints.append("也可填 $" + preset.env_keys[0] + " 从环境变量读取。")
        if not preset.models:
            hints.append("该服务商模型较多，点「获取模型列表」或手填模型名。")
        if preset.note:
            hints.append(preset.note)
        self.provider_hint.setText(" ".join(hints))


    # ── Provider identity（恒为 custom） ──────────────────────

    def get_backend(self) -> str:
        """Always custom. Brand is not a conversation backend."""
        return "custom"

    def set_backend(self, backend: str) -> None:
        """No-op compatibility shim; provider is never brand-selected."""
        return None

    # ── Fetch models ─────────────────────────────────────────

    def _start_fetch_models(self) -> None:
        """Dispatch model discovery to a background thread."""
        if self._fetch_running:
            return
        base_url = self.endpoint_input.text().strip()
        if not base_url:
            set_status(
                self.models_fetch_status, "error", "请先填写 API 地址。"
            )
            return

        self._fetch_running = True
        self.fetch_models_btn.setEnabled(False)
        set_status(self.models_fetch_status, "warning", "正在获取模型列表...")
        api_key = self.direct_api_key_input.text().strip()
        thread = threading.Thread(
            target=self._fetch_models_worker,
            args=(base_url, api_key),
            name="meapet-wizard-fetch-models",
            daemon=True,
        )
        thread.start()

    def _fetch_models_worker(self, base_url: str, api_key: str) -> None:
        """Background worker: try GET /v1/models only. No Ollama fallback."""
        names = []
        error = ""

        # Normalize the base URL — strip common chat suffixes
        norm_url = _normalize_base_url(base_url)

        # Build headers with optional auth
        headers = {}
        if api_key and not api_key.startswith("$"):
            if api_key.startswith("sk-") or "Bearer" not in headers:
                headers["Authorization"] = f"Bearer {api_key}"

        # Try OpenAI-compatible /v1/models
        try:
            url = urljoin(norm_url, "models")
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict):
                    models_raw = data.get("data") or data.get("models") or []
                    if isinstance(models_raw, list):
                        names = [
                            m.get("id") or m.get("name") or ""
                            for m in models_raw
                            if isinstance(m, dict)
                        ]
                        names = [n for n in names if n]
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                error = f"需要鉴权（HTTP {e.code}），请填写有效的 API Key 后重试"
            else:
                error = f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            error = f"连接失败：{e.reason}"
        except Exception as e:
            error = str(e)[:120] or type(e).__name__

        # If /v1/models failed with auth error and we have a key, retry with it
        if not names and error and "需要鉴权" in error and api_key:
            error = ""
            try:
                url = urljoin(norm_url, "models")
                auth_headers = dict(headers)
                if "Authorization" not in auth_headers and not api_key.startswith("$"):
                    auth_headers["Authorization"] = f"Bearer {api_key}"
                req = urllib.request.Request(url, headers=auth_headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, dict):
                        models_raw = data.get("data") or data.get("models") or []
                        if isinstance(models_raw, list):
                            names = [
                                m.get("id") or m.get("name") or ""
                                for m in models_raw
                                if isinstance(m, dict)
                            ]
                            names = [n for n in names if n]
                if names:
                    error = ""
            except urllib.error.HTTPError as e:
                error = f"需要鉴权（HTTP {e.code}），请填写有效的 API Key 后重试"
            except urllib.error.URLError as e:
                error = f"连接失败：{e.reason}"
            except Exception as e:
                error = str(e)[:120] or type(e).__name__

        # 改：移除 Ollama /api/tags fallback

        if not names and not error:
            error = "未能获取到模型列表，请检查地址是否正确。也可手动输入模型名称。"

        try:
            self.models_fetched.emit(names, error)
        except RuntimeError:
            pass

    def _apply_fetched_models(self, names: list[str], error: str) -> None:
        """Populate the model combo with discovered model IDs."""
        self._fetch_running = False
        self.fetch_models_btn.setEnabled(True)

        if error:
            set_status(self.models_fetch_status, "error", error)
            return

        if not names:
            set_status(
                self.models_fetch_status,
                "muted",
                "未发现可用模型，可手动输入。",
            )
            return

        current_text = self.model_combo.currentText().strip()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for name in sorted(names):
            self.model_combo.addItem(name)
        idx = self.model_combo.findText(current_text)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        elif current_text:
            self.model_combo.setEditText(current_text)
        self.model_combo.blockSignals(False)

        count = len(names)
        set_status(
            self.models_fetch_status,
            "success",
            f"获取到 {count} 个模型。",
        )

    # ── Configuration profile ────────────────────────────────

    def collect_direct_profile(self, api_key: str = "") -> dict:
        """Collect the current form values into a config profile dict."""
        from meapet.config.store import infer_direct_protocol

        endpoint = self.endpoint_input.text().strip()
        preset = self._selected_preset()
        if preset is not None and preset.api_base and endpoint == preset.api_base:
            # 显式选了服务商且地址未被改动：直接用该厂商协议。
            # 这样 LM Studio 这类本地 OpenAI 兼容服务不会被
            # 「localhost → ollama」的宽泛推断规则误判。
            protocol = preset.protocol
        else:
            # provider 恒为 custom；协议按 API 地址自动识别（Ollama→ollama_chat 等）
            protocol = infer_direct_protocol(
                "custom",
                api_base=endpoint,
                host="",
            )
        return {
            "provider": "custom",
            "protocol": protocol,
            "api_base": endpoint,
            "host": "",
            "model": self.model_combo.currentText().strip(),
            "api_key": str(api_key or self.direct_api_key_input.text()).strip(),
            "temperature": self.temperature_input.value(),
            "max_tokens": self.max_tokens_input.value(),
        }

    def apply_direct_profile(self, profile: dict) -> None:
        """Restore form fields from a previously saved profile.

        Provider is not restored as a brand selection — identity is always custom.
        下拉只按已保存的 api_base 回选到对应预设，纯属方便查看与再次编辑。
        """
        profile = profile or {}
        endpoint = profile.get("api_base") or profile.get("host") or ""
        self._select_provider_for_endpoint(str(endpoint))
        self.endpoint_input.setText(str(endpoint))

        model = str(profile.get("model") or "")
        self.model_combo.setEditText(model)

        self.direct_api_key_input.setText(str(profile.get("api_key") or ""))
        try:
            self.temperature_input.setValue(float(profile.get("temperature", 0.7)))
        except (TypeError, ValueError):
            self.temperature_input.setValue(0.7)
        try:
            self.max_tokens_input.setValue(int(profile.get("max_tokens", 4096)))
        except (TypeError, ValueError):
            self.max_tokens_input.setValue(4096)


    # ── Helpers ───────────────────────────────────────────────

    def _select_provider_for_endpoint(self, endpoint: str) -> None:
        """按已保存的 api_base 回选下拉；未命中则落到「自动识别」。

        只在地址与预设完全一致时才回选，避免把用户自定义的反代地址
        误标成某个厂商。回选期间屏蔽信号，防止覆写用户已存的地址。
        """
        target = str(endpoint or "").strip()
        index = 0
        if target:
            for i in range(self.provider_combo.count()):
                preset = preset_by_id(self.provider_combo.itemData(i) or CUSTOM_ID)
                if preset is not None and preset.api_base and preset.api_base == target:
                    index = i
                    break
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(index)
        self.provider_combo.blockSignals(False)
        preset = self._selected_preset()
        self.provider_hint.setText(preset.note if preset is not None else "")

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.direct_api_key_input.setEchoMode(
            QLineEdit.Normal if visible else QLineEdit.Password
        )
        self.api_key_visibility.setText("隐藏 Key" if visible else "显示 Key")
        self.api_key_visibility.setAccessibleName(
            "隐藏 API Key" if visible else "显示 API Key"
        )

