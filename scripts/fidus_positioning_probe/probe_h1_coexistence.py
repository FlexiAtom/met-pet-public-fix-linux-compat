#!/usr/bin/env python3
"""H1 共存冒烟：一个进程里同时跑 **Qt + 桥接层 + fidus wheel** 三条 Wayland 连接。

出处：fidus 侧跨项目提案 `~/.Athena/projects/meapet/pool/fidus-wheel-qt-coexistence.md` §2。
它把"进程内嵌 wheel 是否成立"的**唯一硬门槛**（H1）移交给唯一能执行它的一方：meapet
（fidus 没有 meapet 的 Qt 宿主）。本脚本就是该提案要求的那个最小原型。

拓扑（与真桌宠同构，三条连接各自独立，互不借 `wl_display`）
----------------------------------------------------------
* #1 **Qt**（`QT_QPA_PLATFORM=wayland`）——桌宠宿主自己的连接 + 事件循环。
* #2 **`liblayer_shell_shim.so`**——meapet 点击穿透桥接，自持连接 + 自建泵线程
  （不变量见 `wayland_layer.py` 头注："一条 Wayland 连接只能有一个读取者"）。
* #3 **fidus wheel**——`Fidus.build_wayland()` 内部 `Connection::connect_to_env()`。

判据（三条，各自独立出 VERDICT 行，不合并）
------------------------------------------
* `H1-COEXIST`：三连接同进程并存且各自存活——连接数由**对端 inode ∈ niri 的 fd 表**
  确定性认定（不是"fd 变多了"这种代理判据）；桥接层 `layer_last_error()` 仍为空；
  fidus 能截屏（`build_wayland` 的分类器要 4 次整幅截屏）。
* `UI-RESPONSIVE`：fidus 的阻塞调用（`py.detach` 释放 GIL）**不冻结** Qt 主循环——
  主线程 tick 计数在 fidus 阶段持续推进。
* `CLOSED-LOOP`：fidus 从**合成后的屏幕**里找到由**桥接层显示**的图案 ⇒ 三条连接不只
  是没互杀，而是经合成器完成了互操作。这是比"连接数"强得多的共存证据。

不能证明什么（不静默）
--------------------
* **精度 / 帧率 / 长稳 / A10**：本冒烟只判"能不能"，不判"准不准"。图案由本脚本自己
  合成并原样登记（全不透明，保证合成后像素≈模板像素），`set_position` 位移差只作为
  **观察值**打印，不构成几何正确性判据（那是定位线 §2 既有的独立待测项）。
* 结论仅在**本机 niri + Qt5 + 该桥接 + 该 wheel** 的拓扑成立（L3，不可外推）。

跑法
----
    WAYLAND_DISPLAY=wayland-1 .venv/bin/python \
        scripts/fidus_positioning_probe/probe_h1_coexistence.py [--stage-a-only] \
        [--pattern-xy X,Y] [--verbose-wayland]

副作用（跑之前知道）：会在**当前真实桌面**上贴一个不透明图案层 + 一个小 Qt 窗口，
并在 `--no-stage-a-only` 时让 fidus 在屏幕上投射标定标记、做整幅截屏数秒。结束自行清理。
退出码：0 = H1 成立；3 = 实验跑完但 H1 不成立（否定结果，同样是结论）；1 = 探针/环境自身不可用。
"""
from __future__ import annotations

import argparse
import ctypes
import importlib.metadata
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Qt 平台必须在 import PyQt5 之前定死，否则离屏会话会静默退化出一条"假 Qt 连接"，
# 那样测出来的就不是三连接拓扑（正是提案 §2 反证表里点名的代理错位）。
os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

from PyQt5.QtCore import QPoint, Qt, QTimer  # noqa: E402
from PyQt5.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

try:
    import numpy as np
except Exception as exc:  # pragma: no cover
    print(f"[harness] ✗ numpy 不可用: {exc}", flush=True)
    sys.exit(1)

try:
    import fidus as fidus_mod
except Exception as exc:  # pragma: no cover
    print(f"[harness] ✗ fidus wheel 不可导入: {type(exc).__name__}: {exc}", flush=True)
    sys.exit(1)


def _load_facade():
    """按文件路径直载门面，绕开 `meapet.desktop.__init__` 的 import 链。

    桥接门面是本探针的**被测对象之一**而不是被测系统的一部分，把整个 desktop 包拖进来
    会让"探针失败"和"桌宠某个无关 import 失败"混在一起。
    """
    path = REPO_ROOT / "meapet" / "desktop" / "wayland_layer.py"
    spec = importlib.util.spec_from_file_location("wl_facade_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FACADE = _load_facade()

PATTERN_W = 200
PATTERN_H = 200
MOVE_DX, MOVE_DY = 180, -120


# ---------------------------------------------------------------- 连接计数
def _compositor_socket_inodes() -> frozenset[int]:
    """合成器进程（niri）当前持有的全部 socket inode。

    **不可缓存**：客户端每接入一次，合成器侧就新建一条 accepted socket。缓存在基线时刻
    的快照会让 Qt/桥接/fidus 之后建立的连接的对端 inode 永远查不到，连接数恒为 0
    （本探针第一版就是这么错的）。
    """
    inodes: set[int] = set()
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:
            continue
        if comm != "niri":
            continue
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                tgt = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if tgt.startswith("socket:["):
                inodes.add(int(tgt[8:-1]))
    return frozenset(inodes)


def compositor_connections() -> list[tuple[int, int]]:
    """本进程 → 合成器的 Wayland 连接：[(fd, inode), ...]。

    判据链：`/proc/self/fd` 给 inode ↔ fd；`ss -x` 给该 inode 的**对端** inode；
    对端落在 niri 的 fd 表里才算一条合成器连接。绕开"unix socket 变多了"这类代理计数
    （dbus、Qt 的其他 socket 也在里面）。
    """
    inode_to_fd: dict[int, int] = {}
    for fd in os.listdir("/proc/self/fd"):
        try:
            tgt = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            continue
        if tgt.startswith("socket:["):
            inode_to_fd[int(tgt[8:-1])] = int(fd)
    mine = os.getpid()
    peer_of: dict[int, int] = {}
    try:
        out = subprocess.run(
            ["ss", "-x", "-p"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception as exc:
        print(f"[probe] ⚠ ss 不可用（连接数退化为 fd 计数）: {exc}", flush=True)
        return sorted((fd, ino) for ino, fd in inode_to_fd.items())
    for line in out.splitlines()[1:]:
        if f"pid={mine}," not in line:
            continue
        m = re.search(r"\*\s+(\d+)\s+\*\s+(\d+)", line)
        if m:
            peer_of[int(m.group(1))] = int(m.group(2))
    comps = _compositor_socket_inodes()
    hits = []
    for ino, peer in peer_of.items():
        if peer in comps and ino in inode_to_fd:
            hits.append((inode_to_fd[ino], ino))
    return sorted(set(hits))


def fact(label: str, value) -> None:
    print(f"  {label}: {value}", flush=True)


# ---------------------------------------------------------------- 图案
def make_pattern() -> "np.ndarray":
    """合成一个高区分度的不透明 RGBA 模板（uint8, C-contiguous, (h,w,4)）。

    全不透明是硬要求：桥接层提交给合成器的 alpha 若 <255 会与实际屏幕像素不等，
    "找不到目标"就会被误读成共存失败（把渲染混进判据）。
    """
    h, w = PATTERN_H, PATTERN_W
    a = np.zeros((h, w, 4), dtype=np.uint8)
    a[..., :3] = 235
    a[..., 3] = 255
    a[:, w // 2 - 6 : w // 2 + 6, :3] = 15  # 竖黑条
    a[h // 2 - 6 : h // 2 + 6, :, :3] = 15  # 横黑条
    for (y0, x0), col in (
        ((10, 10), (220, 30, 30)),
        ((10, w - 46), (30, 200, 40)),
        ((h - 46, 10), (40, 60, 220)),
        ((h - 46, w - 46), (240, 200, 20)),
    ):
        a[y0 : y0 + 36, x0 : x0 + 36, :3] = col
    for i in range(0, w, 24):  # 斜纹带，给特征匹配足够纹理
        a[h - 70 : h - 54, max(0, i - 6) : min(w, i + 6), :3] = 90
    return np.ascontiguousarray(a)


def qimage_from(arr: "np.ndarray") -> QImage:
    h, w = arr.shape[0], arr.shape[1]
    return QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy()


class HostWindow(QWidget):
    """Qt 侧宿主窗口：存在的意义是"Qt 真的在用这条连接"（mapped + 持续重绘）。"""

    def __init__(self):
        super().__init__()
        self.repaints = 0
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnBottomHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setFixedSize(48, 48)

    def paintEvent(self, ev):  # noqa: N802
        self.repaints += 1
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(20, 20, 24))
        p.end()


# ---------------------------------------------------------------- fidus 侧（工作线程）
def fidus_worker(box: dict, ev_move: threading.Event, do_stage_b: bool, pattern) -> None:
    """Fidus 是 `#[pyclass(unsendable)]`：创建与使用**必须同线程**，且在退出前释放。

    连接数必须在 **engine 存活期间**由本线程取样：`Fidus` 析构即关闭它自己那条
    Wayland 连接，等主线程 join 之后再数只剩 2 条（第一版就误把那个点当作判据点）。
    """
    t0 = time.perf_counter()
    try:
        box["probe_gate"] = (fidus_mod.Fidus.probe_gate(), time.perf_counter() - t0)
    except BaseException as exc:
        box["probe_gate"] = (f"RAISED {type(exc).__name__}: {exc}", time.perf_counter() - t0)
        box["fatal"] = "probe_gate"
        return
    box["conns_after_probe_gate"] = compositor_connections()

    t0 = time.perf_counter()
    try:
        eng = fidus_mod.Fidus.build_wayland()
        box["build"] = ("OK", time.perf_counter() - t0)
    except BaseException as exc:
        box["build"] = (f"RAISED {type(exc).__name__}: {exc}", time.perf_counter() - t0)
        box["fatal"] = "build_wayland"
        return
    box["conns_engine_alive"] = compositor_connections()
    try:
        box["gate_status"] = eng.gate_status()
    except BaseException as exc:
        box["gate_status"] = f"RAISED {type(exc).__name__}: {exc}"

    if not do_stage_b:
        del eng
        box["conns_after_drop"] = compositor_connections()
        return

    t0 = time.perf_counter()
    try:
        eng.register_target(pattern, ambiguous=False)
        box["register"] = ("OK", time.perf_counter() - t0)
    except BaseException as exc:
        box["register"] = (f"RAISED {type(exc).__name__}: {exc}", time.perf_counter() - t0)
        box["fatal"] = "register_target"
        del eng
        box["conns_after_drop"] = compositor_connections()
        return

    t0 = time.perf_counter()
    try:
        info = eng.calibrate_once()
        box["calibrate"] = (repr(info), time.perf_counter() - t0)
    except BaseException as exc:
        box["calibrate"] = (
            f"RAISED {type(exc).__name__}: {exc}",
            time.perf_counter() - t0,
        )
        box["fatal"] = "calibrate_once"
        del eng
        box["conns_after_drop"] = compositor_connections()
        return

    def estimates(engine, n):
        out = []
        for _ in range(n):
            t = time.perf_counter()
            try:
                out.append((engine.estimate(), time.perf_counter() - t))
            except BaseException as exc:
                out.append((f"RAISED {type(exc).__name__}: {exc}", time.perf_counter() - t))
            time.sleep(0.12)
        return out

    box["est_before"] = estimates(eng, 3)
    box["awaiting_move"] = True
    ev_move.wait(timeout=8.0)
    box["est_after"] = estimates(eng, 3)
    box["conns_mid_b"] = compositor_connections()
    del eng
    box["conns_after_drop"] = compositor_connections()


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage-a-only", action="store_true", help="只测连接共存，不做标定/估计")
    ap.add_argument("--pattern-xy", default="48,300", help="图案层左上角，形如 48,300")
    args = ap.parse_args()

    px, py = (int(v) for v in args.pattern_xy.split(","))
    print("=" * 78)
    print("H1 共存冒烟：Qt + 桥接层 + fidus wheel 三连接同进程")
    print("=" * 78)

    print("\n[0] 环境事实")
    fact("python", sys.version.replace("\n", " "))
    du = json.loads(importlib.metadata.distribution("fidus").read_text("direct_url.json"))
    fact("wheel sha256", du["archive_info"]["hashes"]["sha256"])
    fact("wheel url", os.path.basename(du["url"]))
    fact("fidus.__version__", fidus_mod.__version__)
    fact("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM"))
    fact("WAYLAND_DISPLAY", os.environ.get("WAYLAND_DISPLAY"))
    try:
        raw = subprocess.run(
            ["niri", "msg", "--json", "outputs"], capture_output=True, text=True, timeout=5
        ).stdout
        # `niri msg --json outputs` 是 {输出名: {...}} 的映射，不是列表。
        outs = json.loads(raw)
        fact(
            "niri outputs",
            [
                {
                    "name": o.get("name"),
                    "logical": o.get("logical"),
                }
                for o in outs.values()
            ],
        )
    except Exception as exc:
        fact("niri outputs", f"读取失败 {type(exc).__name__}: {exc}")
    fact("baseline 合成器连接", compositor_connections())

    print("\n[1] Qt 连接 #1")
    app = QApplication(sys.argv[:1])
    fact("platformName", app.platformName())
    scr = app.primaryScreen()
    geo = scr.geometry()
    fact("screen", f"{scr.name()} {geo.width()}x{geo.height()} dpr={scr.devicePixelRatio()}")
    host = HostWindow()
    host.move(geo.right() - 60, geo.top() + 12)
    host.show()
    ticks = {"n": 0}
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    timer.start()
    # 只数 QTimer  ticks 会高估"Qt 还活着"：事件循环能在不提交任何帧的情况下转。
    # 强制每 50ms 重绘 ⇒ 重绘增量是"Qt 这条连接仍在向合成器提交 buffer"的直接证据。
    repaint_timer = QTimer()
    repaint_timer.setInterval(50)
    repaint_timer.timeout.connect(host.update)
    repaint_timer.start()

    def pump(ms):
        end = time.perf_counter() + ms / 1000.0
        while time.perf_counter() < end:
            app.processEvents()
            time.sleep(0.002)

    pump(400)
    # QWidget 自己没有 isExposed()/全局坐标：那在底层 QWindow 上（`windowHandle()`）。
    wh = host.windowHandle()
    fact(
        "window mapped",
        f"visible={host.isVisible()} exposed={wh is not None and wh.isExposed()}",
    )
    fact(
        "window geometry",
        f"{host.mapToGlobal(QPoint(0, 0))} {host.width()}x{host.height()}",
    )
    conns_qt = compositor_connections()
    fact("合成器连接(含 Qt)", f"{len(conns_qt)} 条 {conns_qt}")

    print("\n[2] 桥接层连接 #2 + 显示图案")
    backend = FACADE.get_backend()
    t0 = time.perf_counter()
    try:
        backend.enable(host, PATTERN_W, PATTERN_H, px, py)
    except BaseException as exc:
        fact("enable", f"RAISED {type(exc).__name__}: {exc}")
        return 1
    fact("enable", f"OK {time.perf_counter() - t0:.3f}s ctx={backend._ctx}")
    shim = backend._shim
    shim.layer_last_error.restype = ctypes.c_char_p
    fact("layer_last_error", shim.layer_last_error())
    pattern = make_pattern()
    t0 = time.perf_counter()
    try:
        backend.update_pixels(qimage_from(pattern))
        fact("update_pixels", f"OK {time.perf_counter() - t0:.3f}s")
    except BaseException as exc:
        fact("update_pixels", f"RAISED {type(exc).__name__}: {exc}")
    pump(600)
    conns_bridge = compositor_connections()
    fact("合成器连接(含桥接)", f"{len(conns_bridge)} 条 {conns_bridge}")
    fact("layer_last_error(显示后)", shim.layer_last_error())

    print("\n[3] fidus 连接 #3（工作线程；主线程同时泵 Qt）")
    box: dict = {}
    ev_move = threading.Event()
    th = threading.Thread(
        target=fidus_worker,
        args=(box, ev_move, not args.stage_a_only, pattern),
        daemon=True,
    )
    tick_at_start = ticks["n"]
    repaint_at_start = host.repaints
    t_run = time.perf_counter()
    th.start()

    while th.is_alive():
        pump(120)
        if box.get("awaiting_move") and not ev_move.is_set():
            print(
                f"\n  [move] set_position({px + MOVE_DX},{py + MOVE_DY}) 由主线程执行"
                f"（fidus 线程正阻塞在 estimate 之间）",
                flush=True,
            )
            backend.set_position(px + MOVE_DX, py + MOVE_DY)
            pump(400)
            fact("move 后 layer_last_error", shim.layer_last_error())
            ev_move.set()
    th.join(timeout=5)
    dur = time.perf_counter() - t_run

    fact("fidus 线程存活时长", f"{dur:.2f}s")
    fact("probe_gate", f"{box.get('probe_gate')}")
    fact("  连接数(probe_gate 之后)", box.get("conns_after_probe_gate"))
    fact("build_wayland", f"{box.get('build')}")
    fact("  连接数(engine 存活期)", box.get("conns_engine_alive"))
    fact("gate_status", box.get("gate_status"))
    if not args.stage_a_only:
        fact("register_target", f"{box.get('register')}")
        fact("calibrate_once", f"{box.get('calibrate')}")
        fact("estimate(位移前)", box.get("est_before"))
        fact("estimate(位移后)", box.get("est_after"))
        fact("  连接数(stage B 结束时)", box.get("conns_mid_b"))
    fact("  连接数(engine 析构后)", box.get("conns_after_drop"))
    conns_fidus = compositor_connections()
    fact("合成器连接(主线程 join 后)", f"{len(conns_fidus)} 条 {conns_fidus}")
    fact("layer_last_error(全程后)", shim.layer_last_error())
    fact("桥接 ctx 仍存活", bool(backend._ctx))
    fact("Qt 仍存活", f"visible={host.isVisible()} dpr={scr.devicePixelRatio()}")

    print("\n[4] 主线程在 fidus 阶段是否持续推进（GIL 释放证据）")
    ticks_during = ticks["n"] - tick_at_start
    repaints_during = host.repaints - repaint_at_start
    fact("tick 增量", f"{ticks_during} (期望 ~{int(dur * 100)}，interval=10ms)")
    fact(
        "Qt 重绘增量",
        f"{repaints_during} (期望 ~{int(dur * 20)}，强制 update 每 50ms)",
    )
    fact("Qt 累计重绘", host.repaints)

    print("\n[5] 清理")
    t0 = time.perf_counter()
    backend.destroy_context()
    pump(200)
    fact("destroy_context", f"OK {time.perf_counter() - t0:.3f}s")
    fact("合成器连接(ctx 销毁后)", compositor_connections())
    # 门面 disable() 在 `_ctx is None` 时短路，不会再调 cleanup；探针要连**连接本身**
    # 一起收掉，才能验证"三连接全部退场、Qt 那条仍活"。直调 shim 是有意的越门面动作。
    shim.layer_shell_cleanup()
    pump(300)
    conns_after_cleanup = compositor_connections()
    fact("合成器连接(cleanup 后)", conns_after_cleanup)
    fact("Qt 仍存活(cleanup 后)", host.isVisible())
    host.close()
    pump(300)
    fact("合成器连接(Qt 关窗后)", compositor_connections())

    # ------------------------------------------------------------- 判定
    build_ok = str(box.get("build", ("",))[0]) == "OK"
    peak = box.get("conns_engine_alive") or []
    three = len(conns_bridge) >= 2 and len(peak) >= 3
    bridge_clean = shim.layer_last_error() in (None, b"", "")
    coexist = build_ok and three and bridge_clean
    responsive = ticks_during >= max(5, dur * 40) and repaints_during >= max(2, dur * 8)
    teardown_ok = len(conns_after_cleanup) <= 1
    loop_ok = None
    if not args.stage_a_only:
        eb = box.get("est_before") or []
        loop_ok = (
            str(box.get("register", ("",))[0]) == "OK"
            and str(box.get("calibrate", ("",))[0]).startswith("CalibInfo")
            and any(not str(v[0]).startswith("RAISED") for v in eb)
        )

    print("\n" + "=" * 78)
    print(
        "VERDICT-H1-COEXIST :",
        "PASS" if coexist else "FAIL",
        f"(build_ok={build_ok} 峰值连接={len(peak)} 桥接峰值={len(conns_bridge)} "
        f"桥接无粘性错误={bridge_clean})",
    )
    print(
        "VERDICT-UI-RESPONSIVE:",
        "PASS" if responsive else "FAIL",
        f"(tick={ticks_during} repaint={repaints_during} during={dur:.2f}s)",
    )
    print("VERDICT-TEARDOWN   :", "PASS" if teardown_ok else "FAIL",
          f"(cleanup 后残留连接={len(conns_after_cleanup)})")
    if loop_ok is None:
        print("VERDICT-CLOSED-LOOP: SKIPPED (--stage-a-only)")
    else:
        print(
            "VERDICT-CLOSED-LOOP:",
            "PASS" if loop_ok else "FAIL",
            f"(fidus 线程致命点={box.get('fatal', 'none')})",
        )
    print("=" * 78)
    return 0 if coexist else 3


if __name__ == "__main__":
    sys.exit(main())
