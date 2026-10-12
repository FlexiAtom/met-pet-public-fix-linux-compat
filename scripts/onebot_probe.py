"""OneBot 无界面探针：不启动桌宠，只把 OneBot 通道跑起来做连通性验证。

用途（真机排障，按链路分段定位）：

1. 只验证「NapCat → MeaPet」这一段：用 ``--upstream reverse_ws`` 在指定端口监听，
   让 NapCat 的反向 WS 连进来，看到事件与心跳就说明协议对接成功。
2. 只验证「dsh 插件 → MeaPet 的 hub」这一段：``--upstream none --hub`` 起 hub，
   插件的 mode A 连上来后 ``--poke`` 会往 hub 推一条合成消息，插件应当回一条
   API 调用（探针会打印出来）。

默认**不打印消息正文**，只打印元数据（与项目「日志不落隐私」的约定一致）；
需要看正文时显式加 ``--show-text``。

示例：

    python scripts/onebot_probe.py --upstream reverse_ws \
        --port 10234 --path /ws --token <你的 NapCat access_token> \
        --self-id 123456789

    python scripts/onebot_probe.py --upstream none --hub \
        --hub-port 14321 --hub-token <token> --poke
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build_config(args: argparse.Namespace) -> dict:
    onebot: dict = {
        "enabled": True,
        "self_ids": [args.self_id] if args.self_id else [],
        "reply_mode": "pet" if args.echo_reply else "hub",
        "allow": {
            "private": True,
            "groups": True,
            "require_at": args.require_at,
            "users": [],
            "group_ids": [],
        },
        "queue": {"max_pending": 64, "turn_timeout_s": 120.0},
    }
    if args.upstream == "reverse_ws":
        onebot["reverse_ws"] = {
            "enabled": True,
            "host": args.host,
            "port": args.port,
            "path": args.path,
            "access_token": args.token,
        }
    elif args.upstream == "forward_ws":
        onebot["forward_ws"] = {
            "enabled": True,
            "url": args.url,
            "access_token": args.token,
            "reconnect": True,
            "reconnect_initial_ms": 1000,
        }
    if args.hub:
        onebot["hub"] = {
            "enabled": True,
            "host": args.host,
            "port": args.hub_port,
            "ws_path": args.hub_path,
            "http_path": args.hub_http_path,
            "access_token": args.hub_token,
            "publish_events": True,
            "allow_send": True,
        }
    return onebot


def main() -> int:
    parser = argparse.ArgumentParser(description="OneBot 通道连通性探针")
    parser.add_argument("--upstream", choices=["reverse_ws", "forward_ws", "none"],
                        default="reverse_ws", help="上游方式（none = 只起 hub）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10234, help="reverse_ws 监听端口")
    parser.add_argument("--path", default="/ws", help="reverse_ws 路径（NapCat 客户端 URL 里的路径）")
    parser.add_argument("--token", default="", help="上游 access_token")
    parser.add_argument("--url", default="", help="forward_ws 目标地址")
    parser.add_argument("--self-id", default="", help="本机机器人 QQ 号")
    parser.add_argument("--require-at", action="store_true", default=True)
    parser.add_argument("--no-require-at", dest="require_at", action="store_false")
    parser.add_argument("--hub", action="store_true", help="同时起 hub 端点（给 dsh 插件连）")
    parser.add_argument("--hub-port", type=int, default=14321)
    parser.add_argument("--hub-path", default="/onebot/v11/hub")
    parser.add_argument("--hub-http-path", default="/onebot/v11/hub/http")
    parser.add_argument("--hub-token", default="")
    parser.add_argument("--show-text", action="store_true", help="打印消息正文（默认只打印元数据）")
    parser.add_argument(
        "--echo-reply",
        default="",
        help="收到消息后固定回一句（验证回发链路）。"
        "⚠️ 它会把 reply_mode 切到 pet：接**真实实现端**（真 NapCat）时，"
        "任何真实 QQ 消息都会被这句固定文本回复，也就是真的往 QQ 发消息。"
        "只在 --upstream none（纯 hub）或对着假上游时才安全",
    )
    parser.add_argument("--poke", default="", help="往 hub 推一条合成消息（验证插件下行）")
    parser.add_argument(
        "--call",
        action="append",
        default=[],
        help="上游连上后调用一次该 API（可重复），用于验证发送链路；"
        "建议用只读动作如 get_login_info / get_status / get_version_info",
    )
    parser.add_argument("--seconds", type=float, default=0.0, help="0 = 一直跑到 Ctrl-C")
    args = parser.parse_args()

    from meapet.onebot.service import OneBotService

    service = OneBotService(build_config(args))
    issues = service.validate()
    for issue in issues:
        print(f"[probe] 配置问题：{issue}", flush=True)

    started = time.time()
    seen_events = {"count": 0}

    if args.echo_reply and args.upstream != "none":
        # 这条不是形式主义：探针接的是**真实实现端**时，上游会把真实 QQ 消息送进来，
        # 而 --echo-reply 会把每一轮都结算成那句固定文本 → 经同一条上游**真的发到 QQ**。
        # 它比 --call 危险（--call 至少只在你指定动作时才发，且建议只读），
        # 所以必须醒目提示，而不是埋在帮助文本里。
        print(
            "[probe] ⚠️ 已启用 --echo-reply 且上游是 "
            f"{args.upstream}：**任何真实 QQ 消息都会被 '{args.echo_reply}' 回复**，"
            "也就是真的往 QQ 发消息。只想验回发链路请用 --upstream none（纯 hub）。",
            flush=True,
        )

    original = service._on_upstream_event

    def traced(payload, source):
        seen_events["count"] += 1
        kind = payload.get("post_type")
        if kind == "message":
            message_type = payload.get("message_type")
            who = payload.get("group_id") if message_type == "group" else payload.get("user_id")
            extra = ""
            if args.show_text:
                extra = f" text={payload.get('raw_message', '')!r}"
            print(
                f"[probe] ← message {message_type} {who} id={payload.get('message_id')}"
                f" src={source}{extra}",
                flush=True,
            )
        else:
            print(f"[probe] ← {kind}", flush=True)
        return original(payload, source)

    service._on_upstream_event = traced  # type: ignore[assignment]

    # 下游消费者（dsh 插件）发回来的 API 调用也打印出来：
    # 这是验证「插件 → MeaPet」这一侧的关键证据。
    original_hub_call = service.handle_hub_call

    async def traced_hub_call(action, params):
        result = await original_hub_call(action, params)
        shown = json.dumps(dict(params), ensure_ascii=False)
        print(
            f"[probe] ← hub 调用 {action} params={shown} → ok={result.ok} retcode={result.retcode}",
            flush=True,
        )
        return result

    service.handle_hub_call = traced_hub_call  # type: ignore[assignment]

    # 每次有新的 hub 消费者连上来都记一笔：这样消费者断线重连后仍会被 poke，
    # 而不是只在「数量第一次变多」时推一次。
    hub_opens = {"n": 0}
    original_hub_open = service._on_hub_open

    def traced_hub_open(session):
        original_hub_open(session)
        hub_opens["n"] += 1

    service._on_hub_open = traced_hub_open  # type: ignore[assignment]

    service.start()
    print(f"[probe] 已启动：{service.summary()}", flush=True)
    for status in service.status_snapshot():
        print(f"[probe]   {status.name}: {status.mode} {status.endpoint}", flush=True)

    if args.seconds <= 0:
        print("[probe] 运行中，Ctrl-C 退出。", flush=True)
    else:
        print(f"[probe] 将运行 {args.seconds:.0f} 秒。", flush=True)

    poked = 0
    called = False
    try:
        while True:
            time.sleep(0.5)
            # 上游连接数变化时提示一次
            upstream = len(service.upstream_sessions)
            hub = len(service.hub_sessions)
            snapshot = (upstream, hub, service.pending_count)
            if snapshot != getattr(main, "_last", None):
                main._last = snapshot  # type: ignore[attr-defined]
                print(
                    f"[probe] 状态：上游连接={upstream} hub 消费者={hub} "
                    f"待处理={service.pending_count} 事件累计={seen_events['count']}",
                    flush=True,
                )

            if args.call and not called and upstream > 0:
                called = True
                from meapet.async_runtime import submit

                for action in args.call:
                    submit(_call_api(service, action))

            if args.echo_reply:
                turn = service.take_ready_turn()
                if turn is not None:
                    print(f"[probe] → 回发 '{args.echo_reply}' 到 {turn.message.conversation_key}",
                          flush=True)
                    service.complete_turn(turn.turn_id, args.echo_reply)

            if args.poke and hub_opens["n"] > poked:
                poked = hub_opens["n"]
                print(
                    f"[probe] → 向 {hub} 个 hub 消费者推送合成消息：{args.poke!r}",
                    flush=True,
                )
                synthetic = {
                    "time": int(time.time()),
                    "self_id": int(args.self_id or 0),
                    "post_type": "message",
                    "message_type": "private",
                    "sub_type": "friend",
                    "message_id": 900001,
                    "user_id": 10001,
                    "message": args.poke,
                    "raw_message": args.poke,
                    "sender": {"user_id": 10001, "nickname": "probe", "card": "", "role": ""},
                }
                from meapet.async_runtime import submit

                submit(_publish(service, synthetic))

            if args.seconds > 0 and time.time() - started >= args.seconds:
                break
    except KeyboardInterrupt:
        print("\n[probe] 收到中断，正在停止…", flush=True)
    finally:
        service.stop(timeout_seconds=3.0)
        time.sleep(0.3)
        print("[probe] 已停止。", flush=True)
        print(f"[probe] 统计：{json.dumps(service.stats, ensure_ascii=False)}", flush=True)
    return 0


async def _call_api(service, action: str) -> None:
    """走 hub 调用路径打一次真实 API，验证「我们 → 实现端」这一侧。"""
    result = await service.handle_hub_call(action, {})
    data = json.dumps(result.data, ensure_ascii=False)
    print(
        f"[probe] → {action}: ok={result.ok} status={result.status} "
        f"retcode={result.retcode} data={data}",
        flush=True,
    )


async def _publish(service, payload) -> None:
    # 直接走服务的广播路径（等价于上游真的推了一条事件）
    service._on_upstream_event(payload, "probe")


if __name__ == "__main__":
    raise SystemExit(main())
