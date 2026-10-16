#!/usr/bin/env python3
"""L1 读数：产品自己的 Live2D 控件在 live2d-py **两代** API 上各跑一遍。

Subject under test: `meapet.live2d_api` 解析出的那四格名字，被 `live2d_widget.initializeGL` 与
`_update_look_target` 真用出去之后——产品的控件能不能加载模型、写进参数、把帧换出来。

两臂只差一个 `PYTHONPATH`（同一份脚本、同一个仓库、同一个解释器）：

    .venv/bin/python scripts/live2d_generation_probe.py
    PYTHONPATH=~/.cache/live2d100-probe/venv/lib/python3.12/site-packages \\
        .venv/bin/python scripts/live2d_generation_probe.py

它判的一件事（人工 2026-10-16「我建议迁」）：迁移之后**装哪一代都能起来**。注意这里跑的是
产品那个 `Live2DWidget`，不是脚本自己镜像一套调用——镜像探针只能证明脚本会调 API。

判不了的（口径红线，别把"起来了"读成"没问题"）：
- 离屏 QPA 下 `grabFramebuffer()` 的 alpha 恒 255 ⇒ 透明度与边缘质量这一格量不到（H 系列已实测）。
- 跨版本的**像素等价**不由本脚本给：产品的 `paintGL` 每帧都调 `Update()`，而 `Update()` 是墙钟/
  物理驱动的，动画相位不可复现。那份读数在
  `~/.Athena/projects/meapet/reference/live2d-py-1.0.0-20261016/readouts.md` §四（静态帧，无 Update）。
- 合成器（niri）给不给窗口尺寸，offscreen 判不了。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import QCoreApplication  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from meapet import live2d_api  # noqa: E402


def read_param(model, api, pid):
    """按本机那一代读一个参数，返回 (值, 怎么读的)。

    实测的不对称：1.0.0 **新增**了按 id 直接读的 `GetParamValueById`，而 0.7 的 `LAppModel`
    只有按下标的 `GetParameterValue`（传 id 会 `TypeError: argument 1 must be int`），想按 id
    读得先 `GetParamIds()` 找下标——列 id 的名字在 `LAppModel` 上是 `GetParamIds`，
    `GetParameterIds` 只存在于底层的 `live2d.v3.Model`，在加载好的模型上调它会 AttributeError。
    这份对照只活在探针里——产品侧不读参数。
    """
    if api.model_class == "Model":
        return model.GetParamValueById(pid), "GetParamValueById(id)"
    ids = list(model.GetParamIds())
    if pid not in ids:
        return None, "id 不在参数表里"
    index = ids.index(pid)
    return model.GetParameterValue(index), f"GetParameterValue(下标 {index})"


def report(name, value):
    print(f"[{name}] {value}", flush=True)


def main() -> int:
    api = live2d_api.LIVE2D_API
    report("包", f"live2d-py {live2d_api.LIVE2D_VERSION} / namespace={api and api.namespace}")
    report("门票", f"LIVE2D_AVAILABLE={live2d_api.LIVE2D_AVAILABLE} "
                   f"missing={live2d_api.LIVE2D_MISSING_KEY}")
    if api is None:
        report("原因", live2d_api.LIVE2D_UNAVAILABLE_REASON)
        return 1
    report("解析", f"类={api.model_class} 写参={api.param_setter} "
                   f"眨眼={api.blink_setter} 呼吸={api.breath_setter}")

    from meapet.config.store import resolve_resource_path
    from meapet.desktop.live2d_widget import Live2DModel, init_live2d

    app = QApplication(sys.argv[:1])
    init_live2d()
    cfg = json.loads(Path(ROOT, "config.json").read_text(encoding="utf-8"))
    model_dir = resolve_resource_path(cfg["live2d"]["model_dir"])

    l2d = Live2DModel(model_dir)
    widget = l2d.create_widget()
    widget.resize(366, 512)
    failed = []
    widget.initialization_failed.connect(lambda reason: failed.append(reason))
    widget.show()

    pumped = 0
    while pumped < 400 and l2d.model is None and not failed:
        QCoreApplication.processEvents()
        pumped += 1

    report("起帧", f"pump={pumped} model={l2d.model is not None} "
                   f"ready={widget._ready} 初始化失败={failed or '无'}")
    if failed or l2d.model is None:
        return 2

    report("构造出的类", f"{type(l2d.model).__module__}.{type(l2d.model).__name__}")
    report("眨眼/呼吸", f"initializeGL 里跑过 {api.blink_setter}/{api.breath_setter} 且没抛"
                        "（抛了会落进 initialization_failed，上面已经 return）")

    # 产品自己的写参数路：_update_look_target 用解析出来的名字写四个参数。
    widget._update_look_target()
    value, how = read_param(l2d.model, api, "ParamAngleX")
    in_range = isinstance(value, (int, float)) and -30.0 <= value <= 30.0
    report("参数往返", f"{how} = {value!r} ⇒ "
                       f"{'落在产品那发 cx*30 的区间内' if in_range else '越界或读不到，写没写进去判不了'}"
                       f"（写它的名字是 {api.param_setter}）")

    widget._timer.start(16)
    for _ in range(120):
        QCoreApplication.processEvents()
    report("换帧计数", f"_dbg_frame={getattr(widget, '_dbg_frame', 0)} frame_drawn={widget._frame_drawn}")
    report("初始化失败(收尾)", failed or "无")
    widget.shutdown()
    sys.stdout.flush()
    # 两代在本脚本的 Qt/解释器拆除阶段都会 rc=139（同一处、与版本无关），是 offscreen 探针自身的
    # artefact。这里硬退出，免得每次跑都在 systemd-coredump 里堆一份约 20MB 的核心转储。
    os._exit(0 if not failed else 2)


if __name__ == "__main__":
    sys.exit(main())
