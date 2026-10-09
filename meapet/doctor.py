"""`meapet --doctor` —— 不进 GUI 的**只读**取证快照。

人工 2026-10-08 放行这句：「无头模式是给AI用的」「目的是测试，不论是否打包」。三条口径写在这里，
别处不复述：
- **只读**：不写 config、不装东西、不挂载 surface、不建窗口。唯一的外设是读日志尾部与只读的
  import/模块规格探测。（`--with-gui` 会建一个 Qt 应用对象去取屏幕几何与输入阈值那两格——那是显式
  opt-in，默认关着：无显示服务时建 Qt 应用不是抛异常，是 abort，catch 不到。）
  唯一的**写侧**例外是 `--speech-latency`：它真起推理子进程并往 `tts.output_dir` 落 wav
  （合成不播放，所以不出声）。默认档一条都不跑，因为那一发按引擎是十几秒到两分钟。
  另有一档**外设**例外是 `--probe-agent`：它先问一次 TCP 端口在不在听，在听才真开一次 WebSocket
  去问 gateway 在不在场，且**只连回路**（127.0.0.1/::1/localhost），非回路端点报"未连接"而不去连——
  往外发请求不在这个入口的授权里。默认档同样一次都不发。
  第三档是 `--probe-source URL`（探候选内容源）：真发一次 GET，但同样**默认只连回路**，
  往回路外发须再显式带 `--allow-egress`——新增外联要人工明确点头，这一档就是为那句留的闸。
- **每一项后面直接跟判据**（这条在说什么 / 什么算坏）。否则"这台机器没有 layer-shell"和
  "我们的桥接层没编进包"会被读成同一句话——那正是本入口要消灭的形状。
- **探测项一律调产品侧那一个函数**，这里不重算。重算就是第二套真值，doctor 会开始自证。

退出码：0 = 探测跑完了（**读数坏不坏不影响这个数**，坏法要出现在文本里）；
1 = doctor 自己没跑起来（那种时候没有任何读数，不能与"跑完但一切正常"共用 0）。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import ipaddress
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

DOCTOR_FLAG = "--doctor"

#: 只打这些键：都是布尔/枚举档，值本身不携密。密钥面（api_key 等）压根不进白名单。
CONFIG_SUMMARY_KEYS = (
    ("live2d", "enabled"),
    ("fidus", "enabled"),
    ("voice_input", "enabled"),
    ("vision", "mode"),
    ("tts", "enabled"),
    ("tts", "engine"),
    ("tts", "voice_lang"),
    ("tts", "timeout"),
)


@dataclass(frozen=True)
class Item:
    """一行读数：名字、值、以及"这条在说什么"。"""

    name: str
    value: str
    judge: str


def doctor_requested(argv: object = None) -> bool:
    """这条命令是不是来取证的。**整词匹配**——`--doctors` 那种参数不该被劫持成取证。"""
    return DOCTOR_FLAG in [str(arg) for arg in (argv if argv is not None else sys.argv)]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="meapet --doctor",
        description="不进 GUI 的只读环境快照（给 AI / 维护者取读数用）",
    )
    parser.add_argument(DOCTOR_FLAG, action="store_true", help="跑取证并退出")
    parser.add_argument(
        "--doctor-format",
        choices=("text", "json"),
        default="text",
        help="text=人读的一行一项；json=机器读（stdout 只有 JSON，其余串到 stderr）",
    )
    parser.add_argument(
        "--config", type=str, help="指定 config.json 路径（默认走产品那一条 config_path()）"
    )
    parser.add_argument(
        "--with-gui",
        action="store_true",
        help="建一个 Qt 应用对象以取屏幕几何（无显示服务时可能直接 abort，默认不开）",
    )
    parser.add_argument(
        "--skip-tts", action="store_true", help="跳过两路 TTS 前置探测（那两格最慢，各跑一次 health_check）"
    )
    parser.add_argument(
        "--skip-layer", action="store_true", help="跳过 layer 桥接层探测（它会真去 init 一次）"
    )
    parser.add_argument(
        "--speech-latency",
        action="store_true",
        help="每路引擎再各真合成两发并报逐发秒数（本入口唯一的写侧：起推理子进程 + 落 wav，不播放）",
    )
    parser.add_argument(
        "--probe-agent",
        action="store_true",
        help="额外探一次 agent 通道（gateway）在场性：先问 TCP 再真连 WebSocket，**只连回路**，非回路端点不连",
    )
    parser.add_argument(
        "--probe-source",
        action="append",
        default=[],
        metavar="URL",
        help="探一个候选内容源（点击反馈那两行的取证）：逐请求秒级超时，只印状态/字节/首个可见文本片段；可重复",
    )
    parser.add_argument(
        "--allow-egress",
        action="store_true",
        help="配合 --probe-source：允许真往回路外发请求。**新增外联**，须人工明确点头后才带这个",
    )
    return parser.parse_args(argv)


def _probe(name: str, judge: str, thunk) -> Item:
    """探测**自身**的失败也要变成一行读数，而不是 traceback——无头那三个字判的就是这一格。"""
    from meapet.utils import redact_text

    try:
        value = thunk()
    except Exception as exc:  # noqa: BLE001 - 取证面不许把异常递给调用方
        value = f"探测自身失败 {type(exc).__name__}: {exc}"
    return Item(name=name, value=redact_text(str(value)), judge=judge)


def _runtime() -> str:
    from meapet import __version__
    from meapet.paths import is_frozen

    return (
        f"版本 {__version__} · {platform.system()} {platform.release()} · "
        f"python {platform.python_version()} · sys.platform={sys.platform} · "
        f"frozen={is_frozen()} · exe={sys.executable}"
    )


def _dirs() -> str:
    from meapet.log import LOG_DIR
    from meapet.paths import get_data_dir

    root = get_data_dir()
    return (
        f"数据目录={root}（存在={os.path.isdir(root)}） · "
        f"日志目录={LOG_DIR}（存在={os.path.isdir(LOG_DIR)}）"
    )


def _config_summary(config: dict) -> str:
    from meapet.config.store import config_path, scrub_secrets

    clean = scrub_secrets(config or {})
    parts = []
    for section, key in CONFIG_SUMMARY_KEYS:
        block = clean.get(section)
        value = block.get(key) if isinstance(block, dict) else None
        parts.append(f"{section}.{key}={'未设' if value is None else value}")
    return f"读自 {config_path()} · " + " ".join(parts)


def _llm(config: dict) -> str:
    """对话 HTTP 那一路的只读判据：端点归族 + 模型名 + 密钥在场与否（**永不印值**）。

    `api_base` 原文不印：有人把 token 写在查询串里，打出来就是外送。产品侧
    `llm_endpoint_family()` 已经把"连的是哪一家"归成族名，够用且不携密。

    这格**只覆盖 direct/HTTP 那一路**。agent 链接那一路的凭据在 `meapet/agent/factory.py:20`
    的私有 `_resolve_secret` 加三张各不相同的 env 表里，doctor 复制它就是第二套真值——
    所以读数里明写范围，别让"key=缺席"被读成"这台机器没配任何凭据"。
    """
    from meapet.config.store import llm_endpoint_family, resolve_direct_api_key

    llm = config.get("llm") if isinstance(config.get("llm"), dict) else {}
    direct = llm.get("direct") if isinstance(llm.get("direct"), dict) else {}
    agent = llm.get("agent") if isinstance(llm.get("agent"), dict) else {}
    family = llm_endpoint_family(llm) or "未识别"
    model = str(llm.get("model") or direct.get("model") or "").strip() or "未设"
    in_file = any(
        str(block.get("api_key") or "").strip() for block in (llm, direct, agent)
    )
    resolved = bool(resolve_direct_api_key(llm))
    source = "配置" if in_file else ("环境变量" if resolved else "无")
    return (
        f"family={family} model={model} "
        f"direct/HTTP key={'在场' if resolved else '缺席'}(来源={source})"
    )


def _asr(config: dict) -> str:
    """语音输入的模型在场：按**两档**各问一次产品侧那一个真值。

    分档报是因为产品自己那张表就写着这一怕（`meapet/paths.py` 注释：「下了 fp32 却仍读
    int8」）。只报配置那一档的话，这种坏法读数上看起来"模型没下载"，实际是下错了档。
    """
    from meapet.paths import (
        VOICE_ASR_MODEL_FILES,
        find_voice_asr_model_dir,
        normalize_voice_asr_precision,
        project_path,
        voice_asr_cache_dir,
    )

    vi = config.get("voice_input") if isinstance(config.get("voice_input"), dict) else {}
    chosen = normalize_voice_asr_precision(vi.get("precision"))
    roots = f"{voice_asr_cache_dir()} 与 {project_path('models', 'voice_asr')}"
    parts = []
    for precision in VOICE_ASR_MODEL_FILES:
        found = find_voice_asr_model_dir(precision)
        tag = "配置档" if precision == chosen else "另一档"
        parts.append(f"{precision}({tag})={'在场' if found else '缺席'}")
    return (
        f"enabled={vi.get('enabled')} 配置档={chosen} "
        + " ".join(parts)
        + f" · 找过={roots}"
    )


def _display_env() -> str:
    def _flag(name: str) -> str:
        value = os.environ.get(name)
        return value if value else "缺席"

    return (
        f"QT_QPA_PLATFORM={_flag('QT_QPA_PLATFORM')} · "
        f"WAYLAND_DISPLAY={_flag('WAYLAND_DISPLAY')} · "
        f"DISPLAY={_flag('DISPLAY')} · "
        f"XDG_SESSION_TYPE={_flag('XDG_SESSION_TYPE')}"
    )


def _backend() -> str:
    from meapet.desktop.click_through import platform_backend_name

    return f"platform_backend_name={platform_backend_name()}"


def _layer() -> str:
    from meapet.desktop import wayland_layer

    candidates = list(wayland_layer.shim_candidates())
    present = [path for path in candidates if os.path.exists(path)]
    available = wayland_layer.is_available()
    backend = wayland_layer.get_backend()
    last_error = backend.last_error() if backend is not None else "无后端对象"
    return (
        f"候选 {len(candidates)} 条 / 文件在场 {len(present)} 条 {present[:2]} · "
        f"is_available={available} · last_error={last_error}"
    )


def _fidus() -> str:
    from meapet.desktop.fidus_position import have_engine

    return f"have_engine={have_engine()}"


def _qt_app():
    """建（或复用）一个 Qt 应用对象——屏幕几何与输入阈值那两格共用同一份引导。

    这一句是全入口唯一会碰显示服务的地方，所以它只在 `--with-gui` 后面走：无显示服务时
    建 Qt 应用**不是抛异常，是 abort**，`_probe` 那道兜底接不住。
    """
    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtGui import QGuiApplication

    if QCoreApplication.instance() is None:
        QCoreApplication([])
    return QGuiApplication.instance() or QGuiApplication(["meapet-doctor"])


def _screens(with_gui: bool) -> str:
    if not with_gui:
        return ("未取（默认不建 Qt 应用：无显示服务时那一下是 abort，不是异常）。"
                "要这份读数就加 --with-gui")
    from meapet.desktop.screen_geometry import available_geometry_for

    app = _qt_app()
    lines = [f"Qt platformName={app.platformName()}"]
    for screen in app.screens():
        geo = screen.geometry()
        lines.append(
            f"  屏幕 {screen.name()}: {geo.width()}x{geo.height()} @({geo.x()},{geo.y()}) "
            f"ratio={screen.devicePixelRatio()}"
        )
    usable = available_geometry_for(None)
    lines.append(
        f"  可用区={None if usable is None else (usable.width(), usable.height(), usable.x(), usable.y())}"
    )
    return " | ".join(lines)


def _input_thresholds(with_gui: bool) -> str:
    """点击链上那两道**门槛**的数：Qt 系统给的和产品自己写死的，并排印出来。

    分成两组是有原因的：「点一下没反应」有两种完全不同的坏法——拖窗标志先立起
    （`startDragDistance` 通常远小于分区门票那 35 px），或者双击的第二次 release 在去抖窗口内
    就已经把反馈发出去了（去抖窗口要跟的是**系统值**，不是提案里那个 300 ms 参考值）。
    读数不带这两组数就分不出是哪种，所以两边各印各的，不合成一个"看起来正常"。
    """
    if not with_gui:
        return "未取（要建 Qt 应用，与 screens 同档：加 --with-gui）"

    from meapet.desktop.live2d_widget import (
        ZONE_CLICK_MAX_DISTANCE_PX,
        ZONE_CLICK_MAX_HOLD_SECONDS,
    )

    app = _qt_app()
    hints = app.styleHints()
    return (
        f"Qt platformName={app.platformName()} "
        f"doubleClickInterval={hints.mouseDoubleClickInterval()}ms "
        f"startDragDistance={hints.startDragDistance()}px · "
        f"产品门票={ZONE_CLICK_MAX_DISTANCE_PX}px/{ZONE_CLICK_MAX_HOLD_SECONDS}s"
        "（那两个数读自 live2d_widget 的名字，不是这里抄的）"
    )


def _audio_import() -> str:
    import importlib.util

    return f"pyaudio 规格={'在场' if importlib.util.find_spec('pyaudio') else '缺席'}"


def _input_devices(config: dict) -> str:
    from meapet.voice.engine import VOICE_INPUT_SAMPLE_RATE, list_input_devices

    cfg = (config or {}).get("voice_input") or {}
    devices = list_input_devices(VOICE_INPUT_SAMPLE_RATE)
    return (
        f"指定设备={cfg.get('input_device_index')} 精度={cfg.get('precision')} "
        f"采样率={VOICE_INPUT_SAMPLE_RATE} · 枚举到 {len(devices)} 台"
        f"（可用 {sum(1 for row in devices if row[3])} 台）"
    )


def _tts(config: dict, engine: str) -> str:
    from meapet.tts.diagnostics import speech_facts, tts_for_engine

    started = time.monotonic()
    tts = tts_for_engine(config, engine)
    health = bool(tts.health_check())
    return (
        f"enabled={getattr(tts, 'enabled', None)} engine={getattr(tts, 'engine', None)} "
        f"前置={'齐' if health else '不齐'} 本项耗时={time.monotonic() - started:.2f}s · "
        f"{speech_facts(engine, tts)}"
    )


def _tts_latency(config: dict, engine: str) -> str:
    from meapet.tts.diagnostics import speech_latency, tts_for_engine

    return speech_latency(engine, tts_for_engine(config, engine))


#: `--probe-agent` 那一发的总预算（秒）。三家适配器自己的 `timeout_seconds` 是内层闸，
#: 这一道是**外层**的：无头取证里任何一格都不该被一个挂住的端点摁住——挂住的读数和"坏"的
#: 读数在调用方那边长得一模一样，那就等于本入口最想说的那句话（什么算坏）没了。
AGENT_PROBE_BUDGET_SECONDS = 8.0


def _is_loopback(host: str) -> bool:
    """这台端点是不是**回路**：只有回路的才允许真连。

    `localhost` 按名字放行（三家出厂地址都是 `127.0.0.1`）；其余一律不连——doctor 的授权里
    没有"替这台机器往外发请求"这一项，新增外联要人工明确点头。
    """
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _capabilities_summary(caps: object) -> str:
    """能力对象里挑几枚**不携密**的键印出来；三家字段名不同，所以按在场拼，不猜结构。"""
    safe_keys = (
        "platform",
        "protocol",
        "protocol_version",
        "agent_name",
        "server_version",
        "streaming",
    )
    parts = [f"{key}={getattr(caps, key)}" for key in safe_keys if getattr(caps, key, None) is not None]
    return f"{type(caps).__name__} " + " ".join(parts)


async def _probe_and_close(adapter) -> object:
    try:
        return await asyncio.wait_for(adapter.probe(), timeout=AGENT_PROBE_BUDGET_SECONDS)
    finally:
        await adapter.close()


def _port_open(host: str, port: int, budget: float = 2.0) -> bool:
    """那个端口在不在听——**这一道是给 WebSocket 那一发当地闸的**。

    `hermes.py:349` 的 `_ensure_connected` 第一件事是查 token，没配凭据时抛的是
    `ValueError("Hermes WebSocket token is required…")`，而 `probe()` 末尾那个
    `except Exception` 把它归进「握手失败」那一句——于是"这台机器没配 agent 凭据"和
    "gateway 挂了"在读数里长得一模一样。端口先问一句就把这两支分开了：没在听 = 真不在场，
    在听而 probe 抛 = 凭据或协议那一档的坏。
    """
    import socket

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    for family, socktype, proto, _, address in infos:
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(budget)
            if sock.connect_ex(address) == 0:
                return True
    return False


DEFAULT_PORTS = {"ws": 80, "wss": 443, "http": 80, "https": 443}


def _endpoint_port(url: str) -> int | None:
    """地址里那个端口——**没写出来时按 scheme 兜**，不在这里猜出厂默认端口。

    出厂地址（`config/defaults.py:17`）自带端口，所以绝大多数情况走的是 `split.port`；
    兜底那三行只为"用户自己填了个不带端口的 ws://localhost"这种形状兜着，宁可报
    "按 ws→80 试的"也不假装知道他要连的是哪一格。
    """
    split = urlsplit(url)
    try:
        port = split.port
    except ValueError:
        return None  # 端口那一栏写坏了（字母、超范围）
    if port is not None:
        return port
    return DEFAULT_PORTS.get((split.scheme or "").lower())


def _failure_chain(exc: BaseException) -> str:
    """把 `raise X from Y` 那条链读全——**产品侧把真因藏在 `__cause__` 里**。

    `hermes.py:596` 是 `raise ValueError("…握手失败") from exc`：外层那句永远一样，
    而底下那层才是"没配 token"还是"对端拒连"。读数只印外层就等于把两支又捏回一支，
    所以这里顺着 `__cause__/__context__` 再走一层（只一层，链再长就截断，防环与刷屏）。
    """
    parts = [f"{type(exc).__name__}: {exc}"]
    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        parts.append(f"← {type(cause).__name__}: {str(cause)[:180]}")
    return " ".join(parts)


def _agent_channel(config: dict) -> str:
    """agent 链接那一路的在场性——**复用产品侧工厂**，不在这里重算凭据、地址或 kind 默认值。

    凭据解析在 `meapet/agent/factory.py` 的私有 `_resolve_secret` 加三张各不相同的 env 表里，
    doctor 抄它就是第二套真值（`llm` 那格的判据里已经写过这一怕）。所以这一格走的是
    `create_agent_adapter_from_config()` + `probe()`：产品怎么连，我就怎么连。
    """
    from meapet.agent.factory import create_agent_adapter_from_config

    # 工厂会往传进去的那份 dict 里塞 session_id/device_id（`_ensure_local_session`），
    # 而 doctor 的只读承诺是对着调用方那份 config 立的——喂它一份副本。
    probe_config = copy.deepcopy(config or {})
    configured = ((probe_config.get("llm") or {}).get("agent") or {}).get("kind")
    kind = f"配置={configured if configured not in (None, '') else '未设(工厂按 hermes)'}"

    try:
        adapter = create_agent_adapter_from_config(probe_config)
    except Exception as exc:  # noqa: BLE001 - 构造坏也是读数，不是 traceback
        return f"{kind} 适配器构造失败 {type(exc).__name__}: {exc}"

    base_url = str(getattr(adapter.config, "base_url", "") or "")
    split = urlsplit(base_url)
    host = split.hostname or ""
    port = _endpoint_port(base_url)
    label = (
        f"{kind} adapter={type(adapter).__name__} "
        f"host={host or '解析不出'} port={port if port is not None else '解析不出'}"
    )
    if not _is_loopback(host):
        return (
            f"{label} · **未连接**：端点不在回路上。"
            "doctor 不替这台机器往外发请求（新增外联须人工明确点头）"
        )
    if port is None:
        return f"{label} · 不在场：地址里取不出端口（未发 WebSocket）"

    # 先问 TCP，再决定要不要发那一发 WebSocket——未在听就不发。这一道闸把
    # "这台机器没配 agent 凭据"与"gateway 挂了"分开（见 `_port_open` 里那段），
    # 顺带省下对一个死端口做 WS 握手的整预算。
    if not _port_open(host, port):
        return f"{label} · 不在场：tcp={host}:{port} 未监听（未发 WebSocket）"

    try:
        caps = asyncio.run(_probe_and_close(adapter))
    except Exception as exc:  # noqa: BLE001 - 不在场是本案要的那条判据，不是异常
        return f"{label} · 不在场：{_failure_chain(exc)}（预算 {AGENT_PROBE_BUDGET_SECONDS}s）"
    return f"{label} · 在场：{_capabilities_summary(caps)}（{AGENT_PROBE_BUDGET_SECONDS}s 内握手完成）"


#: 内容源探针的逐请求超时（秒）。这一格存在的理由就是**覆盖**客户端那个默认值
#: （`http_async.py:29` 的 `Timeout(120.0, connect=10.0)`）——点击反馈那条链的口径是"逐请求秒级"，
#: 不显式压它，一次挂站的检索页就能把整份取证摁住两分钟。
SOURCE_PROBE_BUDGET_SECONDS = 3.0
SOURCE_SNIPPET_CHARS = 80


def _printable_url(url: str) -> str:
    """印出来用的地址：**查询串整条丢掉**——有人把 token 写在查询里（`llm` 那格同样的怕）。"""
    try:
        split = urlsplit(url)
        port = split.port
    except ValueError:
        return "<地址写法不合法>"
    host = split.hostname or ""
    netloc = f"{host}:{port}" if port is not None else host
    return f"{split.scheme}://{netloc}{split.path}"


def _visible_text(body: bytes, content_type: str) -> str:
    """从响应体里取**一段人能看的文字**——三档解析各走各的，取不到就如实说取不到。

    这一格判的是提案 §2 那两行的"可解析"那半条：状态 200 与一堆字节本身不说明取回了内容，
    所以要把首个可见片段印出来给人过目。失败不抛——"解析不出"就是那条源该被划掉的证据。
    """
    if not body:
        return "<空响应体>"
    lowered = (content_type or "").lower()
    try:
        if "json" in lowered:
            data = json.loads(body.decode("utf-8", errors="replace"))
            pairs = _first_strings(data)
            return " ".join(f"{key}={val}" for key, val in pairs) or "<JSON 里没有字符串字段>"
        if "html" in lowered or "xml" in lowered:
            from lxml import html as lxml_html

            tree = lxml_html.fromstring(body.decode("utf-8", errors="replace"))
            for dead in tree.xpath("//script | //style | //noscript"):
                dead.getparent().remove(dead)
            return " ".join(tree.text_content().split())
        return body.decode("utf-8", errors="replace").strip()
    except Exception as exc:  # noqa: BLE001 - 解析不出是读数，不是 traceback
        return f"<解析失败 {type(exc).__name__}: {str(exc)[:60]}>"


def _first_strings(data: object, limit: int = 3) -> list[tuple[str, str]]:
    """JSON 里前几枚字符串值，**带键名**——古诗源返的是结构，光有值认不出哪句是句。"""
    found: list[tuple[str, str]] = []

    def walk(node: object, key: str = "") -> None:
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            for name, value in node.items():
                walk(value, str(name))
        elif isinstance(node, list):
            for value in node:
                walk(value, key)
        elif isinstance(node, str) and node.strip():
            found.append((key or "?", " ".join(node.split())[:40]))

    walk(data)
    return found


async def _fetch_one(url: str) -> tuple:
    """一发 GET。走 `http_async` 那个共享客户端（TLS/UA/重定向都跟产品同源），
    但**逐请求把超时压到秒级**——默认那 120 s 是给长任务用的，不是给点击反馈用的。"""
    from meapet import http_async

    try:
        response = await http_async.get_json(url, timeout=SOURCE_PROBE_BUDGET_SECONDS)
        return response.status_code, response.headers.get("content-type", ""), response.content, ""
    except Exception as exc:  # noqa: BLE001 - 挂/拒/超时都是取证要的那条判据
        return None, "", b"", f"{type(exc).__name__}: {str(exc)[:120]}"
    finally:
        await http_async.aclose_client()


def _source_probe(url: str, allow_egress: bool) -> str:
    host = urlsplit(url).hostname or ""
    label = _printable_url(url)
    if not allow_egress and not _is_loopback(host):
        return (
            f"{label} · **未请求**：端点不在回路上。带 --allow-egress 才会真往外发，"
            "而新增外联须人工明确点头——这一格默认不发任何一个包"
        )
    started = time.perf_counter()
    status, content_type, body, error = asyncio.run(_fetch_one(url))
    elapsed = time.perf_counter() - started
    if error:
        return (
            f"{label} · 失败：{error}（{elapsed:.2f}s，逐请求预算 {SOURCE_PROBE_BUDGET_SECONDS}s）"
        )
    snippet = _visible_text(body, content_type)[:SOURCE_SNIPPET_CHARS]
    return (
        f"{label} · status={status} ct={content_type or '未声明'} bytes={len(body)} "
        f"耗时={elapsed:.2f}s · 首个可见文本=「{snippet}」"
    )


def _log_tail(lines: int = 5) -> str:
    from meapet.paths import data_path

    path = data_path("chat_errors.log")
    if not os.path.isfile(path):
        return f"{path} 不存在"
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        rows = handle.read().splitlines()[-lines:]
    return f"{os.path.basename(path)} 尾 {len(rows)} 行 :: " + " ⏎ ".join(rows)


def collect(config: dict, *, with_gui: bool = False, skip_tts: bool = False,
            skip_layer: bool = False, measure_speech: bool = False,
            probe_agent: bool = False, probe_sources: tuple[str, ...] = (),
            allow_egress: bool = False) -> list[Item]:
    """跑一遍探测，返回逐项读数（任何一项坏都会变成一行文本，不抛）。

    带 `measure_speech` / `probe_agent` / `probe_sources` 那三档时才跑会动手的格（起子进程落 wav /
    真开一次 WebSocket / 真发一次 GET）；默认三档都不动，一格不碰、一个包不发。
    `probe_sources` 只连回路，除非同时显式带 `allow_egress`（那是"新增外联"，须人工点头）。
    """
    items = [
        _probe(
            "runtime",
            "先看 frozen：True 是打包版、False 是源码态，后面每一格的解读都挂在这一格上",
            _runtime,
        ),
        _probe("paths", "读的是 meapet.paths 那一个真值，不是猜的 ~/.meapet", _dirs),
        _probe(
            "config",
            "只打白名单键（值是布尔/枚举/数字，本身不携密）；doctor 不写 config",
            lambda: _config_summary(config),
        ),
        _probe(
            "llm",
            "只覆盖 direct/HTTP 那一路：family 由产品侧 llm_endpoint_family() 归族，api_base 原文不印"
            "（有人把 token 写在查询串里），key 只报在场/缺席与来源；agent 链接那一路的凭据在 "
            "meapet/agent/factory.py 内部，doctor 不复制它，所以本格为缺席不代表整机无凭据",
            lambda: _llm(config),
        ),
        _probe(
            "display_env",
            "环境变量只是线索：XDG_SESSION_TYPE=wayland 而 Qt 用 xcb 时窗口其实在 XWayland 上",
            _display_env,
        ),
        _probe(
            "backend",
            "产品自己算出的后端；只有 wayland 那一档才有 layer-shell 这条路",
            _backend,
        ),
        _probe(
            "layer_shim",
            "文件不在=打包没带上（找 MeaPet.spec）；文件在场但 init 非 0=这台机器的环境或库问题（找 runtime）",
            _layer,
        ),
        _probe(
            "fidus",
            "只查模块规格不 import；不在场=那条分发渠道还没装上 wheel（随包分发是 standing 裁决），不是运行时故障",
            _fidus,
        ),
        _probe(
            "audio",
            "枚举设备要靠它；空表分不出「没声卡」与「pyaudio 没装」，所以这两格分开报",
            _audio_import,
        ),
        _probe(
            "input_devices",
            "pyaudio 在场却枚举到 0 台=这台机器真没有可用输入设备（或格式不通）",
            lambda: _input_devices(config),
        ),
        _probe(
            "asr",
            "两档各问一次是因为产品自己那张表就写着「下了 fp32 却仍读 int8」这一怕；"
            "配置档=缺席 而另一档=在场 ⇒ 不是没下载，是下错了档。模型路径与档位表都取自 meapet.paths 那一个真值",
            lambda: _asr(config),
        ),
        _probe(
            "screens",
            "屏幕几何只有建了 Qt 应用才取得到；platformName=offscreen 时这些数不是真桌面",
            lambda: _screens(with_gui),
        ),
        _probe(
            "input_thresholds",
            "去抖/分区那两道门槛的数：doubleClickInterval 是**系统给的**，提案里那个 300 ms 只是下限参考——"
            "固定值小于这一格的读数就拦不住双击；startDragDistance 若小于产品门票那 35 px，"
            "手抖一下这次点击就被当拖窗吃掉。platformName=offscreen 时读到的是 Qt 默认值，不是这台桌面的设定",
            lambda: _input_thresholds(with_gui),
        ),
        _probe(
            "log_tail",
            "落盘前已过产品侧 redact_text；这里再打一道是防着旧文件里那句没打码的",
            _log_tail,
        ),
    ]
    if skip_layer:
        items = [item for item in items if item.name != "layer_shim"]
    if not skip_tts:
        # 两路各探一次：engine 那格在配置里只能是一个值，而两路的坏法完全不同
        # （GSV 坏在解释器与依赖，VITS 坏在权重水化），合起来看只会互相遮蔽。
        for engine in ("gpt_sovits", "vits"):
            items.append(
                _probe(
                    f"tts.{engine}",
                    "前置=不齐 时朗读必然不出声；缺哪一格看事实串（解释器、依赖、权重是否还是 LFS 指针）",
                    lambda eng=engine: _tts(config, eng),
                )
            )
        if measure_speech:
            # 延迟那一档跟在两格前置之后：前置不齐时 `speech_latency` 自己写「未测」，
            # 不会把一发注定失败的读数当成延迟报出去。它排在最后是因为最慢——
            # 前面任何一格炸了都不该先付两分钟的合成钱。
            for engine in ("gpt_sovits", "vits"):
                items.append(
                    _probe(
                        f"tts.latency.{engine}",
                        "本入口唯一的写侧：真起推理子进程并往 tts.output_dir 落 wav（不播放，所以不出声）。"
                        "每发都含模型加载（两引擎都是每句新起进程），第二发暖的只是页缓存；"
                        "秒数必须与同一串里的配置一起读，换个选路/权重/CPU 或 GPU 这数就翻几倍",
                        lambda eng=engine: _tts_latency(config, eng),
                    )
                )
    if probe_agent:
        items.append(
            _probe(
                "agent_channel",
                "真连一次 WebSocket，**只连回路**（127.0.0.1/::1/localhost）：非回路端点这一格报"
                "「未连接」而不是去连——替这台机器往外发请求不在 doctor 的授权里。连之前先问 TCP："
                "端口没在听就报 `tcp=… 未监听` 且**不发那一发**，这样「这台机器没配 agent 凭据」"
                "与「gateway 挂了」不会同形（产品侧 `hermes.py:596` 把两者都归成「握手失败」，"
                "真因只在 `__cause__` 里，所以那一发跑了之后这里也顺着把真因印出来）。"
                "凭据与地址都走产品侧 factory，doctor 不复制一份；不在场是本案回退链的**预期输入**，"
                "它不等于朗读/点击反馈坏了",
                lambda: _agent_channel(config),
            )
        )
    for index, url in enumerate(probe_sources, start=1):
        items.append(
            _probe(
                f"source.{index}",
                "点击反馈那两个新内容源（关键词检索 / 古诗 API）的**设施取证**：逐请求超时压到 "
                f"{SOURCE_PROBE_BUDGET_SECONDS}s（不压就吃 `http_async.py:29` 那个 120s 默认），"
                "只印状态/字节/首个可见文本片段——200 与一堆字节不说明取回了内容，片段给人过目才说明。"
                "查询串不进读数（有人把 token 写在那儿）。**默认只连回路**，非回路要显式 --allow-egress，"
                "那一步是新增外联、须人工点头；真站候选清单未定时这条格跑不了，不猜 URL",
                lambda u=url, eg=allow_egress: _source_probe(u, eg),
            )
        )
    return items


def render_text(items: list[Item], elapsed: float) -> str:
    width = max(len(item.name) for item in items) if items else 0
    lines = [f"meapet --doctor · 只读快照 · 共 {len(items)} 项"]
    lines += [f"{item.name:<{width}}  {item.value}｜判据：{item.judge}" for item in items]
    lines.append(f"总耗时 {elapsed:.2f}s（0=探测跑完，不代表一切正常）")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    from meapet.config.store import load_config

    # 产品侧那些函数里有直接 print 的（`wayland_layer.is_available()` 就是）。
    # doctor 的 stdout 要能整块贴回来或喂进 JSON，所以取证期间的 print 一律串到 stderr。
    started = time.monotonic()
    try:
        config = load_config(args.config)
    except Exception as exc:
        print(f"[doctor] config 读不出来，取证无从谈起: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    with contextlib.redirect_stdout(sys.stderr):
        items = collect(
            config, with_gui=args.with_gui, skip_tts=args.skip_tts, skip_layer=args.skip_layer,
            measure_speech=args.speech_latency, probe_agent=args.probe_agent,
            probe_sources=tuple(args.probe_source), allow_egress=args.allow_egress,
        )
    elapsed = time.monotonic() - started
    if args.doctor_format == "json":
        print(json.dumps(
            {"elapsed_s": round(elapsed, 2), "items": [asdict(item) for item in items]},
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(render_text(items, elapsed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
