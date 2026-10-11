"""``onebot`` 配置段的默认值与规范化。

放在 OneBot 包内而不是 ``config/store.py``，是为了让协议实现自带 schema、
可独立测试；``store.py`` 里的 ``_normalize_onebot`` 直接委托到这里。
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Mapping, Sequence, Tuple

REPLY_MODES = ("pet", "hub", "both")

DEFAULT_ONEBOT: Dict[str, Any] = {
    "enabled": False,
    "self_ids": [],
    "reply_mode": "pet",
    # 发起端：主动连对端的正向 WebSocket（NapCat 6700 / SnowLuma 3001）
    "forward_ws": {
        "enabled": False,
        "url": "ws://127.0.0.1:3001/",
        "access_token": "",
        "reconnect": True,
        "reconnect_initial_ms": 1000,
        "reconnect_max_ms": 30000,
    },
    # 接收端：自己监听，对端以「反向 WebSocket」连进来
    "reverse_ws": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 14321,
        "path": "/onebot/v11/ws",
        "access_token": "",
        "allow_insecure_remote": False,
    },
    # 发起端：调用对端的 HTTP API 发送消息
    "http_api": {
        "enabled": False,
        "api_base": "http://127.0.0.1:3000",
        "access_token": "",
        "timeout_s": 10.0,
    },
    # 接收端：接收对端的事件 HTTP POST（发送走 http_api）
    "http_post": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 14321,
        "path": "/onebot/v11/http",
        "access_token": "",
        "allow_insecure_remote": False,
    },
    # 下游消费者端点（dsh 插件等以 OneBot 客户端身份连进来）
    "hub": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 14321,
        "ws_path": "/onebot/v11/hub",
        "http_path": "/onebot/v11/hub/http",
        "access_token": "",
        "publish_events": True,
        "allow_send": True,
        "allow_insecure_remote": False,
        # 事件 HTTP POST 上报（规范 §2.2）：把事件主动推给只会 HTTP 的消费者，
        # 相当于 SnowLuma 的 httpClients / NapCat 的 HTTP 上报。
        "push_urls": [],
        "push_token": "",
        "push_timeout_s": 5.0,
    },
    "allow": {
        "private": True,
        "groups": True,
        "require_at": True,
        "users": [],
        "group_ids": [],
    },
    "queue": {
        "max_pending": 16,
        "turn_timeout_s": 180.0,
    },
}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
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
    if number != number:  # NaN
        return default
    if number < lo or number > hi:
        return default
    return number


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _as_int_list(value: Any) -> List[int]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    out: List[int] = []
    for item in value:
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number >= 0 and number not in out:
            out.append(number)
    return out


def _as_url_list(value: Any) -> List[str]:
    """只保留 http(s) URL，去重、保序。"""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    out: List[str] = []
    for item in value:
        text = _as_text(item)
        if not text or not text.startswith(("http://", "https://")):
            continue
        if text not in out:
            out.append(text)
    return out


def _section(value: Any, defaults: Mapping[str, Any]) -> Dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    out: Dict[str, Any] = copy.deepcopy(dict(defaults))
    for key in defaults:
        if key in raw:
            out[key] = raw[key]
    return out


def normalize_onebot_config(value: Any) -> Dict[str, Any]:
    """规范化 ``onebot`` 段；幂等，任何脏输入都退化为安全默认值。"""
    raw = value if isinstance(value, Mapping) else {}

    cfg: Dict[str, Any] = {
        "enabled": _as_bool(raw.get("enabled"), False),
        "self_ids": _as_int_list(raw.get("self_ids")),
        "reply_mode": _as_text(raw.get("reply_mode"), "pet").lower(),
    }
    if cfg["reply_mode"] not in REPLY_MODES:
        cfg["reply_mode"] = "pet"

    forward = _section(raw.get("forward_ws"), DEFAULT_ONEBOT["forward_ws"])
    cfg["forward_ws"] = {
        "enabled": _as_bool(forward.get("enabled"), False),
        "url": _as_text(forward.get("url"), DEFAULT_ONEBOT["forward_ws"]["url"]),
        "access_token": _as_text(forward.get("access_token")),
        "reconnect": _as_bool(forward.get("reconnect"), True),
        "reconnect_initial_ms": _as_int(forward.get("reconnect_initial_ms"), 1000, 100, 600000),
        "reconnect_max_ms": _as_int(forward.get("reconnect_max_ms"), 30000, 100, 3600000),
    }
    if cfg["forward_ws"]["reconnect_max_ms"] < cfg["forward_ws"]["reconnect_initial_ms"]:
        cfg["forward_ws"]["reconnect_max_ms"] = cfg["forward_ws"]["reconnect_initial_ms"]

    reverse = _section(raw.get("reverse_ws"), DEFAULT_ONEBOT["reverse_ws"])
    cfg["reverse_ws"] = {
        "enabled": _as_bool(reverse.get("enabled"), False),
        "host": _as_text(reverse.get("host"), "127.0.0.1") or "127.0.0.1",
        "port": _as_int(reverse.get("port"), 14321, 1, 65535),
        "path": _as_path(reverse.get("path"), DEFAULT_ONEBOT["reverse_ws"]["path"]),
        "access_token": _as_text(reverse.get("access_token")),
        "allow_insecure_remote": _as_bool(reverse.get("allow_insecure_remote"), False),
    }

    http_api = _section(raw.get("http_api"), DEFAULT_ONEBOT["http_api"])
    cfg["http_api"] = {
        "enabled": _as_bool(http_api.get("enabled"), False),
        "api_base": _as_text(http_api.get("api_base"), DEFAULT_ONEBOT["http_api"]["api_base"]),
        "access_token": _as_text(http_api.get("access_token")),
        "timeout_s": _as_float(http_api.get("timeout_s"), 10.0, 0.5, 120.0),
    }

    http_post = _section(raw.get("http_post"), DEFAULT_ONEBOT["http_post"])
    cfg["http_post"] = {
        "enabled": _as_bool(http_post.get("enabled"), False),
        "host": _as_text(http_post.get("host"), "127.0.0.1") or "127.0.0.1",
        "port": _as_int(http_post.get("port"), 14321, 1, 65535),
        "path": _as_path(http_post.get("path"), DEFAULT_ONEBOT["http_post"]["path"]),
        "access_token": _as_text(http_post.get("access_token")),
        "allow_insecure_remote": _as_bool(http_post.get("allow_insecure_remote"), False),
    }

    hub = _section(raw.get("hub"), DEFAULT_ONEBOT["hub"])
    cfg["hub"] = {
        "enabled": _as_bool(hub.get("enabled"), False),
        "host": _as_text(hub.get("host"), "127.0.0.1") or "127.0.0.1",
        "port": _as_int(hub.get("port"), 14321, 1, 65535),
        "ws_path": _as_path(hub.get("ws_path"), DEFAULT_ONEBOT["hub"]["ws_path"]),
        "http_path": _as_path(hub.get("http_path"), DEFAULT_ONEBOT["hub"]["http_path"]),
        "access_token": _as_text(hub.get("access_token")),
        "publish_events": _as_bool(hub.get("publish_events"), True),
        "allow_send": _as_bool(hub.get("allow_send"), True),
        "allow_insecure_remote": _as_bool(hub.get("allow_insecure_remote"), False),
        "push_urls": _as_url_list(hub.get("push_urls")),
        "push_token": _as_text(hub.get("push_token")),
        "push_timeout_s": _as_float(hub.get("push_timeout_s"), 5.0, 0.5, 60.0),
    }

    allow = _section(raw.get("allow"), DEFAULT_ONEBOT["allow"])
    cfg["allow"] = {
        "private": _as_bool(allow.get("private"), True),
        "groups": _as_bool(allow.get("groups"), True),
        "require_at": _as_bool(allow.get("require_at"), True),
        "users": _as_int_list(allow.get("users")),
        "group_ids": _as_int_list(allow.get("group_ids")),
    }

    queue = _section(raw.get("queue"), DEFAULT_ONEBOT["queue"])
    cfg["queue"] = {
        "max_pending": _as_int(queue.get("max_pending"), 16, 1, 256),
        "turn_timeout_s": _as_float(queue.get("turn_timeout_s"), 180.0, 5.0, 3600.0),
    }
    return cfg


def _as_path(value: Any, default: str) -> str:
    text = _as_text(value, default) or default
    if not text.startswith("/"):
        text = "/" + text
    return text


def active_modes(config: Mapping[str, Any]) -> List[str]:
    """返回当前启用的通信方式标识，供界面与日志展示。"""
    modes: List[str] = []
    if config.get("forward_ws", {}).get("enabled"):
        modes.append("forward_ws")
    if config.get("reverse_ws", {}).get("enabled"):
        modes.append("reverse_ws")
    if config.get("http_api", {}).get("enabled"):
        modes.append("http_api")
    if config.get("http_post", {}).get("enabled"):
        modes.append("http_post")
    if config.get("hub", {}).get("enabled"):
        modes.append("hub")
    return modes


def validate_onebot_config(config: Mapping[str, Any]) -> List[str]:
    """返回人类可读的配置问题列表；空列表表示可启动。

    只报**会导致功能不可用**的问题，不做过度校验。
    """
    issues: List[str] = []
    if not config.get("enabled"):
        return issues

    modes = active_modes(config)
    if not modes:
        issues.append("onebot.enabled 为 true，但没有任何启用中的通信方式")

    allow = config.get("allow") or {}
    if not allow.get("private") and not allow.get("groups"):
        issues.append("私聊与群聊都被禁用，机器人不会响应任何消息")

    forward = config.get("forward_ws") or {}
    if forward.get("enabled"):
        url = str(forward.get("url") or "")
        if not url.startswith(("ws://", "wss://")):
            issues.append("forward_ws.url 必须是 ws:// 或 wss:// 地址")

    http_api = config.get("http_api") or {}
    if http_api.get("enabled"):
        base = str(http_api.get("api_base") or "")
        if not base.startswith(("http://", "https://")):
            issues.append("http_api.api_base 必须是 http:// 或 https:// 地址")

    http_post = config.get("http_post") or {}
    if http_post.get("enabled") and not http_api.get("enabled"):
        issues.append("http_post 只能接收事件，需要同时启用 http_api 才能发送消息")

    for name in ("reverse_ws", "http_post", "hub"):
        section = config.get(name) or {}
        if not section.get("enabled"):
            continue
        host = str(section.get("host") or "")
        insecure = bool(section.get("allow_insecure_remote"))
        if host not in ("127.0.0.1", "::1", "localhost") and not insecure:
            issues.append(
                f"{name}.host 为非回环地址 {host}，需要显式打开 allow_insecure_remote"
            )

    hub = config.get("hub") or {}
    push_urls = hub.get("push_urls") or []
    if push_urls:
        if not hub.get("enabled"):
            issues.append("配置了 hub.push_urls，但 hub.enabled 为 false，事件不会被推送")
        if not hub.get("publish_events"):
            issues.append("配置了 hub.push_urls，但 hub.publish_events 为 false，事件不会被推送")
        for url in push_urls:
            host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
            if url.startswith("http://") and host not in ("127.0.0.1", "::1", "localhost"):
                issues.append(
                    f"hub.push_urls 里有明文 http:// 且主机不是回环（{url}）："
                    "事件正文会以明文离开本机，建议改用 https://"
                )

    # 同一个 host:port 上的路径不能撞车：后注册的路由会被前面的遮住，
    # 症状是「某个端点莫名鉴权失败 / 收不到事件」，很难查。
    used: Dict[Tuple[str, Any], Dict[str, str]] = {}
    for section_key, path_key, label in (
        ("reverse_ws", "path", "反向 WS"),
        ("http_post", "path", "事件 POST"),
        ("hub", "ws_path", "hub WS"),
        ("hub", "http_path", "hub HTTP"),
    ):
        section = config.get(section_key) or {}
        if not section.get("enabled"):
            continue
        path = str(section.get(path_key) or "")
        if not path:
            continue
        addr = (str(section.get("host") or "127.0.0.1"), section.get("port"))
        seen = used.setdefault(addr, {})
        if path in seen:
            issues.append(
                f"{seen[path]} 与 {label} 在 {addr[0]}:{addr[1]} 上用了同一个路径 {path}，"
                "其中一个会被路由遮蔽（对端会连到错的处理器）"
            )
        else:
            seen[path] = label
    return issues
