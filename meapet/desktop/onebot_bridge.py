"""OneBot 通道与桌宠会话之间的桥接。

职责边界（与项目线程约定一致）：

- 网络侧全部由 :class:`meapet.onebot.service.OneBotService` 跑在 asyncio 守护线程上，
  本 mixin 不在 Qt 主线程碰任何 socket。
- 本 mixin 只做两件事：用 ``QTimer`` 轮询待处理的 QQ 消息并交给既有对话流程；
  在回复完成后把文本交回服务回发 QQ。

入站消息**不进气泡**：气泡是角色说话，QQ 原文属于运营面；只有桌宠自己的回复会出现在
气泡里（并同时发往 QQ）。
"""

from __future__ import annotations

import time
from typing import Optional

from PyQt5.QtCore import QTimer

from meapet.desktop import status_language
from meapet.log import get_color_logger

# 与 OneBot 服务端共用同一个 logger，整个通道的日志都进 logs/onebot.log
log = get_color_logger("onebot")

#: 轮询间隔；与 Companion MCP 的 100ms 相比放宽，QQ 场景对延迟不敏感
POLL_INTERVAL_MS = 500


class PetOneBotMixin:
    """OneBot 桌面侧接线（MRO 中位于 ``PetChatFlowMixin`` 之前）。"""

    # -- 生命周期 --------------------------------------------------------- #

    def _init_onebot(self) -> None:
        self._onebot_service = None
        self._onebot_turn_id = ""
        self._onebot_turn_deadline = 0.0
        self._onebot_poll_timer = None
        self._onebot_last_summary = ""

        config = (getattr(self, "config", {}) or {}).get("onebot") or {}
        if not config.get("enabled"):
            return
        try:
            from meapet.onebot.service import OneBotService
        except Exception as exc:  # pragma: no cover - 依赖缺失环境
            log.error(f"[onebot] 无法加载适配器: {type(exc).__name__}: {exc}")
            return

        service = OneBotService(config)
        self._onebot_service = service
        for issue in service.validate():
            log.warning(f"[onebot] 配置问题: {issue}")
        service.start()

        timer = QTimer(self)
        timer.setInterval(POLL_INTERVAL_MS)
        timer.timeout.connect(self._poll_onebot)
        timer.start()
        self._onebot_poll_timer = timer
        log.info(f"[onebot] 桌面侧已接线: {service.summary()}")

    def _stop_onebot(self) -> None:
        """非阻塞停止；退出与配置重载路径都会调用。"""
        timer = getattr(self, "_onebot_poll_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
            try:
                timer.deleteLater()
            except Exception:
                pass
        self._onebot_poll_timer = None

        service = getattr(self, "_onebot_service", None)
        if service is not None:
            try:
                service.stop()
            except Exception as exc:
                log.error(f"[onebot] 停止失败: {type(exc).__name__}: {exc}")
        self._onebot_service = None
        self._onebot_turn_id = ""
        self._onebot_turn_deadline = 0.0

    # -- 轮询 ------------------------------------------------------------- #

    def _poll_onebot(self) -> None:
        service = getattr(self, "_onebot_service", None)
        if service is None:
            return

        summary = service.summary()
        if summary != getattr(self, "_onebot_last_summary", ""):
            self._onebot_last_summary = summary
            log.info(f"[onebot] 状态变化: {summary}")

        if self._onebot_turn_in_progress(service):
            return
        if getattr(self, "_awaiting_reply", False):
            return

        turn = service.take_ready_turn()
        if turn is None:
            return
        self._onebot_turn_id = turn.turn_id
        self._onebot_turn_deadline = turn.deadline
        log.info(f"[onebot] 处理入站消息 turn={turn.turn_id}")
        try:
            self._do_chat(turn.prompt)
        except Exception as exc:
            log.error(f"[onebot] 入站消息处理失败: {type(exc).__name__}: {exc}")
            self._onebot_turn_id = ""
            service.fail_turn(turn.turn_id, type(exc).__name__)

    def _onebot_turn_in_progress(self, service) -> bool:
        """当前是否已有 OneBot 轮次在跑。

        超过截止时间就主动释放（并返回 ``False``），避免上游回复丢失后单轮卡死
        把整个队列堵住。
        """
        turn_id = getattr(self, "_onebot_turn_id", "")
        if not turn_id:
            return False
        deadline = float(getattr(self, "_onebot_turn_deadline", 0.0) or 0.0)
        if time.time() <= deadline:
            return True
        log.warning(f"[onebot] 轮次超时，释放: turn={turn_id}")
        self._onebot_turn_id = ""
        self._onebot_turn_deadline = 0.0
        service.fail_turn(turn_id, "timeout")
        return False

    def _onebot_status_text(self) -> str:
        """给菜单/状态面板用的一行摘要。"""
        service = getattr(self, "_onebot_service", None)
        if service is None:
            return "未启用"
        if not service.running:
            return "未运行"
        snapshot = service.status_snapshot()
        if not snapshot:
            return "已启用（无通信方式）"
        parts = []
        for item in snapshot:
            if item.listening:
                mark = "监听"
            elif item.connected:
                mark = "已连接"
            else:
                mark = "未连接"
            parts.append(f"{item.name}:{mark}")
        return " ".join(parts)

    # -- 回复回收 --------------------------------------------------------- #

    def _notify_channel_turn_finished(self, reply: str = "", reason: str = "") -> None:
        """覆盖 ``PetChatFlowMixin`` 的同名空实现：把回复交回 OneBot 通道。"""
        turn_id = getattr(self, "_onebot_turn_id", "")
        if not turn_id:
            return
        self._onebot_turn_id = ""
        self._onebot_turn_deadline = 0.0
        service = getattr(self, "_onebot_service", None)
        if service is None:
            return
        text = (reply or "").strip()
        if text:
            service.complete_turn(turn_id, text)
        else:
            service.fail_turn(turn_id, reason or "no_reply")

    def _onebot_probe(self) -> Optional[str]:
        """配置向导的连接自检文案；service 不可用时返回 ``None``。"""
        service = getattr(self, "_onebot_service", None)
        if service is None:
            return None
        issues = service.validate()
        if issues:
            return "；".join(issues)
        return self._onebot_status_text()

    def _reload_onebot(self) -> None:
        """菜单项：只重建 OneBot 通道，不重启整个桌宠。"""
        self._stop_onebot()
        self._init_onebot()
        text = self._onebot_status_text()
        show = getattr(self, "_show_bubble", None)
        if callable(show):
            show(
                f"{status_language.onebot_connected()} · {text}"
                if getattr(self, "_onebot_service", None) is not None
                else status_language.menu_onebot_disabled(),
                4000,
                mood=None,
            )
