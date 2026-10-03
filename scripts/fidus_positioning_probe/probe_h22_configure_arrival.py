#!/usr/bin/env python3
"""H22 · 撤掉 exclusive-zone 带的那一瞬，合成器到底发不发 `configure`（只读观测）

出处：`pool/layer-configure-serial-symbol.md` §2 的**前置待测**。那件要加的第 13 枚符号
（"最近一次 `configure` 到达的序号"）值不值，全押在这一格上：

* 撤带时**发** configure 且**尺寸数值不变** ⇒ 宿主只看数值永远发现不了 ⇒ 序号是唯一渠道 ⇒ 立。
* 撤带时**发**且尺寸变 ⇒ 现有的 `logical_size()` 回读就够 ⇒ 符号不必加。
* 撤带时**根本不发** ⇒ 序号也读不到新值，本件前提塌 ⇒ 只剩"桥接层带出原始信息"或"手动重校准"。

**零改码**：`WAYLAND_DEBUG=1` 本来就打印每个事件，要量的是**协议到达**而不是桥内状态
⇒ 不临时改桥接层、不重打 `.so`。（比池件 §2 原计划的"桥内 `dbg!` 计数"省掉一整轮重打与分发。）

时钟对齐（本件量具的全部难点）
------------------------------
日志里有**两种格式、两个时钟**：Qt 那半（libwayland C）打 `[HH:MM:SS.us] {Default Queue} …`，
桥接层那半（`wayland-client` Rust）打 `[<毫秒>][rs] <- zwlr_layer_surface_v1@7.configure, (…)`，
后者是**单调毫秒**基（开机/会话起点的 `Instant`），与当日秒**差一个未知常量**，所以不能拿绝对时间戳硬比。
本件的处置：`set_size` 的**请求行**（`-> …set_size(w, h)`）是客户端同步发出的，
它就是一条自带载荷的刻度 ⇒ 用"我在 Python 里调 `set_size` 的那一刻"与"日志里那条同载荷请求行的
`[rs]` 值（换算成秒）"求偏移；四个锚点各求一次，**偏移散布超过 50 ms 就判不可靠 ⇒ 本件不判**。

相位与判据（顺序，不接受"看到 0 条就算否证"）
--------------------------------------------
* **W_PRE / W_POST（通道自证）** —— 开局与收尾各改一次 size；layer-shell 对 size 请求**必须**回一发
  configure。这两窗收不到对应载荷 ⇒ 观测本身失效，撤带那格的 0 不作数。
* **W_BASE（带出现）** —— 面板挂上那一窗（我方零请求）：带出现时发不发。
* **W_ON（带在场静默）** —— 带稳定后 3 s 零请求窗：应为 0 条，它是"W_OFF 的 0 是不是常态"的对照。
  **少了这一格，"撤带不发"就分不清是"合成器不管带"还是"合成器此刻什么都不发"。**
* **W_OFF（撤带，承重）** —— 面板终止、`wait_panel(False)` 落实后的 3 s。这一窗我方不发任何请求
  （不重新挂载），出现的 configure 只可能是合成器自己发的。

用法
----
    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland WAYLAND_DEBUG=1 \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h22_configure_arrival.py
    # stderr 必须在 --wd-log 那个文件里（shell 重定向 2>），探针结束后自己读它、按锚点切窗。
副作用：真实桌面上挂/拆 layer 浮层与那条 180 px 面板带；不改产品码、不改桥接层、不重打 `.so`。
退出码：0 = 跑到出口（结论再否定也是 0）；1 = 量具/环境不可用（面板造不出来、桥接层没起来）。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import statistics
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import probe_h15_product_wiring as H15  # noqa: E402
import probe_h16_exclusive_zone as H16  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h3_targetability as H3  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402

# [2159071.847][rs] <- zwlr_layer_surface_v1@7.configure, (1149701, 7, 16)
EVENT_RE = re.compile(
    r"^\[(?P<ts>[\d.]+)\]\[rs\]\s*<-\s*zwlr_layer_surface_v1@\d+\.configure,\s*\((?P<args>[^)]*)\)")
# [2159070.547][rs] -> zwlr_layer_surface_v1@7.set_size(7, 16)
REQUEST_RE = re.compile(
    r"^\[(?P<ts>[\d.]+)\]\[rs\]\s*->\s*zwlr_layer_surface_v1@\d+\.set_size\(\s*(?P<args>[^)]*?)\s*\)")

OFFSET_SPREAD_MAX_S = 0.05    # 四个锚点求出的偏移，散布超过这个数就判"对齐不可靠"
RS_TICK_TO_S = 1e-3           # `[rs]` 打的数**是毫秒**，不是秒（下证实测）

# 实证：同一轮里 `[rs]` 相邻两次锚点请求差 2999.3 / 3000.1，而我方 pump 恰为 --hold-ms 3000
# ⇒ 单位是 ms。若这个换算错了，散布门会炸到秒级以上 ⇒ 落到"本件不判"，不会给出假结论。


def fact(label, value) -> None:
    H6.fact(label, value)


def utc_secs_of_day() -> float:
    return time.time() % 86400.0


def parse_log(path: Path) -> tuple[list[tuple[float, tuple]], list[tuple[float, tuple]]]:
    """返回 (configure 事件 [ts,(serial,w,h)]，set_size 请求行 [ts,(w,h)])，都按 ts 升序。"""
    events, requests = [], []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        for rx, sink, arity in ((EVENT_RE, events, 3), (REQUEST_RE, requests, 2)):
            m = rx.match(line)
            if not m:
                continue
            nums = tuple(int(v.strip()) for v in m.group("args").split(",") if v.strip())
            if len(nums) != arity:
                continue
            sink.append((float(m.group("ts")) * RS_TICK_TO_S, nums))
    return events, requests


def align(anchors, requests) -> tuple[float | None, str]:
    """用锚点（Python 调 `set_size` 的当刻 ↔ 日志里同载荷请求行）求 `[rs]`→当日秒 的偏移。

    同一载荷会被请求多次（A2 与 D2 都是回到基线尺寸）⇒ 按锚点顺序**依次消耗**请求行，
    不做"全局找最近"，否则两个同载荷锚点会互相抢。
    """
    if not anchors:
        return None, "没有锚点"
    used = [False] * len(requests)
    diffs = []
    triples = []
    missing = []
    for name, secs, size in sorted(anchors, key=lambda a: a[1]):
        hit = None
        for i, (_ts, payload) in enumerate(requests):
            if used[i] or payload != size:
                continue
            hit = i
            break
        if hit is None:
            missing.append(f"{name}(set_size{size})")
            continue
        used[hit] = True
        diffs.append((name, secs - requests[hit][0]))
        triples.append((name, secs, requests[hit][0]))
    if missing:
        return None, f"这些锚点的请求行没等到（时钟对齐缺刻度）：{missing}"
    if len(diffs) < 2:
        return None, f"可用锚点只有 {len(diffs)} 个 ⇒ 单点偏移无法自证，本件不判"
    vals = [d for _n, d in diffs]
    spread = max(vals) - min(vals)
    if spread > OFFSET_SPREAD_MAX_S:
        return None, (f"锚点偏移散布 {spread * 1e3:.1f} ms > {OFFSET_SPREAD_MAX_S * 1e3:.0f} ms"
                      f"（各锚点 {[f'{n}:{d:.3f}' for n, d in diffs]}）"
                      " ⇒ 两个时钟对不上，切窗不可靠，本件不判")
    return statistics.median(vals), (f"{len(vals)} 个锚点，散布 {spread * 1e3:.1f} ms，取中位数"
                                     f"｜配对 {[f'{n}:当日{d:.3f}↔rs{t:.3f}' for n, d, t in triples]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wd-log", default="/tmp/h22_wd.log",
                    help="WAYLAND_DEBUG 的 stderr 落点（本探针事后要读它）")
    ap.add_argument("--hold-ms", type=int, default=3000,
                    help="每个观察窗的长度（毫秒）")
    args = ap.parse_args()

    wd_path = Path(args.wd_log)
    if not H16.PANEL_BIN.exists():
        print(f"VERDICT-W_OFF : ✗ 面板二进制不在（{H16.PANEL_BIN}）⇒ 造不出带，本件不跑")
        return 1
    # 上一轮日志不能混进本轮：靠 shell 的 `2>` 截断，**进程内不 unlink**
    # （unlink 会让目录项消失、fd 还在写，收尾就读不到文件了）。
    if wd_path.exists() and wd_path.stat().st_size > 0:
        print(f"VERDICT-W_OFF : ✗ {wd_path} 非空 ⇒ stderr 请重定向到一个新文件（`2>` 会截断），"
              "本件不跑")
        return 1

    app = QApplication.instance() or QApplication(sys.argv)
    fact("锚点", __import__("fidus").__git_commit__)
    frames, _rinfo, l_host, _lw = H3.render_live2d_frames(app, 1)
    if not frames:
        print("VERDICT-W_OFF : ✗ 活体渲染不可得 ⇒ 量具自身失效，不猜")
        return 1
    l_host.hide()
    H4.pump(app, 400)
    frame = frames[0]
    fh, fw = frame.shape[:2]

    from meapet.config.store import load_config

    cfg = load_config()
    cfg.setdefault("fidus", {})["enabled"] = False        # 本件与 fidus 无关
    host = H15.WiringHost(cfg)
    label = QLabel(host)
    label.render_offscreen = lambda: H1.qimage_from(frame)
    host.sprite_label = label
    host.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
    host.setAttribute(Qt.WA_QuitOnClose, False)
    host.show()
    H4.pump(app, 600)
    host._init_layer_overlay_mode()
    H4.pump(app, 1500)
    backend = getattr(host, "_layer_backend", None)
    if backend is None:
        print("VERDICT-W_OFF : ✗ 桥接层没起来 ⇒ 无从观测 configure")
        H15.teardown(host, app)
        return 1

    marks: list[tuple[str, float]] = []
    anchors: list[tuple[str, float, tuple]] = []
    rc = 0
    proc = None
    try:
        def mark(name: str) -> None:
            secs = utc_secs_of_day()
            marks.append((name, secs))
            fact(f"相位 {name}", f"当日秒 {secs:.3f}｜configure 回读={backend.logical_size()}")

        def resize(name: str, w: int, h: int) -> None:
            """带载荷的时钟锚点：请求行与这里的 secs 差在几毫秒内。"""
            secs = utc_secs_of_day()
            anchors.append((name, secs, (w, h)))
            backend.set_size(w, h)
            marks.append((name, secs))
            fact(f"锚点/相位 {name}", f"当日秒 {secs:.3f}｜请求 set_size({w},{h})")

        geo, avail = H16.rects(app)
        fact("桌面", f"geometry={geo} available={avail}｜surface 请求=({fw},{fh})")

        # ── W_PRE：通道自证（开局）
        mark("A0-start")
        resize("A1-resize-down", fw, fh - 40)
        H4.pump(app, args.hold_ms)
        resize("A2-resize-back", fw, fh)
        H4.pump(app, args.hold_ms)
        mark("A3-baseline-settled")

        # ── W_BASE：带出现（我方零请求）
        proc = H16.spawn_panel("top")
        if proc is None or not H16.wait_panel(app, True):
            print("VERDICT-W_OFF : ✗ 面板挂不上 ⇒ 造不出带，本件不跑")
            rc = max(rc, 1)
            mark("B0-panel-fail")
        else:
            H4.pump(app, args.hold_ms)
            rows, shot_h = H16.band_rows_visible()
            fact("带在场", f"grim 整行面板色 {rows} 行（抓帧高 {shot_h}，面板自述 {H16.BAND_PX}）"
                           f"｜Qt available={H16.rects(app)[1]}｜configure 回读={backend.logical_size()}")
            mark("B1-band-on")

            # ── W_ON：带在场静默（我方零请求）——W_OFF 的 0 是不是常态，靠这一格分辨
            H4.pump(app, args.hold_ms)
            mark("B2-band-on-settled")

            # ── W_OFF（承重）：撤带，保持带在时那一相，我方不发任何请求
            if proc is not None:
                proc.terminate()
                proc.wait(timeout=10)
                proc = None
            if not H16.wait_panel(app, False):
                print("VERDICT-W_OFF : ✗ 面板没撤干净 ⇒ 撤带那一窗不成立")
                rc = max(rc, 1)
            mark("C1-band-off")
            H4.pump(app, args.hold_ms)
            rows_after, _sh = H16.band_rows_visible()
            fact("带撤后", f"grim 整行面板色 {rows_after} 行（撤干净应为 0）"
                           f"｜configure 回读={backend.logical_size()}")
            mark("C2-band-off-settled")
        # ── W_POST：通道自证（收尾）——即使面板失败也要跑，否则"不可判"与"通道死"分不开
        resize("D1-resize-control-end", fw, fh - 24)
        H4.pump(app, args.hold_ms)
        resize("D2-resize-back-end", fw, fh)
        H4.pump(app, args.hold_ms)
        mark("Z-end")
    finally:
        if proc is not None:
            proc.terminate()
        H15.teardown(host, app)

    if rc:
        return rc
    return report(marks, anchors, wd_path, backend)


def report(marks, anchors, wd_path: Path, backend) -> int:
    print("\n=== 事件流切窗（读 stderr 里的 WAYLAND_DEBUG）===")
    if not wd_path.exists():
        print("VERDICT-W_OFF : ✗ 观测日志不存在 ⇒ stderr 没重定向到 --wd-log？本件不跑")
        return 1
    events, requests = parse_log(wd_path)
    fact("日志面", f"configure 事件 {len(events)} 条、set_size 请求行 {len(requests)} 条")
    if not events or not requests:
        print("VERDICT-W_OFF : ✗ 日志里"
              "一条 `zwlr_layer_surface_v1.configure` 或 `set_size` 请求都没有"
              " ⇒ 观测通道本身不成立（`WAYLAND_DEBUG=1` 没生效？），撤带那一格的 0 不作数")
        return 1
    offset, why = align(anchors, requests)
    if offset is None:
        print(f"VERDICT-W_OFF : 不可判｜时钟对齐失败：{why}")
        return 0
    fact("时钟对齐", f"[rs]→当日秒 偏移 = {offset:.3f}（{why}）")

    # 两个面各归各：偏移定义为「当日秒 − rs秒」⇒ 把**标记**搬进 rs 面来切窗，
    # 事件本身已经在 rs 面（曾经这里也减了一次偏移，等于搬了两趟 ⇒ 所有窗都落空）。
    conv = list(events)
    m_rs = [(name, secs - offset) for name, secs in marks]
    pairs = list(zip(m_rs, m_rs[1:]))
    windows = [(f"{a[0]}→{b[0]}", a[1], b[1]) for a, b in pairs]
    got = {name: [] for name, _a, _b in windows}
    orphan = []
    for secs, payload in conv:
        hit = [name for name, a, b in windows if a <= secs < b]
        (got[hit[0]] if hit else orphan).append(payload)
    for name, a, b in windows:
        fact(name, f"{len(got[name])} 条：{got[name]}"
                   f"（窗宽 {b - a:.2f} s）")
    if orphan:
        fact("窗外（不静默丢）", f"{len(orphan)} 条：{orphan[:8]}")

    def win(a_key: str, b_key: str) -> list[tuple]:
        """按**时间区间**聚合，不是按窗名取键——W_PRE/W_POST 横跨好几个相邻窗，
        按键取会永远取空（曾经就是这样，把两条对照都读成 0 条 ⇒ 假"不可判"）。"""
        at = {name: secs for name, secs in m_rs}
        lo, hi = at[a_key], at[b_key]
        return [p for s, p in conv if lo <= s < hi]

    pre = win("A0-start", "A3-baseline-settled")
    post = win("D1-resize-control-end", "Z-end")
    w_base = win("A3-baseline-settled", "B1-band-on")
    w_on = win("B1-band-on", "B2-band-on-settled")
    w_off = win("C1-band-off", "C2-band-off-settled")
    if not w_base:      # 面板失败那一支：出现窗不存在，就别把它读成"出现时不发"
        w_base = []
    fact("CONTROL", f"W_PRE {len(pre)} 条 {pre}｜W_POST {len(post)} 条 {post}")
    ctrl_ok = any(p[1:] == (anchors[0][2][0], anchors[0][2][1]) for p in pre) \
        and any(p[1:] == (anchors[-2][2][0], anchors[-2][2][1]) for p in post) \
        and len(pre) >= 1 and len(post) >= 1
    if not ctrl_ok:
        print("VERDICT-W_OFF : 不可判｜两次 set_size 对照里至少有一发没在对应窗收到"
              " ⇒ 观测通道不能证明「撤带时没发」，本件不给结论，也不给池件加符号的理由")
        return 0

    print(f"\nVERDICT-W_BASE(带出现)   : {len(w_base)} 条｜载荷 {w_base}")
    print(f"VERDICT-W_ON(带在场静默)  : {len(w_on)} 条｜载荷 {w_on}")
    print(f"VERDICT-W_OFF(撤带·承重)  : {len(w_off)} 条｜载荷 {w_off}")

    if not w_off:
        asym = "而带**出现**那一窗有 " + str(len(w_base)) + " 条" if w_base else \
               "且带**出现**那一窗也是 0 条 ⇒ niri 对已挂载 surface 的 exclusive-zone 变化**根本不发 configure**"
        print(f"VERDICT-W_OFF : **撤带时不发 configure**（{asym}）"
              " ⇒ 池件 layer-configure-serial-symbol 的前提塌：序号读不到新值，加它不解决问题。"
              "剩下的只有「桥接层带出原始信息」或「宿主手动重校准」两条")
        return 0

    sizes_off = {p[1:] for p in w_off}
    off_start = dict(m_rs)["C1-band-off"]
    last_before = [p for secs, p in conv if secs < off_start]
    prev = last_before[-1] if last_before else None
    if prev is not None and all(p[1:] == prev[1:] for p in w_off):
        print(f"VERDICT-W_OFF : 发 {len(w_off)} 条，**尺寸数值与前一发逐字节相同**（都是 {prev[1:]}，"
              f"serial 从 {prev[0]} 变到 {w_off[-1][0]}）⇒ **只有事件、没有数值差**："
              "宿主从数值面上永远看不见这一格，序号（或 dirty 位）是唯一渠道 ⇒ 池件该立")
    else:
        print(f"VERDICT-W_OFF : 发 {len(w_off)} 条，且**尺寸数值变了**（{sizes_off} vs 前一发 "
              f"{None if prev is None else prev[1:]}）⇒ 现有 `logical_size()` 回读就够，不需要序号")
    del backend
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
