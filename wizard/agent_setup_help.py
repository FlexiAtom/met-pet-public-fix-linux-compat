"""配置中心内的 Hermes / OpenClaw 接入说明。"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from importlib.util import find_spec as default_find_spec
from pathlib import Path
from textwrap import dedent
from typing import Callable

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from meapet.config.defaults import (
    DEFAULT_HERMES_WS_URL,
    DEFAULT_OPENCLAW_WS_URL,
)
from meapet.dependencies import (
    CRYPTOGRAPHY_REQUIREMENT,
    WEBSOCKETS_REQUIREMENT,
)
from meapet.message_dialog import MESSAGE_DIALOG_STYLE, MeaMessageDialog
from meapet.ui_theme import (
    FONT_FAMILY,
    GRADIENT_RAISED,
    MIN_TARGET_SIZE,
    MONO_FONT_FAMILY,
    PALETTE,
    RADIUS_SMALL,
    rgba,
    seam_highlight,
    set_scaled_stylesheet,
)
from wizard.styles import set_status


_HERMES_HELP = dedent(
    f"""\
    MeaPet 连接的是 hermes serve 提供的原生 TUI Gateway，不是 8642
    端口的 HTTP API。WebSocket 访问令牌与模型服务商 API Key 是两套凭据。

    1. 先确认 Hermes 自己可以回复

       hermes model
       hermes doctor

    2. 固定 WebSocket 令牌并启动服务（Windows PowerShell）

       $token = python -c "import secrets; print(secrets.token_urlsafe(32))"
       [Environment]::SetEnvironmentVariable(
           "MEA_PET_HERMES_DASHBOARD_SESSION_TOKEN", $token, "User"
       )
       $env:MEA_PET_HERMES_DASHBOARD_SESSION_TOKEN = $token
       hermes serve --host 127.0.0.1 --port 9119

    3. 在当前 Agent 配置中填写

       Agent 类型：Hermes Agent
       WebSocket 地址：{DEFAULT_HERMES_WS_URL}
       访问令牌：$MEA_PET_HERMES_DASHBOARD_SESSION_TOKEN
       当前会话 ID / 长期记忆 Key：首次接入时留空
       最近对话轮数：保持 5 即可

       也可以直接粘贴实际令牌，但它必须与 hermes serve 进程看到的值完全
       一致。地址本身不要追加 ?token=...，MeaPet 会在握手时处理。

    4. 如果刚设置了环境变量，请关闭旧进程，并从新开的 PowerShell 运行
       python setup_wizard.py；也可以把 $token 的实际值直接粘贴到令牌
       字段。随后点击“测试 Agent 连接”，出现“Agent 握手与能力检查正常”
       后保存。

    跨机器使用时，让 Hermes 继续监听远端 127.0.0.1，并用 SSH 把远端
    9119 映射到本机；不要直接把 Hermes 端口暴露到公网。
    """
)


_OPENCLAW_HELP = dedent(
    f"""\
    MeaPet 连接的是 OpenClaw Gateway WebSocket。Gateway 访问令牌与
    OpenAI、Anthropic 等模型服务商 API Key 是两套凭据。

    1. 先完成 OpenClaw 初始化并确认它自己可以回复

       openclaw onboard --install-daemon
       openclaw gateway status
       openclaw models status

       未安装后台服务时，也可以前台运行：
       openclaw gateway --port 18789

    2. 读取当前 Gateway 令牌

       openclaw config get gateway.auth.token

       如果尚未配置令牌，请运行：
       openclaw configure --section gateway

       要让 MeaPet 通过环境变量读取现有令牌，可在 PowerShell 中设置：

       $gatewayToken = "<当前 Gateway 实际使用的 token>"
       [Environment]::SetEnvironmentVariable(
           "MEA_PET_OPENCLAW_GATEWAY_TOKEN", $gatewayToken, "User"
       )
       $env:MEA_PET_OPENCLAW_GATEWAY_TOKEN = $gatewayToken

    3. 在当前 Agent 配置中填写

       Agent 类型：OpenClaw Gateway
       WebSocket 地址：{DEFAULT_OPENCLAW_WS_URL}
       访问令牌：$MEA_PET_OPENCLAW_GATEWAY_TOKEN
       当前会话 ID / 长期记忆 Key：首次接入时留空

       MeaPet 中的令牌必须与 Gateway 实际使用的令牌完全一致。

    4. 关闭旧进程，并从新开的 PowerShell 运行 python setup_wizard.py；
       也可以直接粘贴 Gateway 的实际令牌。点击“测试 Agent 连接”。
       第一次连接如果提示需要配对，请在 Gateway 所在机器运行：

       openclaw devices list
       openclaw devices approve <requestId>

       请核对请求后批准准确的 requestId，然后回到 MeaPet 再测试一次。
       配对成功后设备身份会持久保存，正常重启不需要重复批准。

    跨机器连接优先使用 wss:// 或 SSH 回环隧道。不要在公网连接中打开
    “明确允许远程明文 WS”。
    """
)


@dataclass(frozen=True)
class AgentDependencyReport:
    """当前 Python 对指定 Agent 的运行依赖诊断。"""

    ready: bool
    summary: str
    detail: str
    install_command: str


_AGENT_DEPENDENCY_SPECS = {
    "hermes": (
        ("websockets", "websockets", WEBSOCKETS_REQUIREMENT, 13, 16),
    ),
    "openclaw": (
        ("websockets", "websockets", WEBSOCKETS_REQUIREMENT, 13, 16),
        (
            "cryptography",
            "cryptography",
            CRYPTOGRAPHY_REQUIREMENT,
            42,
            None,
        ),
    ),
}


def _major_version(value: str) -> int | None:
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def inspect_agent_dependencies(
    agent_kind: str,
    *,
    find_spec: Callable[[str], object | None] = default_find_spec,
    get_version: Callable[[str], str] = importlib_metadata.version,
    executable: str | Path | None = None,
) -> AgentDependencyReport:
    """检查当前解释器是否具备 Agent WebSocket 运行依赖。

    这里只做本地模块与版本检查，不导入 Agent 适配器，也不进行网络请求。
    """

    normalized = (
        "openclaw"
        if str(agent_kind or "").strip().lower() == "openclaw"
        else "hermes"
    )
    python_executable = Path(executable or sys.executable)
    installed: list[str] = []
    issues: list[tuple[str, str]] = []

    for module, distribution, requirement, minimum, maximum in (
        _AGENT_DEPENDENCY_SPECS[normalized]
    ):
        try:
            available = find_spec(module) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        if not available:
            issues.append((module, requirement))
            continue

        try:
            resolved_version = str(get_version(distribution) or "").strip()
        except Exception:
            resolved_version = ""
        major = _major_version(resolved_version)
        compatible = (
            major is None
            or (
                major >= minimum
                and (maximum is None or major < maximum)
            )
        )
        if not compatible:
            shown = resolved_version or "未知版本"
            issues.append((f"{module} {shown}", requirement))
            continue
        installed.append(
            f"{module} {resolved_version}"
            if resolved_version
            else f"{module}（版本未知）"
        )

    if issues:
        names = "、".join(name for name, _requirement in issues)
        requirements = " ".join(
            f'"{requirement}"' for _name, requirement in issues
        )
        command = (
            f'"{python_executable}" -m pip install --upgrade {requirements}'
        )
        return AgentDependencyReport(
            ready=False,
            summary=f"依赖未就绪：{names}",
            detail=(
                f"当前 Python：{python_executable}\n"
                "缺少或版本不兼容，暂时无法建立 Agent WebSocket 连接。"
            ),
            install_command=command,
        )

    return AgentDependencyReport(
        ready=True,
        summary="运行依赖已就绪",
        detail=(
            f"当前 Python：{python_executable}\n"
            f"已检测：{' · '.join(installed)}"
        ),
        install_command="",
    )


def _guide_html(text: str) -> str:
    """把现有纯文本步骤整理成标题、正文和可复制命令块。"""

    blocks: list[str] = []
    paragraph: list[str] = []
    code: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(
                f"<p>{html.escape(' '.join(paragraph))}</p>"
            )
            paragraph.clear()

    def flush_code() -> None:
        if code:
            blocks.append(
                "<pre>"
                + html.escape("\n".join(code))
                + "</pre>"
            )
            code.clear()

    for raw_line in str(text or "").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            flush_paragraph()
            flush_code()
            continue
        if re.match(r"^\d+\.\s", stripped):
            flush_paragraph()
            flush_code()
            blocks.append(f"<h3>{html.escape(stripped)}</h3>")
            continue
        if raw_line.startswith(("   ", "\t")):
            flush_paragraph()
            code.append(stripped)
            continue
        flush_code()
        paragraph.append(stripped)
    flush_paragraph()
    flush_code()

    return f"""
        <html>
        <head>
        <style>
            body {{
                color: {PALETTE['text_primary']};
                font-family: {FONT_FAMILY};
                font-size: 14px;
            }}
            p {{
                margin: 0 0 12px 0;
                line-height: 1.5;
            }}
            h3 {{
                color: {PALETTE['primary']};
                font-size: 15px;
                font-weight: 700;
                margin: 18px 0 8px 0;
            }}
            pre {{
                color: {PALETTE['text_secondary']};
                background-color: transparent;
                border: none;
                border-left: 3px solid {PALETTE['primary']};
                font-family: {MONO_FONT_FAMILY};
                font-size: 12px;
                white-space: pre-wrap;
                margin: 4px 0 12px 0;
                padding: 6px 0 6px 12px;
            }}
        </style>
        </head>
        <body>{''.join(blocks)}</body>
        </html>
    """


_AGENT_HELP_STYLE = f"""
    QFrame#AgentDiagnosticCard {{
        background: {GRADIENT_RAISED};
        border: 1px solid {PALETTE['border']};
        border-top-color: {seam_highlight(82)};
        border-left: 3px solid {rgba(PALETTE['primary'], 145)};
        border-radius: {RADIUS_SMALL}px;
    }}
    QLabel#AgentDiagnosticTitle {{
        background: transparent;
        color: {PALETTE['text_primary']};
        font-size: 15px;
        font-weight: 700;
    }}
    QLabel#AgentDependencyStatus,
    QLabel#AgentConnectionStatus {{
        background: transparent;
        color: {PALETTE['text_muted']};
        font-size: 13px;
        font-weight: 700;
    }}
    QLabel#AgentDependencyStatus[status="success"],
    QLabel#AgentConnectionStatus[status="success"] {{
        color: {PALETTE['success']};
    }}
    QLabel#AgentDependencyStatus[status="warning"],
    QLabel#AgentConnectionStatus[status="warning"] {{
        color: {PALETTE['warning']};
    }}
    QLabel#AgentDependencyStatus[status="error"],
    QLabel#AgentConnectionStatus[status="error"] {{
        color: {PALETTE['danger']};
    }}
    QLabel#AgentDependencyDetail {{
        background: transparent;
        color: {PALETTE['text_secondary']};
        font-size: 12px;
    }}
    QPushButton#MessagePrimaryButton[compactAgentHelp="true"],
    QPushButton#MessageSecondaryButton[compactAgentHelp="true"] {{
        min-height: 42px;
        max-height: 42px;
        padding: 0 16px;
    }}
    QPushButton#MessagePrimaryButton[compactAgentHelp="true"]:focus,
    QPushButton#MessageSecondaryButton[compactAgentHelp="true"]:focus {{
        padding: 0 15px;
    }}
    QPushButton#MessagePrimaryButton[compactAgentHelp="true"]:disabled,
    QPushButton#MessageSecondaryButton[compactAgentHelp="true"]:disabled {{
        background: {rgba(PALETTE['surface_elevated'], 150)};
        color: {rgba(PALETTE['text_muted'], 130)};
        border-color: {rgba(PALETTE['border'], 155)};
    }}
"""


def agent_setup_guide(agent_kind: str) -> tuple[str, str]:
    """返回规范化 Agent 类型对应的窗口标题和接入正文。"""
    if str(agent_kind or "").strip().lower() == "openclaw":
        return "OpenClaw 接入步骤", _OPENCLAW_HELP
    return "Hermes 接入步骤", _HERMES_HELP


class AgentSetupHelpDialog(MeaMessageDialog):
    """依赖、连接状态与接入步骤合并展示的非模态诊断窗口。"""

    def __init__(
        self,
        parent=None,
        *,
        agent_kind: str = "hermes",
        connection_button: QPushButton | None = None,
        connection_status: QLabel | None = None,
    ) -> None:
        self._source_connection_button = connection_button
        self._source_connection_status = connection_status
        self._dependency_report: AgentDependencyReport | None = None
        self._agent_kind = ""
        title, text = agent_setup_guide(agent_kind)
        super().__init__(
            parent,
            title=title,
            text=text,
            icon=QMessageBox.Information,
            buttons=QMessageBox.Close,
            default_button=QMessageBox.Close,
        )
        self.setWindowModality(Qt.NonModal)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setAccessibleDescription(
            "当前 Agent 的依赖、连接测试与接入步骤；按 Escape 关闭"
        )
        set_scaled_stylesheet(
            self,
            MESSAGE_DIALOG_STYLE + _AGENT_HELP_STYLE,
        )
        self.kind_label.setText("接入诊断")
        self.close_button.setAccessibleName("关闭 Agent 接入帮助")
        close_button = self.button(QMessageBox.Close)
        if close_button is not None:
            close_button.setAccessibleDescription("关闭接入帮助并返回配置")
            close_button.setObjectName("MessageSecondaryButton")
            close_button.setProperty("compactAgentHelp", True)
            close_button.setDefault(False)
            close_button.setFixedHeight(MIN_TARGET_SIZE)
            close_button.style().unpolish(close_button)
            close_button.style().polish(close_button)

        self._build_diagnostic_card()
        self.card.layout().insertWidget(2, self.diagnostic_card)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(150)
        self._status_timer.timeout.connect(self._sync_connection_status)
        self._status_timer.start()
        self.set_agent_kind(agent_kind)

    def _build_diagnostic_card(self) -> None:
        card = QFrame(self.card)
        card.setObjectName("AgentDiagnosticCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 13)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("接入前检查", card)
        title.setObjectName("AgentDiagnosticTitle")
        header.addWidget(title)
        header.addStretch(1)
        refresh_button = QPushButton("重新检测", card)
        refresh_button.setObjectName("MessageSecondaryButton")
        refresh_button.setProperty("compactAgentHelp", True)
        refresh_button.setFixedHeight(MIN_TARGET_SIZE)
        refresh_button.setAccessibleName("重新检测 Agent 运行依赖")
        refresh_button.clicked.connect(self._refresh_dependency_status)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        dependency_row = QHBoxLayout()
        dependency_row.setSpacing(8)
        dependency_status = QLabel("正在检测运行依赖…", card)
        dependency_status.setObjectName("AgentDependencyStatus")
        dependency_status.setWordWrap(True)
        dependency_status.setAccessibleName("Agent 运行依赖状态")
        dependency_row.addWidget(dependency_status, 1)
        command_button = QPushButton("复制安装命令", card)
        command_button.setObjectName("MessageSecondaryButton")
        command_button.setProperty("compactAgentHelp", True)
        command_button.setFixedHeight(MIN_TARGET_SIZE)
        command_button.setAccessibleName("复制 Agent 依赖安装命令")
        command_button.clicked.connect(self._copy_install_command)
        dependency_row.addWidget(command_button)
        layout.addLayout(dependency_row)

        dependency_detail = QLabel("", card)
        dependency_detail.setObjectName("AgentDependencyDetail")
        dependency_detail.setWordWrap(True)
        dependency_detail.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        dependency_detail.setAccessibleName("Agent 依赖检测详情")
        layout.addWidget(dependency_detail)

        connection_row = QHBoxLayout()
        connection_row.setSpacing(8)
        connection_status = QLabel("尚未测试", card)
        connection_status.setObjectName("AgentConnectionStatus")
        connection_status.setWordWrap(True)
        connection_status.setAccessibleName("Agent 连接测试状态")
        connection_row.addWidget(connection_status, 1)
        connection_test_button = QPushButton("测试当前连接", card)
        connection_test_button.setObjectName("MessagePrimaryButton")
        connection_test_button.setProperty("compactAgentHelp", True)
        connection_test_button.setFixedHeight(MIN_TARGET_SIZE)
        connection_test_button.setAccessibleName("测试当前 Agent 连接")
        connection_test_button.clicked.connect(
            self._start_connection_test
        )
        connection_row.addWidget(connection_test_button)
        layout.addLayout(connection_row)

        self.diagnostic_card = card
        self.dependency_status = dependency_status
        self.dependency_detail = dependency_detail
        self.dependency_command_button = command_button
        self.dependency_refresh_button = refresh_button
        self.connection_status = connection_status
        self.connection_test_button = connection_test_button

    @property
    def agent_kind(self) -> str:
        return self._agent_kind

    def set_agent_kind(self, agent_kind: str) -> None:
        """切换说明内容，并同步窗口标题与滚动位置。"""
        normalized = (
            "openclaw"
            if str(agent_kind or "").strip().lower() == "openclaw"
            else "hermes"
        )
        title, text = agent_setup_guide(normalized)
        self._agent_kind = normalized
        self.setWindowTitle(title)
        self.setAccessibleName(title)
        self.title_label.setText(title)
        self.body.setHtml(_guide_html(text))
        self.body.verticalScrollBar().setValue(0)
        self._refresh_dependency_status()
        self._sync_connection_status()
        self._sync_size()

    def _refresh_dependency_status(self) -> None:
        report = inspect_agent_dependencies(self._agent_kind)
        self._dependency_report = report
        set_status(
            self.dependency_status,
            "success" if report.ready else "error",
            report.summary,
        )
        self.dependency_detail.setText(report.detail)
        self.dependency_command_button.setVisible(
            bool(report.install_command)
        )
        self._sync_connection_status()
        self._sync_size()

    def _copy_install_command(self) -> None:
        report = self._dependency_report
        if report is None or not report.install_command:
            return
        QApplication.clipboard().setText(report.install_command)
        set_status(
            self.dependency_status,
            "warning",
            "安装命令已复制；执行完成后点击“重新检测”。",
        )

    def _start_connection_test(self) -> None:
        report = self._dependency_report
        if report is None or not report.ready:
            set_status(
                self.connection_status,
                "error",
                "无法测试：请先补齐上方运行依赖。",
            )
            return
        source = self._source_connection_button
        if source is None:
            set_status(
                self.connection_status,
                "error",
                "无法开始测试：未连接到当前配置页。",
            )
            return
        try:
            source.click()
        except RuntimeError:
            set_status(
                self.connection_status,
                "error",
                "无法开始测试：配置页已经关闭。",
            )
            return
        self._sync_connection_status()

    def _sync_connection_status(self) -> None:
        report = self._dependency_report
        if report is not None and not report.ready:
            self.connection_test_button.setEnabled(False)
            self.connection_test_button.setText("依赖未就绪")
            set_status(
                self.connection_status,
                "error",
                "连接测试不可用：请先安装缺少的运行依赖。",
            )
            return

        source_button = self._source_connection_button
        source_status = self._source_connection_status
        if source_button is None or source_status is None:
            self.connection_test_button.setEnabled(False)
            self.connection_test_button.setText("返回配置页测试")
            set_status(
                self.connection_status,
                "muted",
                "尚未测试 · 请从 Agent 配置页打开此窗口。",
            )
            return

        try:
            source_enabled = source_button.isEnabled()
            source_text = source_status.text() or "尚未测试"
            source_state = str(
                source_status.property("status") or "muted"
            )
        except RuntimeError:
            self.connection_test_button.setEnabled(False)
            self.connection_test_button.setText("配置页已关闭")
            set_status(
                self.connection_status,
                "error",
                "无法继续测试：配置页已经关闭。",
            )
            return

        self.connection_test_button.setEnabled(source_enabled)
        self.connection_test_button.setText(
            "测试当前连接" if source_enabled else "正在测试…"
        )
        set_status(
            self.connection_status,
            source_state,
            source_text,
        )

    def _sync_size(self) -> None:
        """帮助窗使用宽正文，并为诊断卡与固定底栏预留真实高度。"""

        area = self._reference_area()
        available_width = area.width() - 48 if area is not None else 760
        target_width = max(360, min(760, available_width))
        max_window_height = (
            max(320, area.height() - 32)
            if area is not None
            else 720
        )

        self.setMinimumWidth(target_width)
        self.setMaximumWidth(target_width)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)

        document = self.body.document()
        document.setTextWidth(max(280, target_width - 96))
        text_height = int(document.size().height()) + 12

        self.body.setFixedHeight(80)
        self.card.layout().activate()
        self.layout().activate()
        chrome_height = max(180, self.sizeHint().height() - 80)
        body_ceiling = max(96, max_window_height - chrome_height)
        body_height = min(
            max(180, text_height),
            min(460, body_ceiling),
        )
        if body_ceiling < 180:
            body_height = body_ceiling
        self.body.setFixedHeight(body_height)

        self.card.layout().activate()
        self.layout().activate()
        target_height = min(self.sizeHint().height(), max_window_height)
        self.setFixedHeight(max(240, target_height))


__all__ = [
    "AgentDependencyReport",
    "AgentSetupHelpDialog",
    "agent_setup_guide",
    "inspect_agent_dependencies",
]
