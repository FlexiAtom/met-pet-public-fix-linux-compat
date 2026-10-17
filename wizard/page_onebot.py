"""OneBot（QQ 通道）配置：五种通信方式、准入规则与队列。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, List

from PyQt5.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from meapet.onebot.config import DEFAULT_ONEBOT
from meapet.ui_theme import MIN_TARGET_SIZE
from wizard.styles import STYLE_INPUT, STYLE_PAGE_CARD, field_label, set_status
from wizard.widgets import WheelSafeComboBox

# 逗号（中英文）、分号、顿号与空白都当作分隔符。
_INT_SPLIT_RE = re.compile(r"[,，;；、\s]+")

_TOKEN_PLACEHOLDER = "留空表示不校验；也可填 $ENV_VAR 占位符"


def parse_int_list(text: Any) -> List[int]:
    """把分隔文本解析为非负整数列表：忽略垃圾、去重、保持输入顺序。"""
    if isinstance(text, (list, tuple, set)):
        pieces = [str(item) for item in text]
    else:
        if text is None:
            return []
        pieces = _INT_SPLIT_RE.split(str(text))
    out: List[int] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        try:
            number = int(piece)
        except (TypeError, ValueError):
            continue
        if number < 0 or number in out:
            continue
        out.append(number)
    return out


def format_int_list(values: Any) -> str:
    """把整数列表渲染成逗号分隔文本；非法输入退化为空串。"""
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(
        values, Sequence
    ):
        return ""
    out: List[int] = []
    for item in values:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number >= 0 and number not in out:
            out.append(number)
    return ", ".join(str(number) for number in out)


def parse_url_list(text: Any) -> List[str]:
    """把逗号/换行分隔的文本解析成 http(s) URL 列表；非法项丢弃、去重、保序。"""
    if not isinstance(text, str):
        return []
    out: List[str] = []
    for chunk in text.replace("\n", ",").split(","):
        url = chunk.strip()
        if not url.startswith(("http://", "https://")):
            continue
        if url not in out:
            out.append(url)
    return out


def format_url_list(values: Any) -> str:
    """把 URL 列表渲染成逗号分隔文本；非法输入退化为空串。"""
    if isinstance(values, (str, bytes, bytearray)) or not isinstance(
        values, Sequence
    ):
        return ""
    out: List[str] = []
    for item in values:
        url = _as_text(item)
        if not url.startswith(("http://", "https://")):
            continue
        if url not in out:
            out.append(url)
    return ", ".join(out)


def _as_dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _as_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    if number < lo or number > hi:
        return default
    return number


def _as_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number < lo or number > hi:
        return default
    return number


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, (dict, list, tuple, set)):
        return default
    return str(value).strip()


def _field(layout, label: str, widget, accessible_name: str) -> None:
    layout.addWidget(field_label(label))
    widget.setAccessibleName(accessible_name)
    widget.setMinimumHeight(MIN_TARGET_SIZE)
    layout.addWidget(widget)


class OneBotPage(QFrame):
    """QQ 通道配置：发起端、接收端与下游端点各自独立开关。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("PageCard")
        self.setStyleSheet(STYLE_PAGE_CARD)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(12)

        title = QLabel("OneBot（QQ 通道）")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        description = QLabel(
            "让 MeaPet 通过 OneBot v11 与 NapCat / SnowLuma / dsh 插件互通。"
            "发起端主动连出去，接收端自己监听等对方连进来，五种方式可以任意组合。"
        )
        description.setObjectName("PageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.enabled_cb = QCheckBox("启用 OneBot（QQ 通道）")
        self.enabled_cb.setAccessibleName("启用 OneBot QQ 通道")
        self.enabled_cb.setAccessibleDescription(
            "关闭后桌宠不会连接或响应任何 QQ 消息，下面的配置会原样保留"
        )
        layout.addWidget(self.enabled_cb)
        enabled_hint = QLabel(
            "启用后至少需要一种通信方式；只接收事件不会回复，"
            "发送消息依赖 http_api 或 forward_ws。"
        )
        enabled_hint.setObjectName("HelperText")
        enabled_hint.setWordWrap(True)
        layout.addWidget(enabled_hint)

        identity_row = QHBoxLayout()
        identity_col = QVBoxLayout()
        identity_col.addWidget(field_label("机器人自身 QQ 号："))
        self.self_ids_input = QLineEdit()
        self.self_ids_input.setStyleSheet(STYLE_INPUT)
        self.self_ids_input.setPlaceholderText("例如 123456789, 987654321")
        self.self_ids_input.setAccessibleName("机器人自身 QQ 号")
        self.self_ids_input.setAccessibleDescription(
            "逗号分隔的多个账号；留空表示不限制，由对端上报的 self_id 判断"
        )
        identity_col.addWidget(self.self_ids_input)
        identity_row.addLayout(identity_col, 2)

        mode_col = QVBoxLayout()
        mode_col.addWidget(field_label("回复方式："))
        self.reply_mode_combo = WheelSafeComboBox()
        self.reply_mode_combo.addItem("桌宠自己回复（pet）", "pet")
        self.reply_mode_combo.addItem("只转发给下游（hub）", "hub")
        self.reply_mode_combo.addItem("两者都做（both）", "both")
        self.reply_mode_combo.setAccessibleName("OneBot 回复方式")
        self.reply_mode_combo.setAccessibleDescription(
            "pet 表示桌宠自己回复，hub 表示只转给下游消费者，both 表示两者都做"
        )
        mode_col.addWidget(self.reply_mode_combo)
        identity_row.addLayout(mode_col, 1)
        layout.addLayout(identity_row)
        self_ids_hint = QLabel(
            "多个账号用英文或中文逗号分隔；留空表示不限制（空=不限）。"
        )
        self_ids_hint.setObjectName("HelperText")
        self_ids_hint.setWordWrap(True)
        layout.addWidget(self_ids_hint)

        # ── 发起端 · 正向 WebSocket ────────────────────────────────
        # 勾选框放在分区**外面**：这样分区可以整段折叠（未启用时不占高度），
        # 而"启用"这个入口始终可见——能折叠的必须是字段，绝不能是这个开关。
        self.forward_enabled = QCheckBox("启用正向 WebSocket")
        self.forward_enabled.setAccessibleName("启用正向 WebSocket")
        self.forward_enabled.setToolTip(
            "主动连 NapCat / SnowLuma / dsh 插件，事件与发送都走这条连接。"
        )
        layout.addWidget(self.forward_enabled)
        forward_frame, forward_layout = self._make_section(
            layout,
            "发起端 · 正向 WebSocket",
            "主动连 NapCat / SnowLuma / dsh 插件，事件与发送都走这条连接。",
        )
        self.forward_url = QLineEdit(str(DEFAULT_ONEBOT["forward_ws"]["url"]))
        self.forward_url.setStyleSheet(STYLE_INPUT)
        self.forward_url.setPlaceholderText("ws://127.0.0.1:3001/")
        _field(forward_layout, "对端地址：", self.forward_url, "正向 WebSocket 地址")
        self._build_token_field(
            forward_layout,
            "访问令牌：",
            "forward_token",
            "正向 WebSocket 访问令牌",
            "填 $ENV_VAR 可交给环境变量；留空表示对端不校验",
        )
        self.forward_reconnect = QCheckBox("断线自动重连")
        self.forward_reconnect.setAccessibleName("正向 WebSocket 断线自动重连")
        forward_layout.addWidget(self.forward_reconnect)
        reconnect_row = QHBoxLayout()
        reconnect_row.addWidget(field_label("首次重连间隔（毫秒）：", inline=True))
        self.forward_reconnect_initial_ms = QSpinBox()
        self.forward_reconnect_initial_ms.setRange(100, 600000)
        self.forward_reconnect_initial_ms.setValue(
            int(DEFAULT_ONEBOT["forward_ws"]["reconnect_initial_ms"])
        )
        self.forward_reconnect_initial_ms.setAccessibleName("正向 WebSocket 首次重连间隔")
        self.forward_reconnect_initial_ms.setMinimumHeight(MIN_TARGET_SIZE)
        reconnect_row.addWidget(self.forward_reconnect_initial_ms)
        reconnect_row.addStretch()
        reconnect_row.addWidget(field_label("最大重连间隔（毫秒）：", inline=True))
        self.forward_reconnect_max_ms = QSpinBox()
        self.forward_reconnect_max_ms.setRange(100, 3600000)
        self.forward_reconnect_max_ms.setValue(
            int(DEFAULT_ONEBOT["forward_ws"]["reconnect_max_ms"])
        )
        self.forward_reconnect_max_ms.setAccessibleName("正向 WebSocket 最大重连间隔")
        self.forward_reconnect_max_ms.setMinimumHeight(MIN_TARGET_SIZE)
        reconnect_row.addWidget(self.forward_reconnect_max_ms)
        forward_layout.addLayout(reconnect_row)
        reconnect_hint = QLabel(
            "退避从首次间隔开始翻倍，直到最大间隔；最大间隔小于首次间隔时会自动抬高。"
        )
        reconnect_hint.setObjectName("HelperText")
        reconnect_hint.setWordWrap(True)
        forward_layout.addWidget(reconnect_hint)

        # ── 接收端 · 反向 WebSocket ────────────────────────────────
        self.reverse_enabled = QCheckBox("启用反向 WebSocket")
        self.reverse_enabled.setAccessibleName("启用反向 WebSocket")
        self.reverse_enabled.setToolTip(
            "自己监听端口，等 NapCat / SnowLuma 作为客户端连进来。"
        )
        layout.addWidget(self.reverse_enabled)
        reverse_frame, reverse_layout = self._make_section(
            layout,
            "接收端 · 反向 WebSocket",
            "自己监听端口，等 NapCat / SnowLuma 作为客户端连进来。",
        )
        reverse_host_row = QHBoxLayout()
        host_col = QVBoxLayout()
        host_col.addWidget(field_label("监听地址："))
        self.reverse_host = QLineEdit(
            str(DEFAULT_ONEBOT["reverse_ws"]["host"])
        )
        self.reverse_host.setStyleSheet(STYLE_INPUT)
        self.reverse_host.setAccessibleName("反向 WebSocket 监听地址")
        host_col.addWidget(self.reverse_host)
        reverse_host_row.addLayout(host_col, 2)
        port_col = QVBoxLayout()
        port_col.addWidget(field_label("监听端口："))
        self.reverse_port = QSpinBox()
        self.reverse_port.setRange(1, 65535)
        self.reverse_port.setValue(int(DEFAULT_ONEBOT["reverse_ws"]["port"]))
        self.reverse_port.setAccessibleName("反向 WebSocket 监听端口")
        self.reverse_port.setMinimumHeight(MIN_TARGET_SIZE)
        port_col.addWidget(self.reverse_port)
        reverse_host_row.addLayout(port_col, 1)
        reverse_layout.addLayout(reverse_host_row)
        self.reverse_path = QLineEdit(str(DEFAULT_ONEBOT["reverse_ws"]["path"]))
        self.reverse_path.setStyleSheet(STYLE_INPUT)
        _field(reverse_layout, "路径：", self.reverse_path, "反向 WebSocket 路径")
        self._build_token_field(
            reverse_layout,
            "访问令牌：",
            "reverse_token",
            "反向 WebSocket 访问令牌",
            "填 $ENV_VAR 可交给环境变量；留空表示不校验",
        )
        self.reverse_allow_insecure_remote = QCheckBox("明确允许非回环地址监听")
        self.reverse_allow_insecure_remote.setAccessibleName(
            "反向 WebSocket 允许非回环地址监听"
        )
        self.reverse_allow_insecure_remote.setAccessibleDescription(
            "仅用于可信内网；公网暴露会让事件和令牌以明文传输"
        )
        reverse_layout.addWidget(self.reverse_allow_insecure_remote)
        self.reverse_insecure_warning = self._make_warning(
            reverse_layout,
            "警告：监听非回环地址后，事件和令牌可能在局域网中以明文传输。",
        )

        # ── 发起端 · HTTP API ──────────────────────────────────────
        self.http_api_enabled = QCheckBox("启用 HTTP API")
        self.http_api_enabled.setAccessibleName("启用 HTTP API")
        self.http_api_enabled.setToolTip(
            "调用对端的 HTTP 接口发送消息，不需要保持长连接。"
        )
        layout.addWidget(self.http_api_enabled)
        http_api_frame, http_api_layout = self._make_section(
            layout,
            "发起端 · HTTP API",
            "调用对端的 HTTP 接口发送消息，不需要保持长连接。",
        )
        self.http_api_base = QLineEdit(
            str(DEFAULT_ONEBOT["http_api"]["api_base"])
        )
        self.http_api_base.setStyleSheet(STYLE_INPUT)
        self.http_api_base.setPlaceholderText("http://127.0.0.1:3000")
        _field(http_api_layout, "接口地址：", self.http_api_base, "HTTP API 地址")
        self._build_token_field(
            http_api_layout,
            "访问令牌：",
            "http_api_token",
            "HTTP API 访问令牌",
            "填 $ENV_VAR 可交给环境变量；留空表示对端不校验",
        )
        timeout_row = QHBoxLayout()
        timeout_row.addWidget(field_label("请求超时（秒）：", inline=True))
        self.http_api_timeout_s = QDoubleSpinBox()
        self.http_api_timeout_s.setRange(0.5, 120.0)
        self.http_api_timeout_s.setDecimals(1)
        self.http_api_timeout_s.setSingleStep(0.5)
        self.http_api_timeout_s.setValue(
            float(DEFAULT_ONEBOT["http_api"]["timeout_s"])
        )
        self.http_api_timeout_s.setAccessibleName("HTTP API 请求超时")
        self.http_api_timeout_s.setMinimumHeight(MIN_TARGET_SIZE)
        timeout_row.addWidget(self.http_api_timeout_s)
        timeout_row.addStretch()
        http_api_layout.addLayout(timeout_row)

        # ── 接收端 · 事件 HTTP POST ────────────────────────────────
        self.http_post_enabled = QCheckBox("启用事件 HTTP POST")
        self.http_post_enabled.setAccessibleName("启用事件 HTTP POST")
        self.http_post_enabled.setToolTip(
            "对端把事件推给我们；发送消息仍然依赖 http_api。"
        )
        layout.addWidget(self.http_post_enabled)
        http_post_frame, http_post_layout = self._make_section(
            layout,
            "接收端 · 事件 HTTP POST",
            "对端把事件推给我们；发送消息仍然依赖 http_api。",
        )
        post_host_row = QHBoxLayout()
        post_host_col = QVBoxLayout()
        post_host_col.addWidget(field_label("监听地址："))
        self.http_post_host = QLineEdit(
            str(DEFAULT_ONEBOT["http_post"]["host"])
        )
        self.http_post_host.setStyleSheet(STYLE_INPUT)
        self.http_post_host.setAccessibleName("事件 HTTP POST 监听地址")
        post_host_col.addWidget(self.http_post_host)
        post_host_row.addLayout(post_host_col, 2)
        post_port_col = QVBoxLayout()
        post_port_col.addWidget(field_label("监听端口："))
        self.http_post_port = QSpinBox()
        self.http_post_port.setRange(1, 65535)
        self.http_post_port.setValue(int(DEFAULT_ONEBOT["http_post"]["port"]))
        self.http_post_port.setAccessibleName("事件 HTTP POST 监听端口")
        self.http_post_port.setMinimumHeight(MIN_TARGET_SIZE)
        post_port_col.addWidget(self.http_post_port)
        post_host_row.addLayout(post_port_col, 1)
        http_post_layout.addLayout(post_host_row)
        self.http_post_path = QLineEdit(
            str(DEFAULT_ONEBOT["http_post"]["path"])
        )
        self.http_post_path.setStyleSheet(STYLE_INPUT)
        _field(http_post_layout, "路径：", self.http_post_path, "事件 HTTP POST 路径")
        self._build_token_field(
            http_post_layout,
            "访问令牌：",
            "http_post_token",
            "事件 HTTP POST 访问令牌",
            "填 $ENV_VAR 可交给环境变量；留空表示不校验",
        )
        self.http_post_allow_insecure_remote = QCheckBox("明确允许非回环地址监听")
        self.http_post_allow_insecure_remote.setAccessibleName(
            "事件 HTTP POST 允许非回环地址监听"
        )
        http_post_layout.addWidget(self.http_post_allow_insecure_remote)
        self.http_post_insecure_warning = self._make_warning(
            http_post_layout,
            "警告：监听非回环地址后，事件和令牌可能在局域网中以明文传输。",
        )
        self.http_post_hint = QLabel(
            "只启用事件 POST 时机器人只能收不能发；请同时启用 HTTP API 或正向 WebSocket。"
        )
        self.http_post_hint.setObjectName("HelperText")
        self.http_post_hint.setWordWrap(True)
        http_post_layout.addWidget(self.http_post_hint)

        # ── 下游消费者端点 ────────────────────────────────────────
        self.hub_enabled = QCheckBox("启用下游消费者端点")
        self.hub_enabled.setAccessibleName("启用 OneBot 下游消费者端点")
        self.hub_enabled.setToolTip(
            "dsh 插件等以 OneBot 客户端身份接入，可收事件、也可发消息。"
        )
        layout.addWidget(self.hub_enabled)
        hub_frame, hub_layout = self._make_section(
            layout,
            "下游消费者端点",
            "dsh 插件等以 OneBot 客户端身份接入，可收事件、也可发消息。",
        )
        hub_host_row = QHBoxLayout()
        hub_host_col = QVBoxLayout()
        hub_host_col.addWidget(field_label("监听地址："))
        self.hub_host = QLineEdit(str(DEFAULT_ONEBOT["hub"]["host"]))
        self.hub_host.setStyleSheet(STYLE_INPUT)
        self.hub_host.setAccessibleName("下游端点监听地址")
        hub_host_col.addWidget(self.hub_host)
        hub_host_row.addLayout(hub_host_col, 2)
        hub_port_col = QVBoxLayout()
        hub_port_col.addWidget(field_label("监听端口："))
        self.hub_port = QSpinBox()
        self.hub_port.setRange(1, 65535)
        self.hub_port.setValue(int(DEFAULT_ONEBOT["hub"]["port"]))
        self.hub_port.setAccessibleName("下游端点监听端口")
        self.hub_port.setMinimumHeight(MIN_TARGET_SIZE)
        hub_port_col.addWidget(self.hub_port)
        hub_host_row.addLayout(hub_port_col, 1)
        hub_layout.addLayout(hub_host_row)
        hub_path_row = QHBoxLayout()
        hub_ws_col = QVBoxLayout()
        hub_ws_col.addWidget(field_label("WebSocket 路径："))
        self.hub_ws_path = QLineEdit(str(DEFAULT_ONEBOT["hub"]["ws_path"]))
        self.hub_ws_path.setStyleSheet(STYLE_INPUT)
        self.hub_ws_path.setAccessibleName("下游端点 WebSocket 路径")
        hub_ws_col.addWidget(self.hub_ws_path)
        hub_path_row.addLayout(hub_ws_col, 1)
        hub_http_col = QVBoxLayout()
        hub_http_col.addWidget(field_label("HTTP 路径："))
        self.hub_http_path = QLineEdit(str(DEFAULT_ONEBOT["hub"]["http_path"]))
        self.hub_http_path.setStyleSheet(STYLE_INPUT)
        self.hub_http_path.setAccessibleName("下游端点 HTTP 路径")
        hub_http_col.addWidget(self.hub_http_path)
        hub_path_row.addLayout(hub_http_col, 1)
        hub_layout.addLayout(hub_path_row)
        self._build_token_field(
            hub_layout,
            "访问令牌：",
            "hub_token",
            "下游端点访问令牌",
            "填 $ENV_VAR 可交给环境变量；留空表示不校验",
        )
        self.hub_publish_events = QCheckBox("向接入方推送事件")
        self.hub_publish_events.setAccessibleName("下游端点推送事件")
        hub_layout.addWidget(self.hub_publish_events)
        self.hub_allow_send = QCheckBox("允许接入方发送消息")
        self.hub_allow_send.setAccessibleName("允许下游端点发送消息")
        hub_layout.addWidget(self.hub_allow_send)
        self.hub_allow_insecure_remote = QCheckBox("明确允许非回环地址监听")
        self.hub_allow_insecure_remote.setAccessibleName("下游端点允许非回环地址监听")
        hub_layout.addWidget(self.hub_allow_insecure_remote)
        self.hub_insecure_warning = self._make_warning(
            hub_layout,
            "警告：监听非回环地址后，事件和令牌可能在局域网中以明文传输。",
        )

        # 事件 HTTP POST 上报（规范 §2.2）：与「接入方连进来」互补，
        # 这里是我们主动把事件推给只会 HTTP 的消费方。
        hub_layout.addWidget(field_label("事件上报地址（逗号分隔，可留空）："))
        self.hub_push_urls = QLineEdit()
        self.hub_push_urls.setStyleSheet(STYLE_INPUT)
        self.hub_push_urls.setPlaceholderText("http://127.0.0.1:9999/onebot/v11/http")
        self.hub_push_urls.setAccessibleName("下游事件 HTTP POST 上报地址列表")
        hub_layout.addWidget(self.hub_push_urls)
        self._build_token_field(
            hub_layout,
            "上报令牌：",
            "hub_push_token",
            "事件上报访问令牌",
            "留空则复用上面的访问令牌",
        )
        hub_timeout_row = QHBoxLayout()
        hub_timeout_row.addWidget(field_label("上报超时（秒）："))
        self.hub_push_timeout = QLineEdit(str(DEFAULT_ONEBOT["hub"]["push_timeout_s"]))
        self.hub_push_timeout.setStyleSheet(STYLE_INPUT)
        self.hub_push_timeout.setAccessibleName("事件上报超时秒数")
        hub_timeout_row.addWidget(self.hub_push_timeout, 1)
        hub_layout.addLayout(hub_timeout_row)

        # ── 准入规则 ──────────────────────────────────────────────
        allow_frame, allow_layout = self._make_section(
            layout,
            "准入规则（只作用于桌宠自己回复）",
            "谁能让桌宠自己回复；白名单留空表示不限制。"
            "注意：这些规则管不到下游 hub 消费者（dsh 插件）——见下方说明。",
        )
        self._allow_frame = allow_frame
        # `reply_mode=hub` 时桌宠根本不回复，这整段**一行都不生效**（§6.1 有文档依据）。
        # 与其让一组"看着能改、其实没用"的控件占着屏幕，不如折叠掉并留一句说明。
        self.hub_allow_note = QLabel(
            "回复方式选了「只转发给下游（hub）」：桌宠自己不回复，"
            "所以上面这组准入规则当前不生效，已折叠；改成 pet/both 就会重新出现。"
        )
        self.hub_allow_note.setObjectName("HelperText")
        self.hub_allow_note.setWordWrap(True)
        layout.addWidget(self.hub_allow_note)
        self.allow_private = QCheckBox("响应私聊消息")
        self.allow_private.setAccessibleName("响应私聊消息")
        allow_layout.addWidget(self.allow_private)
        self.allow_groups = QCheckBox("响应群聊消息")
        self.allow_groups.setAccessibleName("响应群聊消息")
        allow_layout.addWidget(self.allow_groups)
        self.allow_require_at = QCheckBox("群聊必须 @ 机器人才响应")
        self.allow_require_at.setAccessibleName("群聊必须 @ 机器人才响应")
        self.allow_require_at.setAccessibleDescription(
            "关闭后群里任何消息都会触发回复，通常会很吵"
        )
        allow_layout.addWidget(self.allow_require_at)
        allow_users_row = QHBoxLayout()
        allow_users_col = QVBoxLayout()
        allow_users_col.addWidget(field_label("允许的用户 QQ（空=不限）："))
        self.allow_users_input = QLineEdit()
        self.allow_users_input.setStyleSheet(STYLE_INPUT)
        self.allow_users_input.setPlaceholderText("例如 10001, 10002；留空表示不限制")
        self.allow_users_input.setAccessibleName("允许的用户 QQ 列表")
        self.allow_users_input.setAccessibleDescription(
            "逗号分隔的 QQ 号；留空表示不限制"
        )
        allow_users_col.addWidget(self.allow_users_input)
        allow_users_row.addLayout(allow_users_col, 1)
        allow_groups_col = QVBoxLayout()
        allow_groups_col.addWidget(field_label("允许的群号（空=不限）："))
        self.allow_group_ids_input = QLineEdit()
        self.allow_group_ids_input.setStyleSheet(STYLE_INPUT)
        self.allow_group_ids_input.setPlaceholderText("例如 20001, 20002；留空表示不限制")
        self.allow_group_ids_input.setAccessibleName("允许的群号列表")
        self.allow_group_ids_input.setAccessibleDescription(
            "逗号分隔的群号；留空表示不限制"
        )
        allow_groups_col.addWidget(self.allow_group_ids_input)
        allow_users_row.addLayout(allow_groups_col, 1)
        allow_layout.addLayout(allow_users_row)
        allow_hint = QLabel(
            "两个名单都留空表示不限制；填写后只有名单内的用户或群能得到桌宠自己的回复。\n"
            "仅在回复方式包含「桌宠」时生效。接入 hub 的消费者（如 dsh 插件）会收到全部事件，"
            "谁能驱动它由插件侧自己的规则决定 —— 这里填的白名单拦不住那条路。"
        )
        allow_hint.setObjectName("HelperText")
        allow_hint.setWordWrap(True)
        allow_layout.addWidget(allow_hint)

        # ── 队列 ──────────────────────────────────────────────────
        queue_frame, queue_layout = self._make_section(
            layout,
            "队列",
            "限制同时排队的请求数，以及单次回复的最长等待时间。",
        )
        queue_row = QHBoxLayout()
        queue_row.addWidget(field_label("最大排队请求数：", inline=True))
        self.max_pending = QSpinBox()
        self.max_pending.setRange(1, 256)
        self.max_pending.setValue(int(DEFAULT_ONEBOT["queue"]["max_pending"]))
        self.max_pending.setAccessibleName("OneBot 最大排队请求数")
        self.max_pending.setMinimumHeight(MIN_TARGET_SIZE)
        queue_row.addWidget(self.max_pending)
        queue_row.addStretch()
        queue_row.addWidget(field_label("单次回复超时（秒）：", inline=True))
        self.turn_timeout_s = QDoubleSpinBox()
        self.turn_timeout_s.setRange(5.0, 3600.0)
        self.turn_timeout_s.setDecimals(1)
        self.turn_timeout_s.setSingleStep(10.0)
        self.turn_timeout_s.setValue(
            float(DEFAULT_ONEBOT["queue"]["turn_timeout_s"])
        )
        self.turn_timeout_s.setAccessibleName("OneBot 单次回复超时")
        self.turn_timeout_s.setMinimumHeight(MIN_TARGET_SIZE)
        queue_row.addWidget(self.turn_timeout_s)
        queue_layout.addLayout(queue_row)
        queue_hint = QLabel(
            "超出排队上限的新消息会被丢弃；超时表示这一次回复放弃等待。"
        )
        queue_hint.setObjectName("HelperText")
        queue_hint.setWordWrap(True)
        queue_layout.addWidget(queue_hint)

        layout.addStretch()

        self._mode_groups = (
            ("forward", self.forward_enabled, forward_frame),
            ("reverse", self.reverse_enabled, reverse_frame),
            ("http_api", self.http_api_enabled, http_api_frame),
            ("http_post", self.http_post_enabled, http_post_frame),
            ("hub", self.hub_enabled, hub_frame),
        )
        self._mode_fields = {
            "forward": (
                self.forward_url,
                self.forward_token,
                self.forward_token_visibility,
                self.forward_token_status,
                self.forward_reconnect,
                self.forward_reconnect_initial_ms,
                self.forward_reconnect_max_ms,
            ),
            "reverse": (
                self.reverse_host,
                self.reverse_port,
                self.reverse_path,
                self.reverse_token,
                self.reverse_token_visibility,
                self.reverse_token_status,
                self.reverse_allow_insecure_remote,
            ),
            "http_api": (
                self.http_api_base,
                self.http_api_token,
                self.http_api_token_visibility,
                self.http_api_token_status,
                self.http_api_timeout_s,
            ),
            "http_post": (
                self.http_post_host,
                self.http_post_port,
                self.http_post_path,
                self.http_post_token,
                self.http_post_token_visibility,
                self.http_post_token_status,
                self.http_post_allow_insecure_remote,
            ),
            "hub": (
                self.hub_host,
                self.hub_port,
                self.hub_ws_path,
                self.hub_http_path,
                self.hub_token,
                self.hub_token_visibility,
                self.hub_token_status,
                self.hub_publish_events,
                self.hub_allow_send,
                self.hub_allow_insecure_remote,
                self.hub_push_urls,
                self.hub_push_token,
                self.hub_push_token_visibility,
                self.hub_push_token_status,
                self.hub_push_timeout,
            ),
        }
        self._allow_queue_fields = (
            self.allow_private,
            self.allow_groups,
            self.allow_require_at,
            self.allow_users_input,
            self.allow_group_ids_input,
            self.max_pending,
            self.turn_timeout_s,
        )

        self.enabled_cb.toggled.connect(self._sync_visibility)
        for _name, checkbox, _frame in self._mode_groups:
            checkbox.toggled.connect(self._sync_visibility)
        self.reply_mode_combo.currentIndexChanged.connect(self._sync_visibility)
        self.reverse_allow_insecure_remote.toggled.connect(self._sync_visibility)
        self.http_post_allow_insecure_remote.toggled.connect(self._sync_visibility)
        self.hub_allow_insecure_remote.toggled.connect(self._sync_visibility)
        self.enabled_cb.setChecked(False)
        self.apply_config({})

    # ── 构建辅助 ──────────────────────────────────────────────

    def _make_section(self, layout, title: str, hint: str):
        """创建一个 SectionCard，返回 (frame, 内部布局)。"""
        frame = QFrame()
        frame.setObjectName("SectionCard")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(16, 14, 16, 16)
        inner.setSpacing(10)
        section_title = QLabel(title)
        section_title.setObjectName("SectionTitle")
        inner.addWidget(section_title)
        description = QLabel(hint)
        description.setObjectName("HelperText")
        description.setWordWrap(True)
        inner.addWidget(description)
        layout.addWidget(frame)
        return frame, inner

    def _make_warning(self, layout, text: str) -> QLabel:
        warning = QLabel(text)
        warning.setProperty("status", "warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)
        return warning

    def _build_token_field(
        self,
        layout,
        label: str,
        attr: str,
        accessible_name: str,
        description: str,
    ) -> None:
        """令牌字段统一为密码回显 + 查看按钮 + 已配置/未配置提示。"""
        line = QLineEdit()
        line.setStyleSheet(STYLE_INPUT)
        line.setEchoMode(QLineEdit.Password)
        line.setPlaceholderText(_TOKEN_PLACEHOLDER)
        line.setAccessibleName(accessible_name)
        line.setAccessibleDescription(description)
        setattr(self, attr, line)
        _field(layout, label, line, accessible_name)

        row = QHBoxLayout()
        button = QPushButton("查看")
        button.setMinimumHeight(MIN_TARGET_SIZE)
        button.setAccessibleName(f"查看{accessible_name}")
        button.setProperty("doesNotModifyConfig", True)
        setattr(self, f"{attr}_visibility", button)
        button.clicked.connect(
            lambda _checked=False, target=line, toggle=button: self._toggle_token(
                target, toggle
            )
        )
        row.addWidget(button)

        status = QLabel("未配置")
        status.setProperty("status", "muted")
        status.setWordWrap(True)
        status.setAccessibleName(f"{accessible_name}状态")
        setattr(self, f"{attr}_status", status)
        row.addWidget(status, 1)
        layout.addLayout(row)

        line.textChanged.connect(
            lambda _text, target=line, hint=status: self._refresh_token_status(
                target, hint
            )
        )
        self._refresh_token_status(line, status)

    @staticmethod
    def _toggle_token(line: QLineEdit, button: QPushButton) -> None:
        visible = line.echoMode() == QLineEdit.Normal
        line.setEchoMode(QLineEdit.Password if visible else QLineEdit.Normal)
        button.setText("查看" if visible else "隐藏")

    @staticmethod
    def _refresh_token_status(line: QLineEdit, hint: QLabel) -> None:
        if line.text().strip():
            set_status(hint, "success", "已配置")
        else:
            set_status(hint, "muted", "未配置")

    def _sync_visibility(self, *_args) -> None:
        """未启用的通信方式**整段折叠**；已启用的置灰/可用照旧，取值逻辑完全不变。

        为什么可以整段藏：每个分区的"启用"勾选框已经移到分区**外面**（见构建处注释），
        所以折叠只会藏掉字段，不会藏掉那个入口——未启用的方式仍以一行勾选框的形式可见，
        勾上即展开。设计依据见 `design-system/pages/wizard.md` 的 Progressive disclosure 第 5 条。
        """
        global_on = self.enabled_cb.isChecked()
        for name, checkbox, frame in self._mode_groups:
            active = global_on and checkbox.isChecked()
            for widget in self._mode_fields[name]:
                widget.setEnabled(active)
            # 折叠的判据是**该方式自己的勾选状态**，而不是总开关：否则用户关掉总开关后
            # 再勾"启用反向 WS"会什么都不出现，看起来像界面坏了。
            # 总开关只决定"可用/置灰"。
            frame.setVisible(checkbox.isChecked())
            frame.setEnabled(active)
        reply_mode = str(self.reply_mode_combo.currentData() or "pet")
        if hasattr(self, "_allow_frame"):
            allow_applies = reply_mode != "hub"
            self._allow_frame.setVisible(allow_applies)
            self.hub_allow_note.setVisible(not allow_applies)
        self.forward_reconnect_initial_ms.setEnabled(
            global_on
            and self.forward_enabled.isChecked()
            and self.forward_reconnect.isChecked()
        )
        self.forward_reconnect_max_ms.setEnabled(
            self.forward_reconnect_initial_ms.isEnabled()
        )
        for widget in self._allow_queue_fields:
            widget.setEnabled(global_on)
        self.reverse_insecure_warning.setVisible(
            global_on
            and self.reverse_enabled.isChecked()
            and self.reverse_allow_insecure_remote.isChecked()
        )
        self.http_post_insecure_warning.setVisible(
            global_on
            and self.http_post_enabled.isChecked()
            and self.http_post_allow_insecure_remote.isChecked()
        )
        self.hub_insecure_warning.setVisible(
            global_on
            and self.hub_enabled.isChecked()
            and self.hub_allow_insecure_remote.isChecked()
        )
        self.http_post_hint.setVisible(
            self.http_post_enabled.isChecked() and not self.http_api_enabled.isChecked()
        )

    # ── 公共 API ──────────────────────────────────────────────

    def apply_config(self, onebot_config: dict) -> None:
        """从规范化或原始字典回填；容忍 None、缺键与错误类型。"""
        raw = _as_dict(onebot_config)

        self.enabled_cb.setChecked(_as_bool(raw.get("enabled"), False))
        self.self_ids_input.setText(format_int_list(raw.get("self_ids")))
        mode = _as_text(raw.get("reply_mode"), "pet").lower()
        index = self.reply_mode_combo.findData(mode)
        self.reply_mode_combo.setCurrentIndex(index if index >= 0 else 0)

        forward_defaults = DEFAULT_ONEBOT["forward_ws"]
        forward = _as_dict(raw.get("forward_ws"))
        self.forward_enabled.setChecked(_as_bool(forward.get("enabled"), False))
        self.forward_url.setText(
            _as_text(forward.get("url"), str(forward_defaults["url"]))
            or str(forward_defaults["url"])
        )
        self.forward_token.setText(_as_text(forward.get("access_token")))
        self.forward_reconnect.setChecked(
            _as_bool(forward.get("reconnect"), bool(forward_defaults["reconnect"]))
        )
        self.forward_reconnect_initial_ms.setValue(
            _as_int(
                forward.get("reconnect_initial_ms"),
                int(forward_defaults["reconnect_initial_ms"]),
                100,
                600000,
            )
        )
        self.forward_reconnect_max_ms.setValue(
            _as_int(
                forward.get("reconnect_max_ms"),
                int(forward_defaults["reconnect_max_ms"]),
                100,
                3600000,
            )
        )

        reverse_defaults = DEFAULT_ONEBOT["reverse_ws"]
        reverse = _as_dict(raw.get("reverse_ws"))
        self.reverse_enabled.setChecked(_as_bool(reverse.get("enabled"), False))
        self.reverse_host.setText(
            _as_text(reverse.get("host"), str(reverse_defaults["host"]))
            or str(reverse_defaults["host"])
        )
        self.reverse_port.setValue(
            _as_int(
                reverse.get("port"), int(reverse_defaults["port"]), 1, 65535
            )
        )
        self.reverse_path.setText(
            _as_text(reverse.get("path"), str(reverse_defaults["path"]))
            or str(reverse_defaults["path"])
        )
        self.reverse_token.setText(_as_text(reverse.get("access_token")))
        self.reverse_allow_insecure_remote.setChecked(
            _as_bool(reverse.get("allow_insecure_remote"), False)
        )

        http_api_defaults = DEFAULT_ONEBOT["http_api"]
        http_api = _as_dict(raw.get("http_api"))
        self.http_api_enabled.setChecked(_as_bool(http_api.get("enabled"), False))
        self.http_api_base.setText(
            _as_text(http_api.get("api_base"), str(http_api_defaults["api_base"]))
            or str(http_api_defaults["api_base"])
        )
        self.http_api_token.setText(_as_text(http_api.get("access_token")))
        self.http_api_timeout_s.setValue(
            _as_float(
                http_api.get("timeout_s"),
                float(http_api_defaults["timeout_s"]),
                0.5,
                120.0,
            )
        )

        http_post_defaults = DEFAULT_ONEBOT["http_post"]
        http_post = _as_dict(raw.get("http_post"))
        self.http_post_enabled.setChecked(_as_bool(http_post.get("enabled"), False))
        self.http_post_host.setText(
            _as_text(http_post.get("host"), str(http_post_defaults["host"]))
            or str(http_post_defaults["host"])
        )
        self.http_post_port.setValue(
            _as_int(
                http_post.get("port"), int(http_post_defaults["port"]), 1, 65535
            )
        )
        self.http_post_path.setText(
            _as_text(http_post.get("path"), str(http_post_defaults["path"]))
            or str(http_post_defaults["path"])
        )
        self.http_post_token.setText(_as_text(http_post.get("access_token")))
        self.http_post_allow_insecure_remote.setChecked(
            _as_bool(http_post.get("allow_insecure_remote"), False)
        )

        hub_defaults = DEFAULT_ONEBOT["hub"]
        hub = _as_dict(raw.get("hub"))
        self.hub_enabled.setChecked(_as_bool(hub.get("enabled"), False))
        self.hub_host.setText(
            _as_text(hub.get("host"), str(hub_defaults["host"]))
            or str(hub_defaults["host"])
        )
        self.hub_port.setValue(
            _as_int(hub.get("port"), int(hub_defaults["port"]), 1, 65535)
        )
        self.hub_ws_path.setText(
            _as_text(hub.get("ws_path"), str(hub_defaults["ws_path"]))
            or str(hub_defaults["ws_path"])
        )
        self.hub_http_path.setText(
            _as_text(hub.get("http_path"), str(hub_defaults["http_path"]))
            or str(hub_defaults["http_path"])
        )
        self.hub_token.setText(_as_text(hub.get("access_token")))
        self.hub_publish_events.setChecked(
            _as_bool(hub.get("publish_events"), bool(hub_defaults["publish_events"]))
        )
        self.hub_allow_send.setChecked(
            _as_bool(hub.get("allow_send"), bool(hub_defaults["allow_send"]))
        )
        self.hub_allow_insecure_remote.setChecked(
            _as_bool(hub.get("allow_insecure_remote"), False)
        )
        self.hub_push_urls.setText(
            format_url_list(hub.get("push_urls")) or ""
        )
        self.hub_push_token.setText(_as_text(hub.get("push_token")))
        self.hub_push_timeout.setText(
            str(
                _as_float(
                    hub.get("push_timeout_s"),
                    float(hub_defaults["push_timeout_s"]),
                    0.5,
                    60.0,
                )
            )
        )

        allow_defaults = DEFAULT_ONEBOT["allow"]
        allow = _as_dict(raw.get("allow"))
        self.allow_private.setChecked(
            _as_bool(allow.get("private"), bool(allow_defaults["private"]))
        )
        self.allow_groups.setChecked(
            _as_bool(allow.get("groups"), bool(allow_defaults["groups"]))
        )
        self.allow_require_at.setChecked(
            _as_bool(allow.get("require_at"), bool(allow_defaults["require_at"]))
        )
        self.allow_users_input.setText(format_int_list(allow.get("users")))
        self.allow_group_ids_input.setText(format_int_list(allow.get("group_ids")))

        queue_defaults = DEFAULT_ONEBOT["queue"]
        queue = _as_dict(raw.get("queue"))
        self.max_pending.setValue(
            _as_int(
                queue.get("max_pending"), int(queue_defaults["max_pending"]), 1, 256
            )
        )
        self.turn_timeout_s.setValue(
            _as_float(
                queue.get("turn_timeout_s"),
                float(queue_defaults["turn_timeout_s"]),
                5.0,
                3600.0,
            )
        )

        self._sync_visibility()

    def collect(self) -> dict:
        """返回与 ``DEFAULT_ONEBOT`` 同形状的普通字典，交给 normalize_config。"""
        return {
            "enabled": bool(self.enabled_cb.isChecked()),
            "self_ids": parse_int_list(self.self_ids_input.text()),
            "reply_mode": str(self.reply_mode_combo.currentData() or "pet"),
            "forward_ws": {
                "enabled": bool(self.forward_enabled.isChecked()),
                "url": self.forward_url.text().strip()
                or str(DEFAULT_ONEBOT["forward_ws"]["url"]),
                "access_token": self.forward_token.text().strip(),
                "reconnect": bool(self.forward_reconnect.isChecked()),
                "reconnect_initial_ms": int(self.forward_reconnect_initial_ms.value()),
                "reconnect_max_ms": int(self.forward_reconnect_max_ms.value()),
            },
            "reverse_ws": {
                "enabled": bool(self.reverse_enabled.isChecked()),
                "host": self.reverse_host.text().strip() or "127.0.0.1",
                "port": int(self.reverse_port.value()),
                "path": self.reverse_path.text().strip()
                or str(DEFAULT_ONEBOT["reverse_ws"]["path"]),
                "access_token": self.reverse_token.text().strip(),
                "allow_insecure_remote": bool(
                    self.reverse_allow_insecure_remote.isChecked()
                ),
            },
            "http_api": {
                "enabled": bool(self.http_api_enabled.isChecked()),
                "api_base": self.http_api_base.text().strip()
                or str(DEFAULT_ONEBOT["http_api"]["api_base"]),
                "access_token": self.http_api_token.text().strip(),
                "timeout_s": float(self.http_api_timeout_s.value()),
            },
            "http_post": {
                "enabled": bool(self.http_post_enabled.isChecked()),
                "host": self.http_post_host.text().strip() or "127.0.0.1",
                "port": int(self.http_post_port.value()),
                "path": self.http_post_path.text().strip()
                or str(DEFAULT_ONEBOT["http_post"]["path"]),
                "access_token": self.http_post_token.text().strip(),
                "allow_insecure_remote": bool(
                    self.http_post_allow_insecure_remote.isChecked()
                ),
            },
            "hub": {
                "enabled": bool(self.hub_enabled.isChecked()),
                "host": self.hub_host.text().strip() or "127.0.0.1",
                "port": int(self.hub_port.value()),
                "ws_path": self.hub_ws_path.text().strip()
                or str(DEFAULT_ONEBOT["hub"]["ws_path"]),
                "http_path": self.hub_http_path.text().strip()
                or str(DEFAULT_ONEBOT["hub"]["http_path"]),
                "access_token": self.hub_token.text().strip(),
                "publish_events": bool(self.hub_publish_events.isChecked()),
                "allow_send": bool(self.hub_allow_send.isChecked()),
                "allow_insecure_remote": bool(
                    self.hub_allow_insecure_remote.isChecked()
                ),
                "push_urls": parse_url_list(self.hub_push_urls.text()),
                "push_token": self.hub_push_token.text().strip(),
                "push_timeout_s": _as_float(
                    self.hub_push_timeout.text().strip(),
                    float(DEFAULT_ONEBOT["hub"]["push_timeout_s"]),
                    0.5,
                    60.0,
                ),
            },
            "allow": {
                "private": bool(self.allow_private.isChecked()),
                "groups": bool(self.allow_groups.isChecked()),
                "require_at": bool(self.allow_require_at.isChecked()),
                "users": parse_int_list(self.allow_users_input.text()),
                "group_ids": parse_int_list(self.allow_group_ids_input.text()),
            },
            "queue": {
                "max_pending": int(self.max_pending.value()),
                "turn_timeout_s": float(self.turn_timeout_s.value()),
            },
        }

    def summary(self) -> str:
        """一行中文摘要，供配置页日志使用（不含任何令牌）。"""
        if not self.enabled_cb.isChecked():
            return "关闭"
        labels = (
            (self.forward_enabled, "正向 WS"),
            (self.reverse_enabled, "反向 WS"),
            (self.http_api_enabled, "HTTP API"),
            (self.http_post_enabled, "事件 POST"),
            (self.hub_enabled, "下游 hub"),
        )
        active = [text for checkbox, text in labels if checkbox.isChecked()]
        mode_text = "、".join(active) if active else "尚未选择通信方式"
        return f"开启（{mode_text}；回复方式 {self.reply_mode_combo.currentData() or 'pet'}）"
