"""一条命令跑完整条桥的端到端验证（不需要 QQ 交互，也不需要 LLM 凭据）。

两种模式：

**默认（in-process）**：本进程内直接起 :class:`OneBotService` 当 MeaPet 侧。
轻、快、可断言**双向**。

**``--via-pet``**：起**真实的桌宠进程**（离屏）当 MeaPet 侧，再用一个假 NapCat 客户端
连到它的反向 WS 端点注入一条消息。更接近真实部署：走的是桌宠自己的 ``_init_onebot``、
真实 uvicorn、真实 hub。

``--via-pet`` 只做**下行**断言，原因见下面「为什么 via-pet 不做上行」——那条会真的往 QQ 发消息。

共同断言：

- 插件被真实 dsh 加载（dsh 侧脚本的判据 1/2）；
- **下行**：注入一条 ``message`` → 插件建出 dsh 会话（``conversations >= 1``，即真的调了 ``agents.create``）；
- **上行**（仅默认模式）：推一条 ``/status`` 本地指令 → 插件回发 ``send_private_msg``，
  且 ``user_id`` 与文案正确。

退出码：0 = 全部 PASS；1 = 断言失败；2 = 环境不具备（找不到 dsh CLI）。

用法：

    python scripts/onebot_bridge_e2e.py
    python scripts/onebot_bridge_e2e.py --via-pet
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SELF_ID = "123456789"
DEFAULT_USER_ID = 10001


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def log(message: str) -> None:
    print(f"[e2e] {message}", flush=True)


def load_meapet_onebot() -> Dict[str, Any]:
    """读仓库 config.json 的 onebot 段（走真实规范化，拿到默认值）。"""
    from meapet.config.store import load_config

    return dict((load_config(str(ROOT / "config.json")) or {}).get("onebot") or {})


def private_message(text: str, user_id: int, self_id: int, message_id: int) -> Dict[str, Any]:
    return {
        "time": int(time.time()),
        "self_id": self_id,
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "message_id": message_id,
        "user_id": user_id,
        "message": text,
        "raw_message": text,
        "sender": {"user_id": user_id, "nickname": "e2e", "card": "", "role": ""},
    }


def plugin_status(port: int, token: str, timeout: float = 5.0) -> Optional[dict]:
    request = urllib.request.Request(f"http://127.0.0.1:{port}/onebot-relay/status")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def wait_until(predicate, timeout: float, interval: float = 0.5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def pet_reply_mode_blocks_via_pet(reply_mode: Any) -> bool:
    """`--via-pet` 是否必须拒绝这个 `reply_mode`。

    `reply_mode` 含 `pet` 时，脚本注入的合成消息会被桌宠**真的回复**，
    而桌宠会经它自己的上游（生产配置里就是**真实 NapCat**）把这条回复发给
    注入时用的 `user_id`——那是一个真实 QQ 号。所以这里只放行 `hub`。
    """
    return str(reply_mode or "").strip().lower() not in ("", "hub")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OneBot 桥端到端验证（MeaPet ↔ 真实 dsh）")
    parser.add_argument("--via-pet", action="store_true",
                        help="用真实桌宠进程当 MeaPet 侧（只做下行断言）")
    parser.add_argument("--self-id", default=DEFAULT_SELF_ID)
    parser.add_argument("--user-id", type=int, default=DEFAULT_USER_ID)
    parser.add_argument("--hub-port", type=int, default=0, help="0 = 自动挑（via-pet 时忽略，用 config 里的）")
    parser.add_argument("--harness-port", type=int, default=0)
    parser.add_argument("--meapet-token", default="e2e-token-0123456789abcdef",
                        help="hub 与插件共用的 token（via-pet 时默认取 config 里的）")
    parser.add_argument("--hold-ms", type=int, default=60000)
    parser.add_argument("--cwd", default=str(ROOT))
    parser.add_argument("--keep-home", action="store_true")
    return parser.parse_args()


def inject_via_reverse_ws(port: int, path: str, token: str, payload: dict) -> bool:
    """扮演一个 NapCat，往桌宠的反向 WS 端点推一条事件。"""
    from websockets.asyncio.client import connect

    async def run() -> bool:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with connect(
                f"ws://127.0.0.1:{port}{path}", additional_headers=headers,
                open_timeout=6, max_size=None,
            ) as websocket:
                await websocket.send(json.dumps(payload, ensure_ascii=False))
                await asyncio.sleep(0.6)
                return True
        except Exception as exc:
            log(f"注入失败：{type(exc).__name__}: {exc}")
            return False

    return asyncio.run(run())


def main() -> int:
    args = parse_args()
    harness_port = args.harness_port or free_port()
    errors: List[str] = []
    hub_calls: List[Dict[str, Any]] = []
    service = None
    pet: Optional[subprocess.Popen] = None

    home_dir = ROOT / ".tmp" / "onebot-e2e-home"
    log_path = Path(tempfile.gettempdir()) / "onebot-bridge-e2e-dsh.log"
    pet_log_path = ROOT / ".tmp" / "onebot-e2e-pet.log"
    node_script = ROOT / "dsh-onebot-relay" / "scripts" / "verify-in-throwaway-harness.mjs"
    if not node_script.exists():
        log(f"FAIL 找不到 {node_script}")
        return 1

    # ---------------------------------------------------------------- #
    # MeaPet 侧（两种模式之一）
    # ---------------------------------------------------------------- #
    if args.via_pet:
        cfg = load_meapet_onebot()
        if not cfg.get("enabled"):
            log("FAIL --via-pet 需要 config.json 里 onebot.enabled=true")
            return 2
        if pet_reply_mode_blocks_via_pet(cfg.get("reply_mode")):
            # ⚠️ 这条不是洁癖：含 pet 的模式下，注入的合成消息会让桌宠真的产生回复，
            # 而它会经真实 NapCat 发到注入时用的 user_id（默认 10001，一个真实 QQ 号）。
            log(
                f"FAIL --via-pet 只允许 reply_mode=hub（当前 {cfg.get('reply_mode')!r}）："
                "含 pet 的模式下，注入的消息会被桌宠真的回复，并可能经真实 NapCat 发到 QQ。"
                "请先把 config.json 的 onebot.reply_mode 改成 hub，或改用默认（进程内 hub）模式 —— "
                "那里用的是本进程内的假 hub，不需要碰真实配置。"
            )
            return 2
        up = cfg.get("reverse_ws") or {}
        hub = cfg.get("hub") or {}
        hub_port = int(hub.get("port") or 14321)
        hub_token = str(hub.get("access_token") or "")
        up_port = int(up.get("port") or hub_port)
        up_path = str(up.get("path") or "/onebot/v11/ws")
        up_token = str(up.get("access_token") or "")
        token = args.meapet_token if args.meapet_token != "e2e-token-0123456789abcdef" else hub_token
        pet_log_path.parent.mkdir(parents=True, exist_ok=True)
        sink = open(pet_log_path, "w", encoding="utf-8")
        pet = subprocess.Popen(
            [sys.executable, str(ROOT / "pet.py")],
            cwd=str(ROOT),
            stdout=sink,
            stderr=subprocess.STDOUT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )
        log(f"已启动真实桌宠（PID {pet.pid}，日志 {pet_log_path}），等它监听 {hub_port}…")
        if not wait_until(lambda: port_open(hub_port), timeout=90):
            errors.append(f"桌宠没有在 90s 内监听 {hub_port}")
    else:
        from meapet.async_runtime import submit
        from meapet.onebot.service import OneBotService

        hub_port = args.hub_port or free_port()
        token = args.meapet_token
        service = OneBotService(
            {
                "enabled": True,
                "self_ids": [int(args.self_id)],
                "reply_mode": "hub",
                "hub": {
                    "enabled": True,
                    "host": "127.0.0.1",
                    "port": hub_port,
                    "ws_path": "/onebot/v11/hub",
                    "http_path": "/onebot/v11/hub/http",
                    "access_token": token,
                    "publish_events": True,
                    "allow_send": True,
                },
                "queue": {"max_pending": 8, "turn_timeout_s": 60.0},
            }
        )
        original_hub_call = service.handle_hub_call

        async def recording_hub_call(action, params):
            result = await original_hub_call(action, params)
            hub_calls.append({"action": action, "params": dict(params), "ok": result.ok,
                              "retcode": result.retcode})
            log(f"← 插件回发 {action} params={json.dumps(dict(params), ensure_ascii=False)[:200]} "
                f"ok={result.ok} retcode={result.retcode}")
            return result

        service.handle_hub_call = recording_hub_call  # type: ignore[assignment]
        service.start()
        log(f"MeaPet hub 已起（进程内）：ws://127.0.0.1:{hub_port}/onebot/v11/hub（reply_mode=hub）")

    # ---------------------------------------------------------------- #
    # dsh 侧：一次性真实 harness
    # ---------------------------------------------------------------- #
    command = [
        "node", str(node_script),
        "--self-id", str(args.self_id),
        "--meapet-token", token,
        "--meapet-ws-url", f"ws://127.0.0.1:{hub_port}/onebot/v11/hub",
        "--cwd", str(args.cwd),
        "--home", str(home_dir),
        "--port", str(harness_port),
        "--hold-ms", str(args.hold_ms),
    ]
    log(f"启动 dsh 侧：{' '.join(command[1:])}")
    log(f"（dsh 侧输出写入 {log_path}）")
    child = None
    try:
        with open(log_path, "w", encoding="utf-8") as sink2:
            child = subprocess.Popen(command, cwd=str(ROOT), stdout=sink2, stderr=subprocess.STDOUT)

        # 插件连上 hub（用插件自报的 client.connected，两种模式通用）
        connected = wait_until(
            lambda: (plugin_status(harness_port, token) or {}).get("client", {}).get("connected"),
            timeout=150,
        )
        if not connected:
            errors.append("插件没有连上 hub（client.connected 仍为 false）")
        else:
            log("PASS 插件已连上 hub（client.connected=true）")

        # 下行：注入一条普通私聊消息 → 插件应建出 dsh 会话
        sequence = 900001
        deadline = time.time() + 150
        while time.time() < deadline and not errors:
            status = plugin_status(harness_port, token) or {}
            if status.get("conversations", 0) >= 1:
                break
            if args.via_pet:
                inject_via_reverse_ws(
                    up_port, up_path, up_token,
                    private_message("e2e 下行验证", args.user_id, int(args.self_id), sequence),
                )
            else:
                service._on_upstream_event(
                    private_message("e2e 下行验证", args.user_id, int(args.self_id), sequence),
                    "e2e",
                )
            sequence += 1
            time.sleep(5)
        status = plugin_status(harness_port, token) or {}
        if status.get("conversations", 0) < 1:
            errors.append("插件没有建出会话（下行方向；conversations 仍为 0）")
        else:
            log(f"PASS 下行方向：插件建出会话 conversations={status.get('conversations')} "
                f"pending={len(status.get('pending') or [])}"
                + ("（经由真实桌宠进程）" if args.via_pet else ""))

        # 上行：只在 in-process 模式做。via-pet 模式下这条会让桌宠把
        # send_private_msg 转给**真实 NapCat**，从而真的往一个 QQ 号发消息——不做。
        if not args.via_pet and not errors:
            sequence = 910001
            reply_deadline = time.time() + 90
            while time.time() < reply_deadline and not any(
                call["action"].startswith("send_") for call in hub_calls
            ):
                service._on_upstream_event(
                    private_message("/status", args.user_id, int(args.self_id), sequence), "e2e"
                )
                sequence += 1
                time.sleep(4)
            call = next((c for c in hub_calls if c["action"].startswith("send_")), None)
            if call is None:
                errors.append("没有等到插件的回发 API 调用（上行方向）")
            elif call["params"].get("user_id") != args.user_id:
                errors.append(f"回发的 user_id 是 {call['params'].get('user_id')}，期望 {args.user_id}")
            elif "会话" not in str(call["params"].get("message", "")):
                errors.append(f"回发文案不像 /status 的作答：{call['params'].get('message')!r}")
            else:
                log("PASS 上行方向：插件把回复以正确的 OneBot 帧发回 MeaPet")
        elif args.via_pet:
            log("（via-pet 模式跳过上行断言：那会让桌宠向真实 NapCat 发一条 QQ 消息）")

        # 顺带确认插件自报状态
        status = plugin_status(harness_port, token)
        if status is None:
            errors.append("打不通插件的 /status（插件可能没加载）")
        else:
            log(f"插件自报：plugin={status.get('plugin')} selfId={status.get('selfId')} "
                f"client.connected={status.get('client', {}).get('connected')}")
    finally:
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except Exception:
                child.kill()
        if service is not None:
            service.stop(timeout_seconds=3.0)
        if pet is not None:
            pet.terminate()
            try:
                pet.wait(timeout=15)
            except Exception:
                pet.kill()
        if home_dir.exists() and not args.keep_home:
            shutil.rmtree(home_dir, ignore_errors=True)

    dsh_log = log_path.read_text(encoding="utf-8", errors="replace")
    # dsh 侧是在 --hold-ms 期间被我们终止的，不会打印最后那句 ALL PASS；
    # 需要确认的是两条**判据**（它们在 hold 之前就打完了）。
    for marker in ("PASS 判据 1", "PASS 判据 2"):
        if marker not in dsh_log:
            errors.append(f"dsh 侧日志缺少「{marker}」（插件可能没被真实 harness 加载）")

    if errors:
        for item in errors:
            log(f"FAIL {item}")
        log(f"dsh 侧日志（保留，便于排查）：{log_path}")
        if args.via_pet:
            log(f"桌宠日志：{pet_log_path}")
        log("dsh 侧日志尾部：\n" + "\n".join(dsh_log.splitlines()[-20:]))
        return 1

    log_path.unlink(missing_ok=True)
    mode = "真实桌宠进程" if args.via_pet else "进程内 hub"
    log(f"ALL PASS：MeaPet（{mode}）↔ 真实 dsh 双向验证通过"
        + ("" if args.via_pet else "（下行建会话 + 上行回发 OneBot 帧）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
