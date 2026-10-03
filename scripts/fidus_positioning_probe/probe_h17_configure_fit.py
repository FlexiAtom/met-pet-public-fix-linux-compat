"""H17 · #78 的回读—夹移闭环：真机上量"合成器配出的尺寸"这条通道到底通不通。

H16 是本件的前提：它在同一台机器上量到"面板在场 ⇒ configure 被夹到 445 ⇒ 我方每一帧
都被自家的尺寸门控丢掉"。本件不重复那条发现，只判**修好之后**的三件事：

    F0 无面板（对照）  回读通道本身可信吗？—— 配出的尺寸 == 请求 ⇒ 一轮下来**零次夹移**。
                      这条同时钉住 `layer_logical_size` 的 ctypes 出参形状（L3）：
                      假 shim 写 `.value` 证不了真桥的按引用回写，只有这里能证。
    F1 有面板（正证）  合成器把请求高夹小之后，产品自己回摆那一发；落定时的
                      `logical_size()` 必须重新等于请求，且**信念矩形**（含带厚换算）与
                      grim 独立尺子读到的真值对齐 ⇒ 摆位不再偏一条保留带。
    F2 撤面板          干扰撤走后再挂一次：不许留下"我以为我在别处"的残余信念，也不许
                      在无需夹移时白发一发。
    --eyeball [秒]     grim 与被怀疑的那条路同源（H16 记过），"到底看不看得见"只由人给。
                      问句自带**按 canvas 顶部留白算出的预期间隙**——不压红是算术要求的，
                      不是人给的例外（上一版把 surface 顶当头顶，写反了预期）。

判据全部走 H12 那把独立尺子（grim + 宿主 NCC，不经 fidus），阈值沿用 `PRESENT_PEAK`
与 `TOL_PX`。本件不测 fidus（开关关掉），也不改任何产品状态：结束一律 teardown。

退出码：0 = 各臂跑到出口且判据绿；1 = 量具／面板不可用（不产出主张）；2 = 判据落红。

用法：

    WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \\
        .venv/bin/python scripts/fidus_positioning_probe/probe_h17_configure_fit.py
    #   带人眼窗（只在 F1 之后开一扇——仪器分不清"没上屏"与"没进抓帧"，见 H16 那段）：
    ... probe_h17_configure_fit.py --eyeball 60
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QLabel

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE.parents[1])):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import probe_h12_product_path as H12  # noqa: E402
import probe_h15_product_wiring as H15  # noqa: E402
import probe_h16_exclusive_zone as H16  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402
import probe_h7_roi_census as H7  # noqa: E402

fact = H12.fact
TOL_PX = H12.TOL_PX
GATE = H12.PRESENT_PEAK


def fit_settled(app, host, timeout: float = 8.0) -> bool:
    """泵到产品那一轮"回读—夹移"落定（`_fit_rect` 归 None）。返回 True = 超时仍挂着。"""
    deadline = time.perf_counter() + timeout
    while getattr(host, "_fit_rect", None) is not None and time.perf_counter() < deadline:
        H4.pump(app, 100)
    return getattr(host, "_fit_rect", None) is not None


def track_moves(host) -> list:
    """记产品自己发出的夹移。**只观察不改判**：包一层计数，仍然调产品原方法。"""
    moves: list[tuple[int, int]] = []
    orig = host._layer_fit_move

    def spy(x: int, y: int) -> bool:
        ok = bool(orig(int(x), int(y)))
        if ok:
            moves.append((int(x), int(y)))
        return ok

    host._layer_fit_move = spy
    return moves


def switch_once(app, host, box, off, tag, fw, fh):
    """一次真实的产品挂载：摆位→挂载→等那一轮回读落定→用**信念矩形**当尺子的基准。

    刻意不用 `H16.run_one_switch`：它拿"挂载前的请求"当基准，而本件要判的正是
    "产品自己挪了位之后，信念与屏幕还对不对得上"——基准得是挪完之后的那一份。
    """
    if host.isVisible() and getattr(host, "_layer_backend", None) is not None:
        host._set_layer_mode(False)
        H4.pump(app, 500)
    moves = track_moves(host)
    host.bubbles.clear()
    geom_req = H16.place_by_product(app, host, fw, fh)
    se_before = H16.sticky_error()
    host._set_layer_mode(True)
    hung = fit_settled(app, host)
    H4.pump(app, 400)

    backend = getattr(host, "_layer_backend", None)
    logical = None if backend is None else backend.logical_size()
    proxy = getattr(host.sprite_label, "_proxy_rect", None)
    belief = None if proxy is None else H15.rect_of(proxy)
    truth = None if belief is None else H12.box_truth(box, off, tag)
    delta = None
    if truth is not None and belief is not None:
        # `box_truth` 给的是 surface **中心**的真值 ⇒ 信念也换算成中心再相减。
        delta = (truth[0] - (belief[0] + belief[2] / 2.0),
                 truth[1] - (belief[1] + belief[3] / 2.0), truth[2])
    return {"geom_req": geom_req, "moves": moves, "hung": hung, "logical": logical,
            "belief": belief, "delta": delta, "bubbles": list(host.bubbles),
            "se_before": se_before, "se_after": H16.sticky_error()}


def report(tag, r, req_size, want_moves):
    """一段事实一条判据；哪一环断就直说断在哪，不拿别的环补。"""
    if r["hung"]:
        print(f"VERDICT-{tag} : ✗ 回读轮超时未落定 ⇒ 本臂无读数")
        return 2
    rc = 0
    logical = None if r["logical"] is None else tuple(r["logical"])
    if logical is None:
        print(f"VERDICT-{tag} : ✗ 真桥的 `logical_size()` 给 None ⇒ §7.1 #12 这条通道"
              f"不通（不猜它该给什么）")
        rc = 1
    elif logical != tuple(req_size):
        print(f"VERDICT-{tag} : RED 落定后 configure 仍是 {logical}（请求 {tuple(req_size)}）")
        rc = 2
    if len(r["moves"]) != want_moves:
        print(f"VERDICT-{tag} : RED 夹移发了 {len(r['moves'])} 发 {r['moves']}"
              f"（该恰为 {want_moves} 发）")
        rc = 2
    if r["delta"] is None:
        print(f"VERDICT-{tag} : ✗ 独立尺子不可得（grim 未出图／信念矩形没写）⇒ 落点不判")
        rc = max(rc, 1)
    else:
        dx, dy, peak = r["delta"]
        if peak < GATE:
            print(f"VERDICT-{tag} : ✗ 尺子峰 {peak:.4f} < 在位闸 {GATE} ⇒ 屏上没有我方内容，"
                  f"落点不判（这不是本臂的否证）")
            rc = max(rc, 1)
        elif max(abs(dx), abs(dy)) > TOL_PX:
            print(f"VERDICT-{tag} : RED 信念−真值 = ({dx:+.2f},{dy:+.2f}) px > {TOL_PX}"
                  f"　峰={peak:.4f}")
            rc = 2
        else:
            print(f"VERDICT-{tag} : PASS configure={logical} 夹移={r['moves']}｜"
                  f"信念 {r['belief']} 与真值差 ({dx:+.2f},{dy:+.2f}) px　峰={peak:.4f}")
    if r["bubbles"]:
        print(f"VERDICT-{tag} : 出口出声了 ⇒ 气泡 {r['bubbles']}（原因见上面 [layer] 行）")
        rc = 2 if rc == 0 else rc
    if r["se_after"] != r["se_before"]:
        print(f"VERDICT-{tag} : RED 黏性槽有变化 ⇒ 帧闸门仍在拒帧｜前={r['se_before']}｜"
              f"后={r['se_after']}")
        rc = 2 if rc == 0 else rc
    return rc


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    fact("锚点", __import__("fidus").__git_commit__)
    frames, _rinfo, l_host, _lw = H7.H3.render_live2d_frames(app, 1)
    if not frames:
        print("VERDICT-H17 : ✗ 活体渲染不可得 ⇒ 量具自身失效，不猜")
        return 1
    l_host.hide()
    H4.pump(app, 400)
    frame = frames[0]
    fh, fw = frame.shape[:2]
    box, off = H12.probe_box(frame)
    if box is None:
        print("VERDICT-H17 : ✗ 选不出独立量具盒 ⇒ 量具自身失效，不猜")
        return 1

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
    H4.pump(app, 1200)

    rc = 0
    proc = None
    try:
        geo, avail = H16.rects(app)
        fact("桌面", f"geometry={geo} available={avail}｜surface 请求=({fw},{fh})｜"
                     f"模型：撤走干扰后落点应 = 请求位，无需夹移")

        r0 = switch_once(app, host, box, off, "h17_f0", fw, fh)
        fact("F0", f"请求 {r0['geom_req']}｜configure {r0['logical']}｜夹移 {r0['moves']}｜"
                   f"信念 {r0['belief']}｜黏性槽 前={r0['se_before']} 后={r0['se_after']}")
        rc = max(rc, report("F0", r0, (fw, fh), want_moves=0))

        proc = H16.spawn_panel("top")
        if proc is None or not H16.wait_panel(app, True):
            print("VERDICT-H17 : ✗ 面板挂不上 ⇒ 本机造不出 F1 那一格，不产出主张")
            rc = max(rc, 1)
        else:
            H4.pump(app, 1200)
            rows, shot_h = H16.band_rows_visible()
            fact("面板", f"grim 整行面板色 {rows} 行（抓帧高 {shot_h}，自述 "
                         f"{H16.BAND_PX}）｜Qt available={H16.rects(app)[1]}")
            r1 = switch_once(app, host, box, off, "h17_f1", fw, fh)
            fact("F1", f"请求 {r1['geom_req']}｜configure {r1['logical']}｜"
                       f"夹移 {r1['moves']}｜信念 {r1['belief']}｜"
                       f"黏性槽 前={r1['se_before']} 后={r1['se_after']}")
            rc = max(rc, report("F1", r1, (fw, fh), want_moves=1))
            if r1["belief"] is not None and r1["geom_req"] is not None:
                fact("F1 带换算",
                     f"信念左上−请求左上 = ({r1['belief'][0] - r1['geom_req'][0]:+d},"
                     f"{r1['belief'][1] - r1['geom_req'][1]:+d}) px｜模型：屏幕落点"
                     f" = 屏高 − 请求高 = {geo[3] - fh}")
            secs = H16.eyeball_secs()
            # 我方到底盖没盖住红条：**不依赖 alpha 阈值**的一读。面板色的"整行"数在无我方
            # 内容时是 180；我方只要遮住红一条，那一行就不再整行是面板色 ⇒ 少的行数=盖住的行数。
            # 这一读专治" belief 矩形说留 15 px、人眼看像贴上"这类分辨不出的差（15 px 只有屏高 2%）。
            fitted_rows, _sh = H16.band_rows_visible()
            fact("F1 面板可见整行",
                 f"{fitted_rows} 行（面板在场、我方未遮时={H16.BAND_PX}）⇒ 我方盖住的整行数 "
                 f"{H16.BAND_PX - fitted_rows}；>0 才是真'桌宠压红条'")
            if secs:
                # 问句里带**预测数字**：canvas 边 ≠ 桌宠边。上一版这句写的是"头压在红条上"，
                # 那是拿 surface 顶行当头顶行——人报了"有空隙"才对，错的是探针自己的预期措辞。
                _bx0, _by0, _bx1, _by1 = H15.opaque_bbox(frame)
                surf_top = (r1["belief"][1] if r1["belief"] is not None
                            else geo[3] - fh)
                head = surf_top + _by0
                gap = head - H16.BAND_PX
                fact("F1 人眼预期",
                     f"surface 顶第 {surf_top} 行 ⇒ 与面板矩形**相交** {H16.BAND_PX - surf_top} 行；"
                     f"但相交≠遮挡（我方 `Layer::Overlay`、面板 `Layer::Top` ⇒ 遮不住），"
                     f"且那几行是**透明留白**（轮廓顶留白 {_by0} px）⇒ 头顶像素预测在第 {head}"
                     f" 行，面板底第 {H16.BAND_PX} 行 ⇒ 预期间隙 {gap:+d} px")
                H16.eyeball_hold(
                    app, secs,
                    "请看屏幕：顶部一条鲑红横条，桌宠应当**整块可见**。按留白换算，"
                    f"头顶像素预测在第 {head} 行、面板底在第 {H16.BAND_PX} 行"
                    f"⇒ 预测两者之间留 {gap:+d} px 空隙；"
                    f"负数=头顶进带内（我方在 Overlay，那一格会是**桌宠压红**，不是红压桌宠）。"
                    "看得见吗？空隙在不在？")

        # ────────────────────────────────── F2 撤掉干扰，回到 F0 那一相
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)
            proc = None
        if not H16.wait_panel(app, False):
            print("VERDICT-F2 : ✗ 面板没撤干净 ⇒ 收尾失败，本臂不判")
            rc = max(rc, 1)
        else:
            # ── F3（**只读、不进 rc**）：面板刚撤，而我方仍是 F1 那一发夹移的 margin（−26）。
            #    带没了 ⇒ 同一个 −26 换算成屏幕第 −26 行：头顶进带区、上方 26 px 出屏，
            #    信念矩形却还写着 154。这是 #78 已知限制"回读只在挂载那一刻跑"的**症状**，
            #    读数登记给 pool/layer-fit-round-only-at-mount.md；不改本件退出码——
            #    本件判的是"挂载那一次能不能把帧放出来"，这一相压根没有重新挂载。
            H4.pump(app, 400)
            be3 = getattr(host, "_layer_backend", None)
            px3 = getattr(host.sprite_label, "_proxy_rect", None)
            fact("F3 撤带瞬间（不判）",
                 f"信念仍={None if px3 is None else H15.rect_of(px3)}｜"
                 f"configure={None if be3 is None else be3.logical_size()}｜"
                 f"grim 尺子(中心x,中心y,峰)={H12.box_truth(box, off, 'h17_f3')}｜"
                 f"面板整行={H16.band_rows_visible()[0]}（撤干净应为 0）")
            H4.pump(app, 800)
            r2 = switch_once(app, host, box, off, "h17_f2", fw, fh)
            fact("F2", f"请求 {r2['geom_req']}｜configure {r2['logical']}｜"
                       f"夹移 {r2['moves']}｜信念 {r2['belief']}")
            rc = max(rc, report("F2", r2, (fw, fh), want_moves=0))
    finally:
        if proc is not None:
            proc.terminate()
        H15.teardown(host, app)
    print(f"\n=== H17 退出码 {rc}（0=绿，1=量具断，2=判据红）===")
    return rc


if __name__ == "__main__":
    sys.exit(main())
