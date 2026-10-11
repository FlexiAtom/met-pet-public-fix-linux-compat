"""验证**桌宠自己的 QQ 回复闭环**（``reply_mode=pet``）：QQ 消息 → 桌宠 LLM → 回复回 QQ。

这是整条链路里最后一条没验过的生产路径：前面的验证里，``reply_mode=hub`` 只走「转发给下游」，
桌面侧的回发挂钩（``_notify_channel_turn_finished`` → ``complete_turn`` → ``send_private_msg``）
只在单元测试里用替身验过。这里用一个**桩 LLM** 把这一步补上。

**为什么不碰真实 QQ**：反向 WS 的 ``access_token`` 换成只在本次运行里使用的随机值，
于是**只有本脚本的假 NapCat 能连上**桌宠；真实 NapCat 连不上（它会在我们跑完后自己重连）。
再加上桌宠按事件来源（``IncomingMessage.source``）选回发通道，回复只会发回我们这条连接。

用法：

    python scripts/onebot_pet_reply_e2e.py
    python scripts/onebot_pet_reply_e2e.py --seconds 90 --keep-config
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STUB_REPLY = "梅尔收到啦，这是桩模型的回复喵"


def log(message: str) -> None:
    print(f"[reply-e2e] {message}", flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _StubLLM(BaseHTTPRequestHandler):
    """最小的 OpenAI 兼容 /v1/chat/completions（SSE），只回一句固定文本。"""

    requests: list = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            body = {}
        type(self).requests.append(
            {"path": self.path, "model": body.get("model"), "stream": body.get("stream"),
             "messages": len(body.get("messages") or []),
             "systemChars": sum(len(str(m.get("content") or ""))
                                for m in (body.get("messages") or [])
                                if m.get("role") == "system"),
             "lastRole": (body.get("messages") or [{}])[-1].get("role")}
        )
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": STUB_REPLY}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        data = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:  # 静音
        return


def start_stub_llm(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _StubLLM)
    thread = threading.Thread(target=server.serve_forever, name="stub-llm", daemon=True)
    thread.start()
    return server


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
        "sender": {"user_id": user_id, "nickname": "reply-e2e", "card": "", "role": ""},
    }


def run_fake_napcat(port: int, path: str, token: str, payload: dict,
                    result: Dict[str, Any], timeout: float) -> None:
    """在后台线程里扮演 NapCat：连上桌宠的反向 WS，发一条消息，等回发。"""
    from websockets.asyncio.client import connect

    async def run() -> None:
        deadline = time.time() + 45
        websocket = None
        while time.time() < deadline:
            try:
                websocket = await connect(
                    f"ws://127.0.0.1:{port}{path}",
                    additional_headers={"Authorization": f"Bearer {token}"},
                    open_timeout=6, max_size=None,
                )
                break
            except Exception as exc:
                result["connectError"] = f"{type(exc).__name__}: {exc}"
                await asyncio.sleep(1.0)
        if websocket is None:
            return
        result["connected"] = True
        try:
            await websocket.send(json.dumps(payload, ensure_ascii=False))
            result["sent"] = True
            wait_until = time.time() + timeout
            while time.time() < wait_until:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=3)
                except asyncio.TimeoutError:
                    continue
                except Exception as exc:
                    result["recvError"] = f"{type(exc).__name__}: {exc}"
                    return
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                if isinstance(frame, dict) and "action" in frame:
                    result["frame"] = frame
                    # 扮演实现端回一个 ok，让桌宠记账
                    await websocket.send(json.dumps(
                        {"status": "ok", "retcode": 0, "data": {"message_id": 1},
                         "echo": frame.get("echo")}
                    ))
                    return
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    asyncio.run(run())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="桌宠自己的 QQ 回复闭环验证（桩 LLM）")
    parser.add_argument("--self-id", default="123456789")
    parser.add_argument("--user-id", type=int, default=10001)
    parser.add_argument("--seconds", type=float, default=75.0,
                        help="桌宠跑多久后走真实 _quit 退出")
    parser.add_argument("--reply-timeout", type=float, default=45.0,
                        help="等桌宠回发的最长时间")
    parser.add_argument("--keep-config", action="store_true",
                        help="保留临时配置（便于排查）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list = []

    # 桩 LLM
    llm_port = free_port()
    stub = start_stub_llm(llm_port)
    log(f"桩 LLM 已起：http://127.0.0.1:{llm_port}/v1/chat/completions")

    # 反向 WS：只让本脚本的假 NapCat 连上（随机 token），真实 NapCat 连不上
    rev_port = free_port()
    test_token = secrets.token_urlsafe(24)

    # 临时配置：基于真实 config.json，覆盖 onebot / llm / tts / watcher
    real_config_path = ROOT / "config.json"
    base: Dict[str, Any] = {}
    if real_config_path.exists():
        try:
            base = json.loads(real_config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log(f"WARN 读真实 config.json 失败（{type(exc).__name__}），改用空配置")
    config = copy.deepcopy(base)
    config["llm"] = {
        "mode": "direct",
        "direct": {
            "provider": "custom",
            "protocol": "openai_chat",
            "api_base": f"http://127.0.0.1:{llm_port}/v1",
            "model": "stub-model",
            "api_key": "stub-key",
            "temperature": 0.0,
            "max_tokens": 64,
        },
    }
    config["onebot"] = {
        "enabled": True,
        "self_ids": [int(args.self_id)],
        "reply_mode": "pet",
        "reverse_ws": {
            "enabled": True, "host": "127.0.0.1", "port": rev_port,
            "path": "/ws", "access_token": test_token,
        },
        "hub": {"enabled": False},
        "allow": {"private": True, "groups": False, "require_at": False},
        "queue": {"max_pending": 8, "turn_timeout_s": 120.0},
    }
    config["tts"] = {"enabled": False}
    config["watcher"] = {"enabled": False}
    config["vision"] = {"enabled": False, "mode": "disabled"}
    config["agent_control"] = {"enabled": False}
    display = dict(config.get("display") or {})
    display.update({"reduced_motion": True, "scale": 0.2, "fps": 10})
    config["display"] = display

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    config_path = tmp_dir / "onebot-reply-e2e-config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"临时配置：{config_path}（真实 config.json 未被改动）")

    result: Dict[str, Any] = {}
    payload = private_message("你好呀", args.user_id, int(args.self_id), 880001)
    worker: Optional[threading.Thread] = None
    pet = None
    exit_code = 0

    try:
        from PyQt5.QtCore import QTimer
        from PyQt5.QtWidgets import QApplication

        from meapet.desktop.app import MeaPet

        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        log("构造 MeaPet（离屏）…")
        pet = MeaPet(str(config_path))

        service = getattr(pet, "_onebot_service", None)
        if service is None:
            errors.append("桌宠的 OneBot 服务没有起来（看 [init] onebot 那行日志）")
        else:
            log(f"OneBot 服务已起：{service.summary()}，反向 WS 监听 {rev_port}")

        worker = threading.Thread(
            target=run_fake_napcat,
            args=(rev_port, "/ws", test_token, payload, result, args.reply_timeout),
            name="fake-napcat", daemon=True,
        )
        worker.start()

        QTimer.singleShot(int(args.seconds * 1000), pet._quit)
        app.exec_()
        log("事件循环已返回")
    finally:
        if worker is not None:
            worker.join(timeout=10)
        stub.shutdown()
        if not args.keep_config and config_path.exists():
            config_path.unlink()

    # ---- 断言 ---------------------------------------------------------- #
    if not result.get("connected"):
        errors.append(f"假 NapCat 没能连上桌宠的反向 WS（{result.get('connectError')}）")
    if not _StubLLM.requests:
        errors.append("桩 LLM 一次都没被调用（桌宠没走 LLM）")
    else:
        log(f"桩 LLM 收到 {len(_StubLLM.requests)} 次请求：")
        for index, item in enumerate(_StubLLM.requests, 1):
            log(f"  #{index} {json.dumps(item, ensure_ascii=False)}")

    frame = result.get("frame")
    if not frame:
        errors.append(f"没有等到桌宠回发的 API 调用（{result.get('recvError') or '超时'}）")
    else:
        action = frame.get("action")
        params = frame.get("params") or {}
        if action != "send_private_msg":
            errors.append(f"回发的 action 是 {action!r}，期望 send_private_msg")
        elif params.get("user_id") != args.user_id:
            errors.append(f"回发的 user_id 是 {params.get('user_id')}，期望 {args.user_id}")
        elif STUB_REPLY not in str(params.get("message", "")):
            errors.append(f"回发文案里没有桩模型的文本：{params.get('message')!r}")
        else:
            log(f"PASS 桌宠把 LLM 的回复回发到了原会话：{params.get('message')!r}")

    if errors:
        for item in errors:
            log(f"FAIL {item}")
        return 1

    log("ALL PASS：reply_mode=pet 的完整闭环成立"
        "（QQ 消息 → 桌宠 LLM → 分段呈现 → 回发 send_private_msg 到原连接）")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
