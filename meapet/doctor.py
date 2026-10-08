"""`meapet --doctor` —— 不进 GUI 的**只读**取证快照。

人工 2026-10-08 放行这句：「无头模式是给AI用的」「目的是测试，不论是否打包」。三条口径写在这里，
别处不复述：
- **只读**：不写 config、不装东西、不挂载 surface、不建窗口。唯一的外设是读日志尾部与只读的
  import/模块规格探测。（`--with-gui` 会建一个 Qt 应用对象去取屏幕几何——那是显式 opt-in，
  默认关着：无显示服务时建 Qt 应用不是抛异常，是 abort，catch 不到。）
- **每一项后面直接跟判据**（这条在说什么 / 什么算坏）。否则"这台机器没有 layer-shell"和
  "我们的桥接层没编进包"会被读成同一句话——那正是本入口要消灭的形状。
- **探测项一律调产品侧那一个函数**，这里不重算。重算就是第二套真值，doctor 会开始自证。

退出码：0 = 探测跑完了（**读数坏不坏不影响这个数**，坏法要出现在文本里）；
1 = doctor 自己没跑起来（那种时候没有任何读数，不能与"跑完但一切正常"共用 0）。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass

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


def _screens(with_gui: bool) -> str:
    if not with_gui:
        return ("未取（默认不建 Qt 应用：无显示服务时那一下是 abort，不是异常）。"
                "要这份读数就加 --with-gui")
    from PyQt5.QtCore import QCoreApplication
    from PyQt5.QtGui import QGuiApplication

    from meapet.desktop.screen_geometry import available_geometry_for

    if QCoreApplication.instance() is None:
        QCoreApplication([])
    app = QGuiApplication.instance() or QGuiApplication(["meapet-doctor"])
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


def _log_tail(lines: int = 5) -> str:
    from meapet.paths import data_path

    path = data_path("chat_errors.log")
    if not os.path.isfile(path):
        return f"{path} 不存在"
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        rows = handle.read().splitlines()[-lines:]
    return f"{os.path.basename(path)} 尾 {len(rows)} 行 :: " + " ⏎ ".join(rows)


def collect(config: dict, *, with_gui: bool = False, skip_tts: bool = False,
            skip_layer: bool = False) -> list[Item]:
    """跑一遍全部探测，返回逐项读数（任何一项坏都会变成一行文本，不抛）。"""
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
            "screens",
            "屏幕几何只有建了 Qt 应用才取得到；platformName=offscreen 时这些数不是真桌面",
            lambda: _screens(with_gui),
        ),
        _probe(
            "log_tail",
            "落盘前已过产品侧 redact_text；这里再打一道是防着旧文件里那句没打码的",
            _log_tail,
        ),
    ]
    if not skip_layer:
        items.insert(
            5,
            _probe(
                "layer_shim",
                "文件不在=打包没带上（找 MeaPet.spec）；文件在场但 init 非 0=这台机器的环境或库问题（找 runtime）",
                _layer,
            ),
        )
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
            config, with_gui=args.with_gui, skip_tts=args.skip_tts, skip_layer=args.skip_layer
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
