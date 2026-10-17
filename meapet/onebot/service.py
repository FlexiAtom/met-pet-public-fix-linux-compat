"""OneBot 服务：四种通信方式的编排、消息路由与 hub 转发。

线程模型（与项目既有约定一致）：

- 所有网络 I/O 都跑在 :mod:`meapet.async_runtime` 的 asyncio 守护线程上。
- 桌面侧只用**轮询**拿待处理的 QQ 消息（:meth:`take_ready_turn`）、并用
  :meth:`complete_turn` 交回回复；两个方法内部用 ``threading.Lock`` 保护，
  因此可以从 Qt 主线程安全调用，不需要跨线程碰控件。
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any, Deque, Dict, List, Mapping, Optional

from meapet.log import get_color_logger
from meapet.onebot import config as onebot_config
from meapet.onebot import events as onebot_events
from meapet.onebot.api import HttpApiSender, SenderRegistry, send_target_params
from meapet.onebot.transport.forward_ws import ForwardWsClient
from meapet.onebot.transport.server import OneBotServer, group_listeners
from meapet.onebot.transport.ws_session import WsSession
from meapet.onebot.types import (
    GROUP,
    ApiResponse,
    ConnectionStatus,
    IncomingMessage,
)

# 用项目的 logger 工厂（而不是裸 `logging.getLogger`）：只有它会挂上文件 Handler，
# 于是本通道的日志会落到 `logs/onebot.log`。裸 logger 的记录既没有 Handler、
# 又在 root 的 WARNING 之下，**连 INFO 都到不了任何地方** ——
# 排查"消息到底有没有到 MeaPet"时，日志里会一片空白（实测踩过）。
_log = get_color_logger("onebot")

#: hub 消费者允许调用的动作（只读 + 发消息），其余一律拒绝
HUB_ALLOWED_ACTIONS = frozenset(
    {
        "send_private_msg",
        "send_group_msg",
        "send_msg",
        "delete_msg",
        "get_msg",
        "get_forward_msg",
        "get_login_info",
        "get_status",
        "get_version_info",
        "can_send_image",
        "can_send_record",
        "get_friend_list",
        "get_group_list",
        "get_group_info",
        "get_group_member_info",
        "get_group_member_list",
        "get_stranger_info",
    }
)


@dataclass
class PendingTurn:
    """一条等待桌宠回复的 QQ 消息。"""

    turn_id: str
    message: IncomingMessage
    created_at: float
    deadline: float

    @property
    def prompt(self) -> str:
        """交给模型的提示词：群聊带上说话人，便于多人场景区分。"""
        if self.message.is_group:
            return f"[群 {self.message.group_id}] {self.message.display_name}：{self.message.text}"
        return self.message.text


class OneBotService:
    """OneBot 适配器的总入口。"""

    def __init__(self, config: Optional[Mapping[str, Any]] = None) -> None:
        self.config = onebot_config.normalize_onebot_config(config or {})
        self.senders = SenderRegistry()
        self.http_sender: Optional[HttpApiSender] = None
        self.forward_clients: List[ForwardWsClient] = []
        self.servers: List[OneBotServer] = []
        self.upstream_sessions: List[WsSession] = []
        self.hub_sessions: List[WsSession] = []

        self._lock = threading.RLock()
        self._pending: Deque[PendingTurn] = deque()
        self._active: Optional[PendingTurn] = None
        self._turn_seq = 0
        self._future = None
        self._started = False
        self._learned_self_ids: List[int] = []
        self.http_post_source = "http_post"

        self.stats: Dict[str, int] = {
            "events_in": 0,
            "messages_in": 0,
            "messages_denied": 0,
            "messages_queued": 0,
            "messages_dropped": 0,
            "replies_sent": 0,
            "send_failures": 0,
            "hub_calls": 0,
            "hub_calls_denied": 0,
            "events_published": 0,
            "events_pushed": 0,
            "push_failures": 0,
            "quick_operations": 0,
            "turns_timed_out": 0,
        }

    # ------------------------------------------------------------------ #
    # 配置与状态
    # ------------------------------------------------------------------ #

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled"))

    @property
    def running(self) -> bool:
        return self._started

    @property
    def reply_mode(self) -> str:
        return str(self.config.get("reply_mode") or "pet")

    @property
    def self_ids(self) -> List[int]:
        configured = list(self.config.get("self_ids") or [])
        with self._lock:
            learned = list(self._learned_self_ids)
        for value in learned:
            if value not in configured:
                configured.append(value)
        return configured

    def validate(self) -> List[str]:
        return onebot_config.validate_onebot_config(self.config)

    def status_snapshot(self) -> List[ConnectionStatus]:
        """给界面/日志用的连接状态快照。"""
        out: List[ConnectionStatus] = []
        if self.config["forward_ws"].get("enabled"):
            client = self.forward_clients[0] if self.forward_clients else None
            out.append(
                ConnectionStatus(
                    name="forward_ws",
                    mode="发起端 · 正向 WebSocket",
                    connected=bool(client and client.connected),
                    endpoint=str(self.config["forward_ws"].get("url") or ""),
                    last_error=(client.last_error if client else ""),
                )
            )
        if self.config["http_api"].get("enabled"):
            out.append(
                ConnectionStatus(
                    name="http_api",
                    mode="发起端 · HTTP API",
                    connected=bool(self.http_sender and self.http_sender.available),
                    endpoint=str(self.config["http_api"].get("api_base") or ""),
                    last_error=(self.http_sender.last_error if self.http_sender else ""),
                )
            )
        for server in self.servers:
            out.append(
                ConnectionStatus(
                    name=server.name,
                    mode="接收端 · 监听中" if server.running else "接收端 · 未运行",
                    listening=server.running,
                    endpoint=server.endpoint,
                    last_error=server.last_error,
                )
            )
        with self._lock:
            hub_count = len(self.hub_sessions)
        if self.config["hub"].get("enabled"):
            out.append(
                ConnectionStatus(
                    name="hub",
                    mode=f"下游消费者 · 已连接 {hub_count}",
                    listening=any(s.name.startswith("hub") for s in self.servers),
                    endpoint=str(self.config["hub"].get("ws_path") or ""),
                )
            )
        return out

    def summary(self) -> str:
        modes = onebot_config.active_modes(self.config)
        return f"reply_mode={self.reply_mode} modes={','.join(modes) or 'none'}"

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        if not self.enabled or self._future is not None:
            return
        for issue in self.validate():
            _log.warning("[onebot] 配置问题：%s", issue)
        from meapet.async_runtime import submit

        # 顺序很重要：_start_all() 一进协程就会读 _started。如果在 submit 之后再置位，
        # 事件循环可能已经跑过那句判断，把一次正常启动误判成"已被 stop 抢先"。
        self._started = True
        self._future = submit(self._start_all())
        _log.info("[onebot] 已启动：%s", self.summary())

    async def _start_all(self) -> None:
        cfg = self.config

        # 先把要建的东西都建好（不注册、不监听），最后统一在"还该启动"时启动。
        # 原因：stop() 可能在协程跑到一半时并发清空 self.servers / self.forward_clients，
        # 于是「已 append、还没 start」或「已 start、但列表已被清掉」都会留下
        # 没人负责关闭的监听端口。这里把决定点收敛到一次检查上。
        http_sender: Optional[HttpApiSender] = None
        if cfg["http_api"].get("enabled"):
            http_sender = HttpApiSender(
                api_base=str(cfg["http_api"].get("api_base") or ""),
                access_token=str(cfg["http_api"].get("access_token") or ""),
                timeout_s=float(cfg["http_api"].get("timeout_s") or 10.0),
            )

        forward_client: Optional[ForwardWsClient] = None
        if cfg["forward_ws"].get("enabled"):
            forward_client = ForwardWsClient(
                cfg["forward_ws"],
                on_event=self._on_upstream_event,
                on_status=self._on_forward_status,
            )

        servers = [self._build_server(group) for group in group_listeners(cfg)]

        if not self._started:
            # stop() 已经跑过：什么都不启动，直接退出
            _log.info("[onebot] 启动过程被 stop() 抢先，已放弃启动")
            return

        if http_sender is not None:
            self.http_sender = http_sender
            self.senders.register(http_sender, fallback=True)

        if forward_client is not None:
            self.forward_clients.append(forward_client)
            # 正向 WS 是发起端的主发送通道：不注册的话回复无处可发
            self.senders.register(forward_client, fallback=True)
            forward_client.start()

        for server in servers:
            self.servers.append(server)
            server.start()

    def _build_server(self, group: Mapping[str, Any]) -> OneBotServer:
        cfg = self.config
        reverse = group.get("reverse_ws")
        http_post = group.get("http_post")
        hub = group.get("hub")
        name = "onebot"
        if reverse:
            name = "reverse_ws"
        elif http_post:
            name = "http_post"
        elif hub:
            name = "hub"
        return OneBotServer(
            host=str(group["host"]),
            port=int(group["port"]),
            name=name,
            on_upstream_event=self._on_upstream_event,
            on_hub_call=self.handle_hub_call,
            reverse_ws_path=(reverse or {}).get("path"),
            reverse_ws_token=str((reverse or {}).get("access_token") or ""),
            http_post_path=(http_post or {}).get("path"),
            http_post_token=str((http_post or {}).get("access_token") or ""),
            hub_ws_path=(hub or {}).get("ws_path"),
            hub_http_path=(hub or {}).get("http_path"),
            hub_token=str((hub or {}).get("access_token") or ""),
            on_upstream_open=self._on_upstream_open,
            on_upstream_closed=self._on_upstream_closed,
            on_hub_open=self._on_hub_open,
            on_hub_closed=self._on_hub_closed,
        )

    def stop(self, timeout_seconds: float = 0.0) -> None:
        """非阻塞停止所有传输；语义与 Companion MCP 运行时一致。"""
        self._started = False
        for client in self.forward_clients:
            self.senders.unregister(client.name)
            client.stop()
        self.forward_clients.clear()

        # 先断开所有会话再停监听：否则 uvicorn 的优雅收尾会等这些连接，
        # 拖到宽限窗口用完才被 cancel，留下悬挂任务。
        for session in list(self.upstream_sessions) + list(self.hub_sessions):
            session.close("服务已停止")
        self.upstream_sessions.clear()
        self.hub_sessions.clear()

        for server in self.servers:
            server.stop(timeout_seconds)
        self.servers.clear()

        future, self._future = self._future, None
        if future is not None and timeout_seconds > 0:
            try:
                future.result(timeout=max(0.0, float(timeout_seconds)))
            except Exception:
                pass

        self.senders.clear()
        self.http_sender = None
        with self._lock:
            self._pending.clear()
            self._active = None
        _log.info("[onebot] 已停止")

    # ------------------------------------------------------------------ #
    # 上游事件
    # ------------------------------------------------------------------ #

    def _on_forward_status(self, connected: bool, detail: str) -> None:
        if connected:
            _log.info("[onebot] 正向 WS 已就绪")
        elif detail:
            _log.warning("[onebot] 正向 WS 断开：%s", detail)

    def _on_upstream_open(self, session: WsSession) -> None:
        self.upstream_sessions.append(session)
        self.senders.register(session)

    def _on_upstream_closed(self, session: WsSession) -> None:
        try:
            self.upstream_sessions.remove(session)
        except ValueError:
            pass
        self.senders.unregister(session.name)

    @staticmethod
    def _raw_brief(payload: Mapping[str, Any]) -> str:
        """**解析前**能拿到的元数据（刻意不含正文，只报消息的"结构"）。

        实测动机：用户发消息没反应时，日志里只有一排心跳——因为转发日志发生在
        **解析成功之后**，于是"实现端没上报"和"上报了但没解析出来"看起来一模一样。
        """
        parts = [f"post_type={payload.get('post_type')}"]
        for field in ("message_type", "sub_type", "notice_type", "request_type", "meta_event_type"):
            value = payload.get(field)
            if isinstance(value, str) and value.strip():
                parts.append(f"{field}={value}")
        for field in ("user_id", "group_id", "message_id", "self_id"):
            if payload.get(field) is not None:
                parts.append(f"{field}={payload.get(field)}")
        message = payload.get("message")
        if isinstance(message, list):
            kinds = [
                str(seg.get("type") or "?")
                for seg in message[:6]
                if isinstance(seg, Mapping)
            ]
            parts.append(f"message=[{'|'.join(kinds)}]（{len(message)} 段）")
        elif isinstance(message, str):
            parts.append(f"message=str({len(message)} 字符)")
        return " ".join(parts)

    def _on_upstream_event(self, payload: Mapping[str, Any], source: str) -> None:
        """所有上游事件（四种方式）最终都汇到这里。"""
        self.stats["events_in"] += 1
        post_type = ""
        if isinstance(payload, Mapping):
            post_type = str(payload.get("post_type") or "")
        # message 事件是用户唯一在等的东西，且很罕见 ⇒ 解析**之前**先留痕，
        # 这样"收到了但解析不出来"不会再是静默的。
        if post_type == "message":
            _log.info("[onebot] 收到 message 事件（解析前）：%s", self._raw_brief(payload))
        event = onebot_events.parse_event(payload)
        if event is None:
            if post_type:
                _log.warning(
                    "[onebot] 事件解析失败（实现端字段差异？）：%s",
                    self._raw_brief(payload),
                )
            return

        if event.self_id and event.self_id not in self._learned_self_ids:
            with self._lock:
                if event.self_id not in self._learned_self_ids:
                    self._learned_self_ids.append(event.self_id)

        if event.message is None:
            # 通知/请求/元事件：只转发给 hub 消费者
            if self._should_publish():
                self._publish(payload)
            return

        self.stats["messages_in"] += 1
        message = replace(event.message, source=source)

        if self._should_publish():
            self._publish(payload, message)

        if self.reply_mode in ("pet", "both"):
            if not self._allowed(message):
                self.stats["messages_denied"] += 1
                return
            self._enqueue(message)

    def _should_publish(self) -> bool:
        if self.reply_mode not in ("hub", "both"):
            return False
        hub = self.config["hub"]
        return bool(hub.get("enabled") and hub.get("publish_events"))

    def _allowed(self, message: IncomingMessage) -> bool:
        allow = self.config["allow"]
        if message.is_private and not allow.get("private"):
            return False
        if message.is_group and not allow.get("groups"):
            return False
        users = allow.get("users") or []
        if users and message.user_id not in users:
            return False
        group_ids = allow.get("group_ids") or []
        if group_ids and (not message.is_group or message.group_id not in group_ids):
            return False
        if message.is_group and allow.get("require_at") and not message.is_at_self:
            return False
        return bool(message.text.strip())

    # ------------------------------------------------------------------ #
    # hub：事件广播与 API 调用
    # ------------------------------------------------------------------ #

    def _on_hub_open(self, session: WsSession) -> None:
        self.hub_sessions.append(session)

    def _on_hub_closed(self, session: WsSession) -> None:
        try:
            self.hub_sessions.remove(session)
        except ValueError:
            pass

    def _publish(
        self,
        payload: Mapping[str, Any],
        message: Optional[IncomingMessage] = None,
    ) -> None:
        urls = list((self.config["hub"] or {}).get("push_urls") or [])
        if not self.hub_sessions and not urls:
            # 收到事件却**没有任何 hub 消费者**：消息就此消失，而且两边都不会报错。
            # `reply_mode=hub` 时这是最容易踩的静默失败（插件没连上 / 没重启过 harness），
            # 所以留一条警告。只记元数据（会话键 + 消息 id），**不记正文**——
            # 与探针 `--show-text` 同一口径。
            _log.warning(
                "[onebot] 收到事件但没有任何 hub 消费者，已丢弃：%s",
                self._event_brief(message, payload),
            )
            return
        self.stats["events_published"] += 1
        _log.info(
            "[onebot] 已转发事件：hub 消费者 %d 个%s，%s",
            len(self.hub_sessions),
            f"、HTTP 上报地址 {len(urls)} 个" if urls else "",
            self._event_brief(message, payload),
        )
        for session in list(self.hub_sessions):
            task = _spawn(session.push(payload))
            if task is None:
                continue
            task.add_done_callback(
                lambda fut, sess=session: self._after_publish(fut, sess)
            )
        # 规范 §2.2「HTTP POST」：把事件主动推给只会 HTTP 的消费者
        for url in urls:
            _spawn(self._post_event(str(url), payload, message))

    @staticmethod
    def _event_brief(
        message: Optional[IncomingMessage],
        payload: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """日志用的**元数据**摘要：会话键 + 消息 id，刻意不含正文。

        非消息事件要**写清是什么事件**（`meta_event/heartbeat`、`request/friend`…）：
        实测踩过一次——用户私聊没反应，日志里只有一排 `（非消息事件）`，分不出
        "心跳" 与 "好友请求"，查了半天才发现那条消息压根没被实现端上报
        （非好友私聊落在 QQ 的陌生人消息里，NapCat 默认不当事件上报）。
        """
        if message is None:
            if not isinstance(payload, Mapping):
                return "（非消息事件）"
            post_type = str(payload.get("post_type") or "?").strip()
            sub = ""
            for field in ("meta_event_type", "notice_type", "request_type", "sub_type"):
                value = payload.get(field)
                if isinstance(value, str) and value.strip():
                    sub = value.strip()
                    break
            return f"（非消息事件：{post_type}/{sub}）" if sub else f"（非消息事件：{post_type}）"
        key = getattr(message, "conversation_key", "") or "未知会话"
        message_id = getattr(message, "message_id", "") or "?"
        return f"{key} msg={message_id}"

    async def _post_event(
        self,
        url: str,
        payload: Mapping[str, Any],
        message: Optional[IncomingMessage],
    ) -> None:
        """POST 一条事件，并执行响应体里的**快速操作**（规范 §7）。

        快速操作的字段与对应 API 的参数同形；这里支持最常见的 ``reply``
        （+ 可选的 ``auto_escape``）——即让消费者在响应里直接把回复交回来。
        """
        hub = self.config["hub"] or {}
        token = str(hub.get("push_token") or hub.get("access_token") or "")
        timeout = float(hub.get("push_timeout_s") or 5.0)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            from meapet.http_async import get_client

            client = await get_client()
            response = await client.post(
                url,
                content=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
                headers=headers,
                timeout=timeout,
            )
        except Exception as exc:
            self.stats["push_failures"] += 1
            _log.warning("[onebot] 事件上报失败：%s", type(exc).__name__)
            return

        if response.status_code >= 400:
            self.stats["push_failures"] += 1
            _log.warning("[onebot] 事件上报被拒：HTTP %s", response.status_code)
            return
        self.stats["events_pushed"] += 1

        try:
            body = response.json()
        except Exception:
            return
        if not isinstance(body, Mapping):
            return
        reply = body.get("reply")
        if not reply or message is None:
            return
        if not hub.get("allow_send", True):
            _log.warning("[onebot] 快速操作被忽略：hub.allow_send=false")
            return
        self.stats["quick_operations"] += 1
        await self.send_message(message, str(reply))

    def _after_publish(self, future, session: WsSession) -> None:
        if future.cancelled() or future.exception() is not None:
            self._on_hub_closed(session)
            return
        if future.result() is False:
            self._on_hub_closed(session)

    async def handle_hub_call(
        self, action: str, params: Mapping[str, Any]
    ) -> ApiResponse:
        """执行 hub 消费者发来的 API 调用。"""
        self.stats["hub_calls"] += 1
        if not isinstance(params, Mapping):
            params = {}
        hub = self.config["hub"]
        if not hub.get("allow_send"):
            self.stats["hub_calls_denied"] += 1
            return ApiResponse(
                ok=False,
                status="failed",
                retcode=103,
                data=None,
                error="hub 未允许发送",
            )

        action = str(action or "").strip()
        if action not in HUB_ALLOWED_ACTIONS:
            self.stats["hub_calls_denied"] += 1
            return ApiResponse(
                ok=False, status="failed", retcode=103, data=None, error="动作不在白名单内"
            )

        if action == "get_version_info" and not self.senders.pick():
            return ApiResponse(
                ok=True,
                status="ok",
                retcode=0,
                data={
                    "app_name": "meapet",
                    "app_version": _meapet_version(),
                    "protocol_version": "v11",
                },
            )
        if action == "get_status" and not self.senders.pick():
            return ApiResponse(ok=True, status="ok", retcode=0, data=self._status_payload())

        if action == "send_msg":
            resolved = self._resolve_send_msg(params)
            if resolved is None:
                return ApiResponse(ok=False, status="failed", retcode=100, data=None)
            action, params = resolved

        return await self.senders.send(action, params, source=self._pick_source())

    def _resolve_send_msg(self, params: Mapping[str, Any]) -> Optional[tuple]:
        kind = str(params.get("message_type") or "").strip().lower()
        if kind not in ("private", "group"):
            kind = GROUP if params.get("group_id") else "private"
        target_id = params.get("group_id") if kind == GROUP else params.get("user_id")
        if target_id in (None, ""):
            return None
        action, built = send_target_params(kind, int(target_id), params.get("message"))
        for key in ("auto_escape",):
            if key in params:
                built[key] = params[key]
        return action, built

    def _pick_source(self) -> str:
        """hub 调用没有来源会话，优先用正向 WS / 任意可用通道。"""
        if self.forward_clients and self.forward_clients[0].available:
            return self.forward_clients[0].name
        for session in self.upstream_sessions:
            if session.available:
                return session.name
        return ""

    def _status_payload(self) -> Dict[str, Any]:
        online = bool(self._pick_source())
        return {
            "online": online,
            "good": online,
            "stat": {
                "events_in": self.stats["events_in"],
                "messages_in": self.stats["messages_in"],
                "replies_sent": self.stats["replies_sent"],
                "pending": self.pending_count,
                "hub_sessions": len(self.hub_sessions),
                "events_pushed": self.stats["events_pushed"],
                "push_failures": self.stats["push_failures"],
                "quick_operations": self.stats["quick_operations"],
            },
        }

    # ------------------------------------------------------------------ #
    # 桌宠回复路径（Qt 线程安全）
    # ------------------------------------------------------------------ #

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def has_active_turn(self) -> bool:
        with self._lock:
            return self._active is not None

    def _enqueue(self, message: IncomingMessage) -> None:
        with self._lock:
            self._turn_seq += 1
            timeout = float(self.config["queue"].get("turn_timeout_s") or 180.0)
            now = time.time()
            turn = PendingTurn(
                turn_id=f"{message.conversation_key}#{self._turn_seq}",
                message=message,
                created_at=now,
                deadline=now + timeout,
            )
            limit = int(self.config["queue"].get("max_pending") or 16)
            if len(self._pending) >= limit:
                self.stats["messages_dropped"] += 1
                _log.warning("[onebot] 待处理队列已满（%d），丢弃最新消息", limit)
                return
            self._pending.append(turn)
            self.stats["messages_queued"] += 1

    def take_ready_turn(self) -> Optional[PendingTurn]:
        """Qt 轮询用：取出应当处理的下一轮；没有可处理项返回 ``None``。

        顺带回收超时未回复的活动轮次，避免单轮卡死堵住整个队列。
        """
        with self._lock:
            self._reap_expired_locked()
            if self._active is not None:
                return None
            while self._pending:
                turn = self._pending.popleft()
                if time.time() > turn.deadline:
                    self.stats["turns_timed_out"] += 1
                    continue
                self._active = turn
                return turn
            return None

    def _reap_expired_locked(self) -> None:
        active = self._active
        if active is not None and time.time() > active.deadline:
            self._active = None
            self.stats["turns_timed_out"] += 1
            _log.warning("[onebot] 本轮回复超时，已释放队列")

    def complete_turn(self, turn_id: str, reply_text: str) -> None:
        """Qt 线程：本轮回复已生成，回发 QQ 并释放队列。"""
        with self._lock:
            active = self._active
            if active is None or active.turn_id != turn_id:
                return
            self._active = None
        if reply_text and reply_text.strip():
            _spawn(self.send_message(active.message, reply_text))

    def fail_turn(self, turn_id: str, reason: str = "") -> None:
        """Qt 线程：本轮失败/取消，直接释放队列。"""
        with self._lock:
            active = self._active
            if active is not None and active.turn_id == turn_id:
                self._active = None
        if reason:
            _log.warning("[onebot] 本轮未产生回复：%s", reason)

    async def send_message(self, message: IncomingMessage, text: str) -> bool:
        """把文本作为消息回发到该会话。"""
        action, params = send_target_params(
            message.target.kind, message.target.target_id, text
        )
        result = await self.senders.send(action, params, source=message.source)
        if result.ok:
            self.stats["replies_sent"] += 1
            return True
        self.stats["send_failures"] += 1
        _log.warning("[onebot] 回发失败：%s", result.error or f"retcode={result.retcode}")
        return False

    async def send_text_to(
        self, kind: str, target_id: int, text: str, source: str = ""
    ) -> bool:
        """不依赖入站事件的直接发送（CLI/测试/主动发言用）。"""
        action, params = send_target_params(kind, target_id, text)
        result = await self.senders.send(action, params, source=source)
        if result.ok:
            self.stats["replies_sent"] += 1
        else:
            self.stats["send_failures"] += 1
        return result.ok


def _spawn(coro):
    """在当前 asyncio 循环上派发协程；没有循环时返回 ``None``。"""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            from meapet.async_runtime import submit

            return submit(coro)
        except Exception:
            coro.close()
            return None
    return loop.create_task(coro)


def _meapet_version() -> str:
    try:
        from meapet import __version__ as version

        return str(version)
    except Exception:
        return "1.6"
