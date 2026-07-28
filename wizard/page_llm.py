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
    STYLE_INPUT,
    STYLE_PAGE_CARD,
    set_status,
)
from wizard.widgets import WheelSafeComboBox

from meapet.config.defaults import DEFAULT_OPENAI_API_BASE
from meapet.config.providers import CUSTOM_ID, all_presets, preset_by_id

# 查询模型列表时使用的 UA。不少网关（Cloudflare 等）会 403 掉无 UA 的请求。
_MODELS_USER_AGENT = "MeaPet/1.0 (+https://github.com/suan-11/mea-pet-public)"
# 自定义头不得篡改鉴权与协议语义。
_FETCH_PROTECTED_HEADERS = frozenset(
    {"authorization", "x-api-key", "anthropic-version", "accept", "user-agent"}
)


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
        # 已保存配置里的自定义请求头（未选预设时原样保留，不因重开向导丢失）
        self._custom_headers: dict = {}
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
        self.endpoint_input = QLineEdit(DEFAULT_OPENAI_API_BASE)
        self.endpoint_input.setObjectName("ApiBaseUrl")
        self.endpoint_input.setAccessibleName("API 地址")
        self.endpoint_input.setPlaceholderText(DEFAULT_OPENAI_API_BASE)
        self.endpoint_input.setStyleSheet(STYLE_INPUT)
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
        self.direct_api_key_input.setStyleSheet(STYLE_INPUT)
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

        # --- 高级配置（默认折叠） ---
        self.advanced_toggle = QPushButton("▸ 高级配置")
        self.advanced_toggle.setObjectName("AdvancedToggle")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setAccessibleName("展开或收起高级配置")
        self.advanced_toggle.setProperty("doesNotModifyConfig", True)
        conn_layout.addWidget(self.advanced_toggle)

        self.advanced_box = QFrame()
        self.advanced_box.setObjectName("AdvancedBox")
        adv = QVBoxLayout(self.advanced_box)
        adv.setContentsMargins(0, 4, 0, 0)
        adv.setSpacing(8)

        # 超时时间
        timeout_row = QHBoxLayout()
        timeout_label = QLabel("超时时间（秒）：")
        timeout_label.setObjectName("FieldLabel")
        timeout_row.addWidget(timeout_label)
        self.timeout_input = QSpinBox()
        self.timeout_input.setObjectName("TimeoutSeconds")
        self.timeout_input.setAccessibleName("请求超时时间")
        self.timeout_input.setRange(0, 3600)
        self.timeout_input.setSpecialValueText("自动")
        self.timeout_input.setValue(0)
        self.timeout_input.setStyleSheet("QSpinBox { padding: 0px 34px 0px 8px; }")
        timeout_row.addWidget(self.timeout_input)
        timeout_row.addStretch()
        adv.addLayout(timeout_row)
        timeout_hint = QLabel("0 表示自动（按最大回复长度估算，至少 300 秒）。")
        timeout_hint.setObjectName("HelperText")
        timeout_hint.setWordWrap(True)
        adv.addWidget(timeout_hint)

        # 代理地址
        proxy_label = QLabel("代理地址：")
        proxy_label.setObjectName("FieldLabel")
        adv.addWidget(proxy_label)
        self.proxy_input = QLineEdit()
        self.proxy_input.setObjectName("ProxyUrl")
        self.proxy_input.setAccessibleName("HTTP 代理地址")
        self.proxy_input.setPlaceholderText("如 http://127.0.0.1:7890，留空则不使用代理")
        self.proxy_input.setStyleSheet(STYLE_INPUT)
        adv.addWidget(self.proxy_input)
        proxy_hint = QLabel("仅对该模型接口的请求生效，不影响其它网络访问。")
        proxy_hint.setObjectName("HelperText")
        proxy_hint.setWordWrap(True)
        adv.addWidget(proxy_hint)

        # 自定义请求头
        headers_label = QLabel("自定义请求头：")
        headers_label.setObjectName("FieldLabel")
        adv.addWidget(headers_label)
        self.headers_input = QLineEdit()
        self.headers_input.setObjectName("CustomHeaders")
        self.headers_input.setAccessibleName("自定义请求头")
        self.headers_input.setPlaceholderText("每条一个，形如 X-Title: MeaPet，多条用 ; 分隔")
        self.headers_input.setStyleSheet(STYLE_INPUT)
        adv.addWidget(self.headers_input)
        headers_hint = QLabel(
            "部分网关要求附加请求头。鉴权相关的头由程序按协议自动设置，"
            "此处填写的同名项会被忽略。"
        )
        headers_hint.setObjectName("HelperText")
        headers_hint.setWordWrap(True)
        adv.addWidget(headers_hint)

        # Anthropic 专属：扩展思考
        self.thinking_group = QFrame()
        self.thinking_group.setObjectName("ThinkingGroup")
        think = QVBoxLayout(self.thinking_group)
        think.setContentsMargins(0, 6, 0, 0)
        think.setSpacing(8)
        thinking_title = QLabel("扩展思考（Claude 专属）")
        thinking_title.setObjectName("FieldLabel")
        think.addWidget(thinking_title)
        think_row = QHBoxLayout()
        think_mode_label = QLabel("思考模式：")
        think_mode_label.setObjectName("FieldLabel")
        think_row.addWidget(think_mode_label)
        self.thinking_mode = WheelSafeComboBox()
        self.thinking_mode.setObjectName("ThinkingMode")
        self.thinking_mode.setAccessibleName("扩展思考模式")
        self.thinking_mode.addItem("关闭", "")
        self.thinking_mode.addItem("自适应（推荐）", "adaptive")
        self.thinking_mode.addItem("手动预算", "enabled")
        think_row.addWidget(self.thinking_mode)
        think_effort_label = QLabel("思考深度：")
        think_effort_label.setObjectName("FieldLabel")
        think_row.addWidget(think_effort_label)
        self.thinking_effort = WheelSafeComboBox()
        self.thinking_effort.setObjectName("ThinkingEffort")
        self.thinking_effort.setAccessibleName("思考深度")
        for label, value in (
            ("默认", ""), ("low", "low"), ("medium", "medium"),
            ("high", "high"), ("max", "max"),
        ):
            self.thinking_effort.addItem(label, value)
        think_row.addWidget(self.thinking_effort)
        think_row.addStretch()
        think.addLayout(think_row)
        budget_row = QHBoxLayout()
        budget_label = QLabel("思考预算（tokens）：")
        budget_label.setObjectName("FieldLabel")
        budget_row.addWidget(budget_label)
        self.thinking_budget = QSpinBox()
        self.thinking_budget.setObjectName("ThinkingBudget")
        self.thinking_budget.setAccessibleName("思考预算")
        self.thinking_budget.setRange(0, 200000)
        self.thinking_budget.setSpecialValueText("未设置")
        self.thinking_budget.setValue(0)
        self.thinking_budget.setStyleSheet("QSpinBox { padding: 0px 34px 0px 8px; }")
        budget_row.addWidget(self.thinking_budget)
        budget_row.addStretch()
        think.addLayout(budget_row)
        thinking_hint = QLabel(
            "「手动预算」模式下需 ≥ 1024 才会生效；「自适应」由模型自行决定深度。"
            "开启扩展思考时不再发送回复随机性参数（接口要求）。"
        )
        thinking_hint.setObjectName("HelperText")
        thinking_hint.setWordWrap(True)
        think.addWidget(thinking_hint)
        adv.addWidget(self.thinking_group)

        self.advanced_box.setVisible(False)
        conn_layout.addWidget(self.advanced_box)

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
        self.advanced_toggle.toggled.connect(self._on_advanced_toggled)
        self.thinking_mode.currentIndexChanged.connect(self._sync_thinking_enabled)
        self._sync_thinking_enabled()

    # ── 高级配置 ─────────────────────────────────────────────

    def _on_advanced_toggled(self, checked: bool) -> None:
        self.advanced_box.setVisible(checked)
        self.advanced_toggle.setText(("▾ " if checked else "▸ ") + "高级配置")

    def _sync_thinking_enabled(self) -> None:
        """思考深度只在自适应模式有意义，预算只在手动模式有意义。"""
        mode = str(self.thinking_mode.currentData() or "")
        self.thinking_effort.setEnabled(mode == "adaptive")
        self.thinking_budget.setEnabled(mode == "enabled")

    def _update_advanced_visibility(self) -> None:
        """按当前协议显示厂商专属选项（扩展思考仅 Anthropic 协议可用）。"""
        self.thinking_group.setVisible(self._current_protocol() == "anthropic_messages")

    def _current_protocol(self) -> str:
        from meapet.config.store import infer_direct_protocol

        preset = self._selected_preset()
        endpoint = self.endpoint_input.text().strip()
        if preset is not None and preset.api_base and endpoint == preset.api_base:
            return preset.protocol
        return infer_direct_protocol("custom", api_base=endpoint, host="")

    @staticmethod
    def _parse_headers_text(text: str) -> dict:
        """解析 "A: 1; B: 2" 形式的自定义请求头。"""
        headers: dict = {}
        for chunk in str(text or "").replace("\n", ";").split(";"):
            item = chunk.strip()
            if not item or ":" not in item:
                continue
            name, _, value = item.partition(":")
            name = name.strip()
            if name:
                headers[name] = value.strip()
        return headers

    @staticmethod
    def _format_headers_text(headers: dict) -> str:
        return "; ".join(f"{k}: {v}" for k, v in (headers or {}).items())

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
        # 预设自带的建议请求头填进高级区，用户可见可改。
        if preset.headers:
            self.headers_input.setText(self._format_headers_text(preset.headers_dict))
        self._update_advanced_visibility()


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
        preset = self._selected_preset()
        # 协议决定鉴权头形式；未选预设时按地址推断，保证与真正发起对话时一致。
        if preset is not None:
            protocol = preset.protocol
            extra_headers = preset.headers_dict
        else:
            from meapet.config.store import infer_direct_protocol
            protocol = infer_direct_protocol("custom", api_base=base_url, host="")
            extra_headers = {}
        thread = threading.Thread(
            target=self._fetch_models_worker,
            args=(base_url, api_key, protocol, extra_headers),
            name="meapet-wizard-fetch-models",
            daemon=True,
        )
        thread.start()

    def _fetch_models_worker(
        self,
        base_url: str,
        api_key: str,
        protocol: str = "openai_chat",
        extra_headers: dict | None = None,
    ) -> None:
        """后台线程：按供应商协议查询可用模型列表。

        鉴权头必须与真正发起对话时一致（Anthropic 用 x-api-key + anthropic-version，
        其余用 Authorization: Bearer），否则这里测得通/不通都没有参考价值。
        另外必须带 User-Agent：不少网关（Cloudflare 等）会直接 403 掉没有 UA 的请求。
        """
        names: list[str] = []
        error = ""

        headers = {
            "Accept": "application/json",
            # 缺省 UA 会被 Cloudflare 之类的防护规则拦成 403（error code 1010）。
            "User-Agent": _MODELS_USER_AGENT,
        }
        for key, value in (extra_headers or {}).items():
            name = str(key or "").strip()
            if name and name.lower() not in _FETCH_PROTECTED_HEADERS:
                headers[name] = str(value)
        # $VAR 占位符在向导里无法解析，视作未填写（仍尝试匿名查询）。
        usable_key = api_key if api_key and not api_key.startswith("$") else ""
        if usable_key:
            if protocol == "anthropic_messages":
                headers["x-api-key"] = usable_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["Authorization"] = f"Bearer {usable_key}"

        if protocol == "ollama_chat":
            # Ollama 原生接口列的是本地已 pull 的模型。
            url = urljoin(_normalize_base_url(base_url), "api/tags")
        else:
            url = urljoin(_normalize_base_url(base_url), "models")

        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict):
                models_raw = data.get("data") or data.get("models") or []
                if isinstance(models_raw, list):
                    names = [
                        str(m.get("id") or m.get("name") or "")
                        for m in models_raw
                        if isinstance(m, dict)
                    ]
                    names = [n for n in names if n]
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:100].strip()
            except Exception:
                pass
            if e.code in (401, 403):
                error = (
                    f"鉴权或访问被拒（HTTP {e.code}）。请检查 API Key 是否正确"
                    f"、是否有权访问该地址。{('服务端返回：' + body) if body else ''}"
                )
            elif e.code == 404:
                error = (
                    f"接口不存在（HTTP 404）：{url}。"
                    "请确认 API 地址填的是基础地址（通常以 /v1 结尾）。"
                )
            else:
                error = f"HTTP {e.code}: {e.reason}{('；' + body) if body else ''}"
        except urllib.error.URLError as e:
            error = f"连接失败：{e.reason}"
        except Exception as e:
            error = str(e)[:120] or type(e).__name__

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
        # 高级区里的自定义头是最终来源（选预设时已把建议值填进去，用户可改）。
        headers = self._parse_headers_text(self.headers_input.text())
        profile = {
            "provider": "custom",
            "protocol": protocol,
            "api_base": endpoint,
            "host": "",
            "model": self.model_combo.currentText().strip(),
            "api_key": str(api_key or self.direct_api_key_input.text()).strip(),
            "temperature": self.temperature_input.value(),
            "max_tokens": self.max_tokens_input.value(),
        }
        if headers:
            profile["headers"] = headers
        if self.timeout_input.value() > 0:
            profile["timeout_seconds"] = float(self.timeout_input.value())
        proxy = self.proxy_input.text().strip()
        if proxy:
            profile["proxy"] = proxy
        thinking_mode = str(self.thinking_mode.currentData() or "")
        if protocol == "anthropic_messages" and thinking_mode:
            thinking: dict = {"type": thinking_mode}
            if thinking_mode == "adaptive":
                effort = str(self.thinking_effort.currentData() or "")
                if effort:
                    thinking["effort"] = effort
            elif self.thinking_budget.value() > 0:
                thinking["budget"] = self.thinking_budget.value()
            profile["thinking"] = thinking
        return profile

    def apply_direct_profile(self, profile: dict) -> None:
        """Restore form fields from a previously saved profile.

        Provider is not restored as a brand selection — identity is always custom.
        下拉只按已保存的 api_base 回选到对应预设，纯属方便查看与再次编辑。
        """
        profile = profile or {}
        saved_headers = profile.get("headers")
        self._custom_headers = (
            {str(k): str(v) for k, v in saved_headers.items()}
            if isinstance(saved_headers, dict)
            else {}
        )
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

        # 高级配置回填；有非默认值时自动展开，避免用户以为配置丢了。
        self.headers_input.setText(self._format_headers_text(self._custom_headers))
        try:
            self.timeout_input.setValue(int(float(profile.get("timeout_seconds") or 0)))
        except (TypeError, ValueError):
            self.timeout_input.setValue(0)
        self.proxy_input.setText(str(profile.get("proxy") or ""))
        thinking = profile.get("thinking")
        thinking = thinking if isinstance(thinking, dict) else {}
        mode_index = self.thinking_mode.findData(
            str(thinking.get("type") or "").strip().lower()
        )
        self.thinking_mode.setCurrentIndex(max(0, mode_index))
        effort_index = self.thinking_effort.findData(
            str(thinking.get("effort") or "").strip().lower()
        )
        self.thinking_effort.setCurrentIndex(max(0, effort_index))
        try:
            self.thinking_budget.setValue(int(thinking.get("budget") or 0))
        except (TypeError, ValueError):
            self.thinking_budget.setValue(0)
        self._sync_thinking_enabled()
        self._update_advanced_visibility()
        if self._custom_headers or profile.get("timeout_seconds") or \
                profile.get("proxy") or thinking:
            self.advanced_toggle.setChecked(True)

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
