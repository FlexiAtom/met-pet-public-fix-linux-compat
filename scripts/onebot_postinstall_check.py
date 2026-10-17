"""dsh 插件安装后的运行期自检（重启 harness 之后跑）。

判据取 dsh-plugin-dev skill 的 ``references/install-and-verify.md``：只看退出码不算数，
必须打到真实路由、拿到真实载荷。

检查项：

1. 带 Bearer 请求 ``{prefix}/status`` → 200 + JSON（这是「插件真的加载了」的判据）。
2. 不带 token → 401（路由存在但鉴权生效；404 说明路由根本没注册）。
3. MeaPet 的 ``onebot.hub.access_token`` 与插件 ``meapetToken`` 是否一致
   （不一致时插件永远连不上 hub，但两边都不报错，最难查）。

用法：

    python scripts/onebot_postinstall_check.py --token <meapetToken>
    python scripts/onebot_postinstall_check.py            # token 从 profile patch 里读
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_token_from_patch(dsh_home: Path, profile: str, plugin: str) -> str:
    patch = dsh_home / "profiles" / profile / "cordis.patch.yml"
    if not patch.exists():
        return ""
    text = patch.read_text(encoding="utf-8", errors="replace")
    # 只在该插件的行块里找，避免抓到 astrbot-relay 的 token
    index = text.find(f"id: {plugin}")
    if index == -1:
        return ""
    block = text[index:]
    match = re.search(r"meapetToken:\s*'([^']*)'", block)
    if match and match.group(1).strip():
        return match.group(1).strip()
    match = re.search(r"meapetToken:\s*\"([^\"]*)\"", block)
    return match.group(1).strip() if match else ""


def first_session_verdict(payload, state_file_exists: bool):
    """把 `/status` 的读数翻译成「到底跑通过一轮没有」的结论。

    纯函数（便于单测）。返回 `(lines, needs_user_action)`：
    `needs_user_action=True` 表示"还差操作者发一条消息"——**这不是故障**，
    所以调用方不要把它算进失败项，否则会让人以为桥坏了。

    关键判据来自插件 `/status`：`conversations`（QQ↔dsh 会话映射条数）、
    `pending`（在途轮次）、`client.connected`，以及 `statePath` / `stateExisted`。
    """
    if not isinstance(payload, dict):
        return [], False

    client = payload.get("client") or {}
    connected = bool(client.get("connected"))
    conversations = int(payload.get("conversations") or 0)
    pending = payload.get("pending") or []
    state_path = str(payload.get("statePath") or "").strip()
    state_desc = state_path or "<DSH_HOME>/onebot-relay/state.json"

    if not connected:
        return (
            [f"TODO 还没连上 MeaPet hub，所以不会有会话（先启动桌宠，它会监听 14321）"],
            True,
        )

    if conversations == 0:
        return (
            [
                "TODO 桥已连通，但**还没有任何会话**：请在 QQ 里给机器人发一条消息"
                "（群聊需先 @ 它 —— 你这份配置是 requireAt=true）",
                f"     发完消息后预期：conversations 变为 1，并生成 {state_desc}",
            ],
            True,
        )

    lines = [f"PASS 已观测到 {conversations} 个 QQ↔dsh 会话映射（QQ → dsh 方向已打通）"]
    if pending:
        lines.append(f"     仍有 {len(pending)} 个在途轮次（模型回复中或等待回发 QQ）")
    else:
        lines.append("     当前没有在途轮次（上一轮已结算）")
    if state_file_exists:
        lines.append(f"PASS 会话映射文件已落盘：{state_desc}")
    else:
        lines.append(f"WARN 有会话映射但文件还没落盘（可能刚建立、尚未持久化）：{state_desc}")
    return lines, False


def detect_active_profile(dsh_home: Path, plugin: str) -> dict:
    """推断**活跃** profile，并看插件装在哪些 profile 里（纯函数，便于单测）。

    为什么需要它：`404（路由没有注册）` 有一个非常容易踩的成因——**插件装是装了，
    但装进了错的 profile**。harness 启动时会重写它正在跑的那个 profile 的
    `cordis.yml`，所以「`cordis.yml` 最新的那个」通常就是活跃 profile。
    （实测踩过一次：插件在 `web`，harness 跑的是 `desktop`，症状就是永远 404。）
    """
    result: dict = {"active": "", "mtime": 0.0, "installed": False, "installed_in": []}
    profiles = dsh_home / "profiles"
    if not profiles.is_dir():
        return result
    for entry in sorted(profiles.iterdir()):
        if not entry.is_dir() or entry.name == "node_modules":
            continue
        if (entry / "node_modules" / plugin).is_dir():
            result["installed_in"].append(entry.name)
        composed = entry / "cordis.yml"
        if composed.is_file():
            try:
                mtime = composed.stat().st_mtime
            except OSError:
                continue
            if mtime > result["mtime"]:
                result["active"] = entry.name
                result["mtime"] = mtime
    result["installed"] = bool(result["active"]) and result["active"] in result["installed_in"]
    return result


def request(url: str, token: str, timeout: float = 6.0):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser(description="dsh-onebot-relay 运行期自检")
    parser.add_argument("--harness", default="http://127.0.0.1:19387",
                        help="harness Web 端口（也就是本 GUI 的地址）")
    parser.add_argument("--prefix", default="/onebot-relay")
    parser.add_argument("--profile", default="",
                        help="不传则自动推断活跃 profile（看各 profile 的 cordis.yml 谁最新）")
    parser.add_argument("--plugin", default="dsh-onebot-relay")
    parser.add_argument("--token", default="")
    parser.add_argument("--meapet-config", default=str(ROOT / "config.json"))
    args = parser.parse_args()

    dsh_home = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
    # 不传 --profile 时自动推断：装错 profile 是 404 最常见的成因（实测踩过）
    info = detect_active_profile(dsh_home, args.plugin)
    profile = args.profile.strip() or info["active"] or "web"
    if not args.profile.strip() and info["active"]:
        print(f"[check] 自动推断活跃 profile = {profile}（各 profile 里 cordis.yml 最新的那个）")
    token = args.token.strip() or read_token_from_patch(dsh_home, profile, args.plugin)
    if not token:
        print("FAIL 拿不到 meapetToken（既没传 --token，profile patch 里也没找到）")
        return 2
    print(f"[check] 使用 token {token[:8]}…（{len(token)} 字符）")

    failures = 0
    url = f"{args.harness.rstrip('/')}{args.prefix}/status"

    # 2) 先测无 token（404 = 路由没注册；401 = 路由在、鉴权生效）
    status, body = request(url, "")
    if status == 404:
        print(f"FAIL 无 token 请求 {url} → 404：路由没有注册，插件没加载"
              "（检查 dsh.profile.bundles 与是否真的重启了 harness）")
        failures += 1
        # 404 的两个常见成因，这里直接把排查方向打出来
        if info["active"]:
            installed_in = info["installed_in"]
            if installed_in and not info["installed"]:
                print(f"     ⚠️ 插件装在 {', '.join(installed_in)}，**不在活跃 profile "
                      f"({info['active']}) 里** —— 这就是 404 的成因")
                print(f"        解决：node scripts/install-into-profile.mjs --profile {info['active']} "
                      "--self-id <你的QQ> --meapet-token <hub token> --cwd <本仓库>")
            elif not installed_in:
                print(f"     插件还没装进任何 profile 的 node_modules —— 先跑安装器"
                      f"（--profile {info['active']}）")
            else:
                print("     插件已在活跃 profile 里 —— 那多半是 **harness 没重启**"
                      "（对比它的启动时间与插件安装时间）")
        else:
            print("     诊断：没找到任何带 cordis.yml 的 profile（DSH_HOME / --profile 对不对？）")
    elif status == 401:
        print(f"PASS 无 token → 401（路由存在且鉴权生效）")
    elif status == 0:
        print(f"FAIL 连不上 {url}：{body}")
        failures += 1
    else:
        print(f"WARN 无 token → {status}（预期 401；插件可能未开鉴权）body={body[:120]}")

    # 1) 带 token
    status, body = request(url, token)
    payload = None
    if status == 200:
        try:
            payload = json.loads(body)
            print("PASS 带 token → 200（插件已加载并挂载了路由）")
        except Exception:
            print(f"WARN 带 token → 200 但不是 JSON：{body[:200]}")
    else:
        print(f"FAIL 带 token → {status}：{body[:200]}")
        failures += 1

    # 2) 插件自报状态：这一步才是"桥能不能用"的关键
    if isinstance(payload, dict):
        client = payload.get("client") or {}
        connected = bool(client.get("connected"))
        print(f"     插件自报：plugin={payload.get('plugin')} selfId={payload.get('selfId')} "
              f"conversations={payload.get('conversations')} "
              f"serverEnabled={payload.get('serverEnabled')}")
        print(f"     连 MeaPet hub：connected={connected} opened={client.get('opened')} "
              f"attempts={client.get('attempts')} url={client.get('url')}")
        if connected:
            print("PASS 插件已连上 MeaPet hub —— 现在可以给机器人发消息了")
        else:
            print("WARN 插件已加载，但**还没连上** MeaPet hub"
                  "（这一步不算产品故障，通常是桌宠没在跑）")
            print("     下一步：先启动桌宠（.\\启动桌宠.bat），它会监听 14321 并提供 hub；"
                  "插件会按 reconnectInitialMs 自动重连")
            if client.get("lastError"):
                print(f"     插件最近一次错误：{client['lastError']}")

    # 3) 交叉核对 MeaPet 的 hub token
    config_path = Path(args.meapet_config)
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            onebot_cfg = config.get("onebot") or {}
            hub_cfg = onebot_cfg.get("hub") or {}
            hub_token = str(hub_cfg.get("access_token") or "")
            if not hub_token:
                print("WARN MeaPet 的 onebot.hub.access_token 为空：插件会连不上（hub 不鉴权就两边一致为空才正常）")
            elif hub_token == token:
                print("PASS MeaPet hub.access_token 与插件 meapetToken 一致")
            else:
                print("FAIL MeaPet hub.access_token 与插件 meapetToken 不一致 —— 插件连不上 hub，"
                      "但两边都不会报错")
                failures += 1
            # 端口也顺手对一下，避免"改了配置但没重启桌宠"这类问题
            hub_port = hub_cfg.get("port")
            if hub_port and isinstance(payload, dict) and payload.get("client", {}).get("url"):
                plugin_url = str(payload["client"]["url"])
                if f":{hub_port}/" not in plugin_url:
                    print(f"FAIL 端口不一致：MeaPet hub.port={hub_port}，"
                          f"插件连的是 {plugin_url}")
                    failures += 1
                else:
                    print(f"PASS 端口一致（{hub_port}）")
        except Exception as exc:
            print(f"WARN 读取 {config_path} 失败：{type(exc).__name__}")
    else:
        print(f"WARN 找不到 MeaPet 配置 {config_path}，跳过 token 交叉核对")

    # 4) 首次会话判据：桥通了 ≠ 跑通过一轮
    needs_user_action = False
    if isinstance(payload, dict):
        state_path = str(payload.get("statePath") or "").strip()
        state_file = Path(state_path) if state_path else (dsh_home / "onebot-relay" / "state.json")
        lines, needs_user_action = first_session_verdict(payload, state_file.exists())
        for line in lines:
            print(line)

    print(f"\n[check] {args.prefix}/status 判据完成，失败项：{failures}")
    if needs_user_action and failures == 0:
        print("[check] 结论：插件侧一切就绪，**还差一步（不是故障）**——按上面的 TODO 发一条真实消息即可")
    print("[check] 仍需人工确认（脚本看不到 harness stdout）：日志里应出现 [dsh-onebot-relay] 前缀，"
          "且首次会话后应生成 <DSH_HOME>/onebot-relay/state.json")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
