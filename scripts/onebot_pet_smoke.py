"""真实启动桌宠（离屏）做 OneBot 接线的冒烟验证。

为什么需要它：``PetOneBotMixin`` 接在 8 个 mixin 的 MRO 里、初始化走 ``_safe("onebot", ...)``，
单元测试只能逐点验证，**整机装起来能不能跑**只有真的构造一次 ``MeaPet`` 才知道。

它做三件事：

1. 走真实构造路径 ``MeaPet(config_path)``，确认 OneBot 服务真的起来了（配置里启用时）；
2. 按间隔打印通道状态（监听中/上游连接数/hub 消费者数/事件计数）；
3. 用**真实退出路径** ``pet._quit()`` 收尾，确认 ``_stop_onebot`` 被调用且引用被清空。

退出码：0 = 符合预期；1 = 配置启用了 OneBot 但服务没起来。

用法：

    python scripts/onebot_pet_smoke.py --seconds 45
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MeaPet OneBot 接线冒烟")
    parser.add_argument("--seconds", type=float, default=45.0,
                        help="启动后运行多久再走真实退出路径")
    parser.add_argument("--report-every", type=float, default=10.0,
                        help="每隔多少秒打印一次通道状态；0 = 只在结束时打印")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    os.chdir(ROOT)
    # 必须在导入 PyQt5 之前设置，保证无显示器环境也能跑
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PyQt5.QtCore import QTimer
    from PyQt5.QtWidgets import QApplication

    def log(msg: str) -> None:
        print(f"[smoke] {msg}", flush=True)

    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)

    from meapet.config.store import load_config
    from meapet.desktop.app import MeaPet

    config = load_config(args.config)
    wants_onebot = bool((config.get("onebot") or {}).get("enabled"))
    log(f"配置：onebot.enabled={wants_onebot} reply_mode={(config.get('onebot') or {}).get('reply_mode')}")

    pet = MeaPet(args.config)
    failures: list[str] = []

    has_mixin = any("OneBot" in cls.__name__ for cls in type(pet).__mro__)
    log(f"MRO 含 OneBot mixin：{has_mixin}")
    if not has_mixin:
        failures.append("MRO 里没有 PetOneBotMixin")

    service = getattr(pet, "_onebot_service", None)
    log(f"服务已创建：{service is not None}")
    if wants_onebot and service is None:
        failures.append("配置启用了 onebot，但 _init_onebot 没有建起服务（看日志里的 [init] onebot）")

    def report(tag: str) -> None:
        svc = getattr(pet, "_onebot_service", None)
        if svc is None:
            log(f"[{tag}] service=no")
            return
        log(f"[{tag}] status={pet._onebot_status_text()}")
        log(f"[{tag}] upstream={len(svc.upstream_sessions)} hub={len(svc.hub_sessions)} "
            f"pending={svc.pending_count} stats={svc.stats}")

    if service is not None:
        report("boot")
        if args.report_every > 0:
            elapsed = args.report_every
            while elapsed < args.seconds:
                QTimer.singleShot(int(elapsed * 1000),
                                  lambda t=f"{elapsed:.0f}s": report(t))
                elapsed += args.report_every

    def finish() -> None:
        log("走真实退出路径 pet._quit()")
        pet._quit()

    QTimer.singleShot(int(args.seconds * 1000), finish)
    app.exec_()

    log(f"退出后 _onebot_service={getattr(pet, '_onebot_service', 'attr-missing')}")
    if getattr(pet, "_onebot_service", None) is not None:
        failures.append("_quit 之后 _onebot_service 没有被清空（_stop_onebot 可能没跑到）")

    if failures:
        for item in failures:
            log(f"FAIL {item}")
        return 1
    log("PASS OneBot 桌面接线正常：启动、运行、优雅退出都符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
