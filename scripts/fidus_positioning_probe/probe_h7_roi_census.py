#!/usr/bin/env python3
"""H7 · ROI 普查：把"取哪一块当模板"从**几何巧合**改成**判据化选择**

出处：定位线卡在"产品素材的置信通道是死的"。`§12f-6` + H6 的 18 格普查给出的是：

| 现有 ROI 定义 | 引擎判决 | ceiling |
|---|---|---|
| `raw`（整窗）/`alpha_crop`（外接框） | 12/18 格直接 `FidusUntrackable` | — |
| `patch`（不透明质心内接 160²） | 6/6 接受 | **6 格全部正好 = 地板 `0.050000000745058…`** |

也就是说：现在能注册的那一种，其 `1−s` 通道在真机上**不含任何信息**（`s ≥ 0.95` 恒被地板截断）；
而 `patch` 的锁还**系统性偏 23.8 px**（它锁的是质心处那块内容，不是窗口中心）。
调用方裁决："ROI 定义我建议改"。本探针就是**改之前先把候选量出来**——没有这张表，
"改成什么"只能是审美选择。

规则（**事先写死，不在读数之后挑**）
-----------------------------------
候选盒 = 满足全部三条者：
1. 尺寸来自固定档 `SIZES`（与 `H3.PATCH_SIZES` 同一立场：事后按帧挑尺寸＝挑结论）；
2. 整盒落在不透明轮廓内（`coverage ≥ MIN_COVERAGE`）——把"透明区按 RGB 计亮度"那个**口径**
   问题从根上绕开：盒内几乎没有透明像素，模板说什么与屏上有什么就不会在透明区必然不一致；
3. 通过 fidus 自己的**结构门**（`localizability` 判 `ok`，即非 `featureless`/`too_small`）——
   否则"低自相似"可以由一块**平坦区**廉价取得，那是把引擎的拒绝换了个方向犯。

选择键 = `(s_engine 升, 面积降, 距窗口中心升)`。`s_engine` 是 H6 已证**与引擎逐位等**的
宿主复刻（细半径 + 密铺 + 预算），先用便宜的细半径值排名取每档 top-K，再对少数候选算全量并
向引擎对表。第三键只为确定性，不主张"居中更好"。

四条独立 VERDICT（不合并、不互相顶替）
-------------------------------------
* `RULE`  规则**逐帧独立**跑一遍：每帧各自选出的盒，其引擎 ceiling 是否离地板。
  这是**样本外**验证——不是拿帧 0 挑出最好的一块再炫耀它的读数。
* `RIGID` 把帧 0 选中的盒当固定模板，去匹配帧 1..N-1：峰值衰减 + **峰在窗口内的位移**。
  低歧义与刚性是**对立**的：脸上五官最"不重复"（s 低 ⇒ ceiling 高），但动画会让它们在窗内**自己动**；
  轮廓外扩/头发随呼吸动，躯干最刚。所以 `RULE` 过了 `RIGID` 也可能不过——两问必须分开答。
* `ANCHOR` 盒中心 → 窗口中心的偏移是否**恒定**（能常量换算才是可用的位置通道；
  `patch` 的 23.8 px 就是这一项没被管住的结果，它的来源是质心随动画漂）。
* `COST`  选中盒的注册墙钟（best-of-1）。契约 v7 第 8 条：重注册不得进 2 ms GUI tick。
* `LADDER` 把三条约束（尺寸降档 / 整盒在轮廓内 / argmin 搜索）**一条条加上去**，看 ceiling 的
  收益到底归属哪一条。没有这张表，"改 ROI"会被误读成"改成搜索"——而搜索恰恰可能是最不该要的那条。
* `FREEZE` 三种产品口径对照：**固定几何盒**（同坐标逐帧重切）/ **逐帧 argmin** / **冻结帧 0 模板**。
  `RULE` 一臂暴露了逐帧 argmin 会**换位置**（锚点常量随之漂 ⇒ 位置通道被自己污染），
  本臂量"退回固定盒要付多少 s"，用来判"要不要搜索 + 迟滞带"。

失效边界 / 诚实声明
------------------
* 本探针**只回答"能否注册、ceiling 多少、在窗内动不动"**。**屏上**首锁、空桌面误锁峰值
  仍需真机（`probe_h3_targetability --on-screen` / `probe_h5_ceiling_surface.py` 的 C 臂族）；
  新 ROI 的**误锁**一项在本探针里是**未测**，不得由"ceiling 高"外推。
* 渲染阶段会真机 `show()` 一个桌宠窗口（退出即关），其余全程 CPU：不抓屏、不上屏、不
  `calibrate_once`、不 `estimate`。`--replay` 分支连桌面无副作用。
* 渲染帧**不可跨进程复现** ⇒ 结论要跨轮比只能 `--dump` 后 `--replay`；输出末尾打印素材指纹。
* 密铺判据若再变，`ANCHOR` 常量和 H6 的公式会失配 ⇒ 锚点变更必重跑（§12h-3）。

用法
----
    QT_QPA_PLATFORM=wayland .venv/bin/python scripts/fidus_positioning_probe/probe_h7_roi_census.py
    # 只回放、不渲染（零桌面副作用）：
    .venv/bin/python scripts/fidus_positioning_probe/probe_h7_roi_census.py --replay /tmp/h7_frames
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import fidus  # noqa: E402
import probe_h3_targetability as H3  # noqa: E402
import probe_h6_dense_gate as H6  # noqa: E402

# ─────────────────────────────────────────────  固定参数（写死在这里，不进输出后再调）
SIZES = (96, 128, 160, 192, 224)
STEP = 32                    # 滑动步长
MIN_COVERAGE = 0.99          # 盒内不透明像素占比下限：让 alpha 口径问题不参与
TOPK_PER_SIZE = 3            # 细半径排名后进入全量复核的候选数
SEARCH_PX = 20               # 跨帧峰值搜索半径（窗内位移的量程上限）
FLOOR = float(np.float32(0.05))   # 引擎是 f32：拿 float64 的 0.05 比会**恒不等**


def fact(label, value) -> None:
    H6.fact(label, value)


def _f(v) -> str:
    return H6._f(v)


# ─────────────────────────────────────────────  候选枚举（只用 alpha + 亮度，不碰引擎）
def integral(arr: np.ndarray) -> np.ndarray:
    """带首行首列 0 的积分图：`ii[y1, x1] - ii[y0, x1] - ...` 即矩形和。"""
    h, w = arr.shape
    out = np.zeros((h + 1, w + 1), dtype=np.float64)
    out[1:, 1:] = np.cumsum(np.cumsum(arr.astype(np.float64), axis=0), axis=1)
    return out


def box_sum(ii: np.ndarray, x0: int, y0: int, w: int, h: int) -> float:
    return float(ii[y0 + h, x0 + w] - ii[y0, x0 + w] - ii[y0 + h, x0] + ii[y0, x0])


def box_positions(mask: np.ndarray, size: int, step: int) -> list[tuple[int, int]]:
    """所有"整盒落在轮廓内"的左上角。用积分图一遍算覆盖率，不做逐盒布尔。"""
    h, w = mask.shape
    if size > min(h, w):
        return []
    ii = integral(mask.astype(np.float64))
    area = float(size * size)
    out: list[tuple[int, int]] = []
    for y0 in range(0, h - size + 1, step):
        for x0 in range(0, w - size + 1, step):
            if box_sum(ii, x0, y0, size, size) / area >= MIN_COVERAGE:
                out.append((x0, y0))
    return out


def fine_worst(plane: np.ndarray) -> float | None:
    """便宜的排名用尺子：只做 fidus 那四个细半径（密铺留给复核档）。None = 结构上不可用。"""
    vals = [v for r in H3.RADII
            for (dx, dy) in ((r, 0), (0, r), (r, r), (r, -r))
            if (v := H3._self_ncc(plane, dx, dy)) is not None]
    return max(vals) if vals else None


def candidates(frame: np.ndarray) -> list[dict]:
    """枚举**可行**候选：轮廓内 + 通过 fidus 结构门，带细半径值供排名。"""
    h, w = frame.shape[:2]
    mask = frame[..., 3] > 8
    ys, xs = np.where(mask)
    if len(xs) < 2:
        return []
    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
    by0, by1 = int(ys.min()), int(ys.max()) + 1
    out: list[dict] = []
    per_size: dict[int, int] = {}
    n_seen = 0
    for size in SIZES:
        for (x0, y0) in box_positions(mask[by0:by1, bx0:bx1], size, STEP):
            (x0, y0) = (x0 + bx0, y0 + by0)
            n_seen += 1
            per_size[size] = per_size.get(size, 0) + 1
            box = np.ascontiguousarray(frame[y0:y0 + size, x0:x0 + size])
            if H3.localizability(box)[0] != "ok":       # 结构门：平坦/过小一律先剔
                continue
            plane = H3.luma709(box[..., :3])
            s_fine = fine_worst(plane)
            if s_fine is None:
                continue
            out.append({"size": size, "x0": x0, "y0": y0, "w": size, "h": size,
                        "s_fine": float(s_fine), "box": box,
                        "off": (x0 + size / 2 - w / 2, y0 + size / 2 - h / 2),
                        "bbox": [bx0, by0, bx1, by1]})
    for c in out:
        c["n_seen"] = n_seen
        c["per_size"] = per_size
    return out


def score_exact(eng, cands: list[dict]) -> list[dict]:
    """每档取细半径 top-K，算全量 `s_engine` 并向引擎对表。就地填字段、返回被复核的子集。"""
    review: list[dict] = []
    for size in SIZES:
        pool = sorted([c for c in cands if c["size"] == size], key=lambda c: c["s_fine"])
        review.extend(pool[:TOPK_PER_SIZE])
    for c in review:
        g = H6.replica_gate(c["box"])
        c["s_eng"], c["cap_replica"] = g["s"], g["cap"]
        c["plan_kept"] = g["plan"]["kept"]
        t0 = time.perf_counter()
        try:
            eng.register_target(c["box"], False)
            c["engine_cap"], c["engine_err"] = float(eng.confidence_ceiling), ""
        except BaseException as exc:  # noqa: BLE001
            c["engine_cap"], c["engine_err"] = None, type(exc).__name__
        c["ms"] = (time.perf_counter() - t0) * 1000.0
        c["eq_replica"] = (c["engine_cap"] is not None
                           and c["engine_cap"] == c["cap_replica"])
    return review


def pick_by_rule(review: list[dict]) -> dict | None:
    """选择键：`s_engine` 升 → 面积降 → 距窗口中心近（第三键只为确定性）。被拒者不参与。"""
    ok = [c for c in review if c["engine_cap"] is not None]
    if not ok:
        return None
    return min(ok, key=lambda c: (c["s_eng"], -(c["w"] * c["h"]),
                                  abs(c["off"][0]) + abs(c["off"][1])))


# ─────────────────────────────────────────────  刚性：固定模板跨帧匹配（不用引擎，自证口径）
def match_peak(tplane: np.ndarray, fplane: np.ndarray, search: int,
               ref_x0: int, ref_y0: int) -> tuple[float, int, int]:
    """`tplane` 在 `fplane` 上找 NCC 峰，位移**以模板当帧的原点 (ref_x0, ref_y0) 为零点**。

    返回 (峰值, dx, dy)。零点取"模板在参考帧里的位置"而不是画面中心，否则 (0,0) 不表示
    "内容没动"，整个 `RIGID` 一问就失去意义。口径与 `H3._self_ncc` 同式；窗口统计走积分图，
    只有互相关项逐位移求和。
    """
    th, tw = tplane.shape
    fh, fw = fplane.shape
    t = tplane.astype(np.float64)
    n = float(t.size)
    mt = t.mean()
    tc = t - mt
    tnorm = float((tc * tc).sum()) ** 0.5
    if tnorm <= 0.0:
        return (-1.0, 0, 0)
    ii_m = integral(fplane)
    ii_s = integral(fplane.astype(np.float64) ** 2)
    tf = t.flatten()
    best = (-1.0, 0, 0)
    for dy in range(-search, search + 1):
        for dx in range(-search, search + 1):
            x0, y0 = ref_x0 + dx, ref_y0 + dy
            if x0 < 0 or y0 < 0 or x0 + tw > fw or y0 + th > fh:
                continue
            m = box_sum(ii_m, x0, y0, tw, th) / n
            s2 = box_sum(ii_s, x0, y0, tw, th) / n
            var = max(s2 - m * m, 0.0)
            if var <= 0.0:
                continue
            win = fplane[y0:y0 + th, x0:x0 + tw].astype(np.float64).reshape(-1)
            num = float((tf * (win - m)).sum())
            v = num / (tnorm * (var * n) ** 0.5)
            if v > best[0]:
                best = (v, dx, dy)
    return best


def rigidity(template_box: np.ndarray, frames: list[np.ndarray],
             ref_x0: int, ref_y0: int) -> list[dict]:
    tp = H3.luma709(template_box[..., :3]).astype(np.float64)
    rows = []
    for i, fr in enumerate(frames):
        if fr.shape[:2] != frames[0].shape[:2]:
            rows.append({"i": i, "peak": None, "dx": None, "dy": None, "why": "尺寸变"})
            continue
        fp = H3.luma709(fr[..., :3]).astype(np.float64)
        peak, dx, dy = match_peak(tp, fp, SEARCH_PX, ref_x0, ref_y0)
        rows.append({"i": i, "peak": peak, "dx": dx, "dy": dy})
    return rows


# ─────────────────────────────────────────────  帧采集 / 落盘
def frames_digest(frames: list[np.ndarray]) -> str:
    body = "|".join(f"{i}={hashlib.sha256(f.tobytes()).hexdigest()[:16]}"
                    for i, f in enumerate(frames))
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def collect_frames(app, n: int) -> tuple[list[np.ndarray], dict]:
    from probe_h3_targetability import render_live2d_frames  # noqa: PLC0415
    frames, rinfo, host, widget = render_live2d_frames(app, n)
    del host, widget
    keep = {"renderer", "window_px", "ready"}
    fact("渲染信息", {k: v for k, v in sorted(rinfo.items()) if k in keep}
         or {k: rinfo[k] for k in list(rinfo)[:4]})
    return frames, rinfo


def dump_frames(dirpath: Path, frames: list[np.ndarray]) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        np.save(dirpath / f"{i:03d}.npy", f, allow_pickle=False)
    (dirpath / "digest.txt").write_text(frames_digest(frames) + "\n", encoding="utf-8")


def load_frames(dirpath: Path) -> list[np.ndarray]:
    return [np.load(p) for p in sorted(dirpath.glob("[0-9][0-9][0-9].npy"))]


# ─────────────────────────────────────────────  各臂
def arm_identity() -> dict:
    print("\n=== 0 · 交付面自证（本探针的一切数字都以锚点为条件）===")
    so = Path(fidus.__file__).with_name("fidus.abi3.so")
    anchor = getattr(fidus, "__git_commit__", None)
    so_sha = hashlib.sha256(so.read_bytes()).hexdigest()
    fact("fidus.__git_commit__", f"{anchor!r} 期望 {H6.ANCHOR!r} ⇒ "
         + ("命中" if anchor == H6.ANCHOR else "✗ 不是本轮的件（结论只能按当轮读）"))
    fact("已装 .so sha256", f"{so_sha[:16]}… 期望 {H6.SO_SHA[:16]}… ⇒ "
         + ("命中" if so_sha == H6.SO_SHA else "✗ 不一致"))
    return {"anchor_ok": anchor == H6.ANCHOR, "so_ok": so_sha == H6.SO_SHA, "anchor": anchor}


def arm_baseline(eng, frames: list[np.ndarray]) -> dict:
    print("\n=== A · 现状基线：`patch`（不透明质心内接 160²）逐帧 ceiling + 质心漂移 ===")
    rows, offs = [], []
    for i, fr in enumerate(frames):
        fed = H3.make_feed(fr, None, "patch")
        off = H3.patch_offset(fr, H3.PATCH_FEED_SIZE)
        offs.append(off)
        g = H6.replica_gate(fed)
        try:
            eng.register_target(fed, False)
            cap, err = float(eng.confidence_ceiling), ""
        except BaseException as exc:  # noqa: BLE001
            cap, err = None, type(exc).__name__
        rows.append({"i": i, "cap": cap, "err": err, "s": g["s"], "off": off})
        fact(f"帧 {i} {fed.shape[1]}×{fed.shape[0]}",
             f"{'REFUSED(' + err + ')' if cap is None else 'ceiling=' + _f(cap)}"
             f"｜s={_f(g['s'])}｜盒中心−窗中心=({off[0]:+.1f}, {off[1]:+.1f}) px")
    good = [r["cap"] for r in rows if r["cap"] is not None]
    at_floor = sum(1 for c in good if c == FLOOR)
    dx = [o[0] for o in offs]
    dy = [o[1] for o in offs]
    spread = (max(dx) - min(dx), max(dy) - min(dy)) if offs else (0.0, 0.0)
    fact("计数", f"接受 {len(good)}/{len(rows)}｜其中 ceiling 正好=地板 {at_floor} 帧")
    fact("质心漂移（盒中心相对窗中心的逐帧极差）", f"{spread[0]:.1f} × {spread[1]:.1f} px")
    return {"rows": rows, "n_ok": len(good), "at_floor": at_floor, "drift": spread}


def arm_rule(eng, frames: list[np.ndarray]) -> list[dict]:
    print("\n=== B · 规则逐帧独立跑（样本外）：每帧各自选 ROI，向引擎对表 ===")
    picks: list[dict] = []
    for i, fr in enumerate(frames):
        cands = candidates(fr)
        if not cands:
            fact(f"帧 {i}", "无可行候选（轮廓内放不下任何一档尺寸）")
            picks.append({"i": i, "pick": None, "n_cand": 0, "n_seen": 0})
            continue
        review = score_exact(eng, cands)
        pick = pick_by_rule(review)
        rec = {"i": i, "n_seen": cands[0]["n_seen"], "n_cand": len(cands),
               "n_review": len(review), "pick": pick, "review": review,
               "per_size": cands[0]["per_size"],
               "s_span": (min(c["s_eng"] for c in review if c["s_eng"] is not None),
                          max(c["s_eng"] for c in review if c["s_eng"] is not None))
               if any(c["s_eng"] is not None for c in review) else None}
        picks.append(rec)
        if pick is None:
            fact(f"帧 {i}", f"看过 {rec['n_seen']} 盒、可行 {rec['n_cand']}、"
                            f"复核 {rec['n_review']} ⇒ **全部被引擎拒**")
            continue
        fact(f"帧 {i}", f"看过 {rec['n_seen']} 盒（按档 {rec['per_size'] or '无'}）、"
                        f"可行 {rec['n_cand']}、复核 {rec['n_review']} ⇒ "
                        f"选中 {pick['size']}²@({pick['x0']},{pick['y0']}) "
                        f"s_eng={_f(pick['s_eng'])}"
                        f"（复核集 s 跨度 {rec['s_span'][1] - rec['s_span'][0]:.4f}）"
                        f" ceiling={_f(pick['engine_cap'])}"
                        f"{'　✗ 复刻与引擎不等' if not pick['eq_replica'] else ''}"
                        f" 保留轴={pick['plan_kept'] or '无'} 注册 {pick['ms']:.1f} ms")
    return picks


def arm_rigid(frames: list[np.ndarray], picks: list[dict]) -> dict:
    print("\n=== C · 刚性：帧 0 选中的盒当固定模板，匹配帧 1..N-1 ===")
    base = picks[0]["pick"] if picks and picks[0].get("pick") else None
    if base is None:
        fact("跳过", "帧 0 未选中任何 ROI")
        return {}
    rows = rigidity(base["box"], frames, base["x0"], base["y0"])
    peaks, dxs, dys = [], [], []
    for r in rows:
        if r["peak"] is None:
            fact(f"帧 {r['i']}", "尺寸变化 ⇒ 不参与")
            continue
        if r["i"] == 0:
            fact("帧 0（自匹配哨兵）", f"峰值={r['peak']:.4f} 位移=({r['dx']:+d},{r['dy']:+d})"
                 "　← 必须 =1 且 (0,0)，否则本函数的 NCC 口径有错")
        else:
            fact(f"帧 {r['i']}", f"峰值={r['peak']:.4f} 峰位移=({r['dx']:+d},{r['dy']:+d}) px")
        if r["i"] > 0:
            peaks.append(r["peak"])
            dxs.append(r["dx"])
            dys.append(r["dy"])
    pb = H3.patch_box(frames[0], H3.PATCH_FEED_SIZE)
    patch_rows = rigidity(H3.make_feed(frames[0], None, "patch"), frames, pb[0], pb[1])
    p_peaks = [r["peak"] for r in patch_rows if r["i"] > 0 and r["peak"] is not None]
    p_dxy = [(r["dx"], r["dy"]) for r in patch_rows if r["i"] > 0 and r["peak"] is not None]
    fact("对照 · 现 patch 同一把尺子",
         f"峰值 {['%.3f' % p for p in p_peaks]} 位移 {p_dxy}")
    return {"rows": rows, "peaks": peaks, "dxs": dxs, "dys": dys,
            "patch_peaks": p_peaks, "patch_dxy": p_dxy}


def arm_anchor(picks: list[dict]) -> dict:
    print("\n=== D · 锚点：各帧独立选中的 ROI 是否落在**同一个**盒上 ===")
    got = [(p["i"], p["pick"]) for p in picks if p.get("pick")]
    if not got:
        fact("跳过", "无选中项")
        return {}
    pos = [(c["x0"], c["y0"], c["size"]) for _i, c in got]
    offs = [c["off"] for _i, c in got]
    same = len(set(pos)) == 1
    fact("各帧选中盒 (x0,y0,size)", f"{pos}"
         + ("　← 三档全同才说明规则不随动画漂" if same else ""))
    fact("是否同一盒", "是 ⇒ 偏移是常量，位置通道可直接换算回窗中心"
         if same else f"否 ⇒ 逐帧选的盒本身在动，跨帧极差 "
         f"{max(o[0] for o in offs) - min(o[0] for o in offs):.0f}×"
         f"{max(o[1] for o in offs) - min(o[1] for o in offs):.0f} px")
    for i, c in got:
        fact(f"  帧 {i} 偏移", f"({c['off'][0]:+.1f}, {c['off'][1]:+.1f}) px")
    mx = max(offs, key=lambda o: abs(o[0]) + abs(o[1]))
    fact("偏移量级", f"首个 ({offs[0][0]:+.1f}, {offs[0][1]:+.1f}) px ⇒ 换算式 "
         "窗中心 = 报告位置 − 该偏移；此常量须在注册时写死并随模板一起存档")
    return {"pos": pos, "same": same, "offs": offs, "max": mx}


# ─────────────────────────────────────────────  收益归因：哪一条约束买到了 ceiling
def nearest_covering(frame: np.ndarray, size: int, cx: int, cy: int,
                     step: int = 4) -> np.ndarray | None:
    """整盒落在轮廓内、且盒心最接近 (cx, cy) 的那一块（不做 argmin 搜索）。"""
    pos = box_positions(frame[..., 3] > 8, size, step)
    if not pos:
        return None
    x0, y0 = min(pos, key=lambda p: (p[0] + size / 2 - cx) ** 2 + (p[1] + size / 2 - cy) ** 2)
    return np.ascontiguousarray(frame[y0:y0 + size, x0:x0 + size])


def engine_read(eng, box: np.ndarray) -> tuple[float | None, str, float]:
    g = H6.replica_gate(box)
    t0 = time.perf_counter()
    try:
        eng.register_target(box, False)
        cap, err = float(eng.confidence_ceiling), ""
    except BaseException as exc:  # noqa: BLE001
        cap, err = None, type(exc).__name__
    ms = (time.perf_counter() - t0) * 1000.0
    return cap if cap is not None else None, err, ms


def arm_ladder(eng, frames: list[np.ndarray]) -> dict:
    print("\n=== E · 收益归因：把 ROI 定义一条条加上去，看 ceiling 是**哪一步**买来的 ===")
    rungs = ("R0 patch160（现状）", "R1 只把尺寸降到 96", "R2 再要求整盒在轮廓内",
             "R3 再在可行盒里取 s 最小（=B 臂的规则）")
    table: dict[str, list[dict]] = {r: [] for r in rungs}
    for i in (0, len(frames) - 1):
        fr = frames[i]
        mask = fr[..., 3] > 8
        ys, xs = np.where(mask)
        cx, cy = int(xs.mean()), int(ys.mean())
        boxes = {rungs[0]: H3.center_patch(fr, 160),
                 rungs[1]: H3.center_patch(fr, 96),
                 rungs[2]: nearest_covering(fr, 96, cx, cy)}
        cands = candidates(fr)
        best = pick_by_rule(score_exact(eng, cands)) if cands else None
        boxes[rungs[3]] = None if best is None else best["box"]
        print(f"  — 帧 {i} —")
        for rung in rungs:
            b = boxes[rung]
            if b is None:
                fact(rung, "放不下（无整盒在轮廓内的位置）")
                table[rung].append({"i": i, "cap": None, "err": "放不下"})
                continue
            cap, err, ms = engine_read(eng, b)
            g = H6.replica_gate(b)
            table[rung].append({"i": i, "cap": cap, "err": err, "s": g["s"], "ms": ms,
                                "shape": (b.shape[1], b.shape[0])})
            fact(f"{rung} {b.shape[1]}×{b.shape[0]}",
                 f"s={_f(g['s'])}｜{'REFUSED(' + err + ')' if cap is None else 'ceiling=' + _f(cap)}"
                 f"｜{ms:.1f} ms")
    return {"table": table, "rungs": rungs}


def arm_freeze(eng, frames: list[np.ndarray], picks: list[dict]) -> dict:
    """把三种产品口径摆在一起：**固定几何盒** / **逐帧 argmin** / **冻结帧 0 模板**。

    逐帧 argmin（B 臂）在样本外把 ceiling 抬离了地板，但如果它**每帧换一个位置**，锚点常量就随帧漂
    ——位置通道被自己污染。本臂量的是"换成固定几何盒（同一坐标每帧重切）会付出多少 s"，
    以及"冻结帧 0 模板"让出多少。前者是唯一不需要迟滞带就能锚点恒定的定义。
    """
    print("\n=== F · 三种口径对照：固定几何盒 / 逐帧 argmin / 冻结帧 0 模板 ===")
    prev = picks[0].get("pick") if picks else None
    if prev is None:
        fact("跳过", "帧 0 未选中")
        return {}
    x0, y0, size = prev["x0"], prev["y0"], prev["size"]
    fact("冻结对象", f"{size}²@({x0},{y0})｜帧 0 ceiling={_f(prev['engine_cap'])}"
         "（同一块数组再注册必然同值，不必重测）")
    gaps, fixed_caps = [], []
    for rec in picks[1:]:
        i, best = rec["i"], rec["pick"]
        box = np.ascontiguousarray(frames[i][y0:y0 + size, x0:x0 + size])
        cap, err, ms = engine_read(eng, box)
        g = H6.replica_gate(box)
        fixed_caps.append(cap)
        line = (f"固定盒在帧 {i}：s={_f(g['s'])}｜"
                + (f"REFUSED({err})" if cap is None else f"ceiling={_f(cap)}")
                + f"｜{ms:.1f} ms")
        if best is not None:
            gap = (g["s"] or 0.0) - best["s_eng"]
            gaps.append(gap)
            line += f"　vs 本帧 argmin {_f(best['s_eng'])} ⇒ 固定盒让出 {gap:+.4f}"
        print(f"  {line}", flush=True)
    off = (x0 + size / 2 - frames[0].shape[1] / 2, y0 + size / 2 - frames[0].shape[0] / 2)
    fact("固定盒的锚点常量", f"({off[0]:+.1f}, {off[1]:+.1f}) px，**按构造逐帧不变**")
    ok_fixed = [c for c in fixed_caps if c is not None and c > FLOOR + 1e-12]
    fact("固定盒逐帧 ceiling", f"{[_f(c) for c in fixed_caps]}｜离地板 {len(ok_fixed)}"
         f"/{len(fixed_caps)}")
    return {"gaps": gaps, "fixed_caps": fixed_caps, "off": off,
            "n_off_floor": len(ok_fixed), "n": len(fixed_caps)}


# ─────────────────────────────────────────────  入口
def main() -> int:
    ap = argparse.ArgumentParser(description="H7: ROI 定义普查（判据化选框）")
    ap.add_argument("--frames", type=int, default=6, help="真实 Live2D 帧数")
    ap.add_argument("--dump", metavar="DIR", help="落盘渲染帧（供跨轮同批对照）")
    ap.add_argument("--replay", metavar="DIR", help="回读落盘帧，零桌面副作用")
    args = ap.parse_args()

    if args.dump and args.replay:
        ap.error("--dump 与 --replay 互斥")

    idy = arm_identity()

    if args.replay:
        frames = load_frames(Path(args.replay))
        if not frames:
            print("✗ 目录里没有 000.npy 形式的帧")
            return 1
        fact("回读", f"{len(frames)} 帧 {frames[0].shape[1]}×{frames[0].shape[0]} "
                    f"指纹={frames_digest(frames)}")
    else:
        from PyQt5.QtWidgets import QApplication  # noqa: PLC0415
        app = QApplication(sys.argv)
        frames, _ = collect_frames(app, args.frames)
        if not frames:
            print("✗ 渲染不可用（模型目录/`_ready`），量具自身失效")
            return 1
        fact(f"渲染 {len(frames)} 帧",
             f"{frames[0].shape[1]}×{frames[0].shape[0]} 指纹={frames_digest(frames)}")
        if args.dump:
            dump_frames(Path(args.dump), frames)
            fact("已落盘", f"{args.dump}（`--replay` 可零副作用重跑本普查）")
            return 0

    eng = fidus.Fidus.build_wayland()
    base = arm_baseline(eng, frames)
    picks = arm_rule(eng, frames)
    rigid = arm_rigid(frames, picks)
    anchor = arm_anchor(picks)
    ladder = arm_ladder(eng, frames)
    froze = arm_freeze(eng, frames, picks)

    got = [p["pick"] for p in picks if p.get("pick")]
    caps = [p["engine_cap"] for p in got]
    off_floor = [c for c in caps if c > FLOOR + 1e-12]
    eq = sum(1 for p in got if p["eq_replica"])
    print("\n[VERDICT]")
    print(f"VERDICT-IDENTITY: 锚点={'命中' if idy['anchor_ok'] else '✗'}"
          f" .so={'命中' if idy['so_ok'] else '✗'} ⇒ 以上全部读数只对 {idy['anchor']} 成立")
    print(f"VERDICT-FRAMES  : {len(frames)} 帧 {frames[0].shape[1]}×{frames[0].shape[0]}"
          f" 指纹={frames_digest(frames)}　（跨轮比较只对同指纹的那批成立）")
    print(f"VERDICT-BASELINE: patch160 接受 {base['n_ok']}/{len(frames)}、"
          f"其中 ceiling=地板 {base['at_floor']} 帧、质心漂移 {base['drift'][0]:.1f}×"
          f"{base['drift'][1]:.1f} px ⇒ "
          + ("现状 ROI 的置信通道确实无信息（调用方要改的就是这一条）"
             if base["at_floor"] == base["n_ok"] and base["n_ok"] else "现状 ROI 并非全在地板"))
    print(f"VERDICT-RULE    : 逐帧独立选框 ⇒ 选中 {len(got)}/{len(frames)} 帧"
          f"、ceiling 离地板 {len(off_floor)}/{len(caps)}："
          + " ".join(f"{c:.4f}" for c in caps)
          + (f"　⇒ conf 通道**有读数**了（基线是 6/6 全等地板），但绝对幅度仍小："
             f"s 落在 {min(p['s_eng'] for p in got):.4f}–{max(p['s_eng'] for p in got):.4f}"
             if got else "　⇒ 无选中"))
    lad = []
    for rung in ladder["rungs"]:
        vals = [_f(t["cap"]) if t["cap"] is not None else t["err"]
                for t in ladder["table"][rung]]
        lad.append(f"{rung.split('（')[0]}→[{','.join(vals)}]")
    print("VERDICT-LADDER  : " + " ".join(lad) + "　⇒ 逐条约束各买了多少见 E 臂，不在此合并")
    if froze:
        print(f"VERDICT-FREEZE  : 固定几何盒（帧 0 那个位置、逐帧重切）ceiling "
              f"{[_f(c) for c in froze['fixed_caps']]}｜离地板 {froze['n_off_floor']}"
              f"/{froze['n']}｜让出幅度 {['%+.4f' % g for g in froze['gaps']]}"
              f"｜锚点常量 {froze['off'][0]:+.1f},{froze['off'][1]:+.1f} px 按构造不变 ⇒ "
              + ("固定盒即可，不需要 argmin 搜索（搜索的收益低于它带来的锚点漂移）"
                 if froze["gaps"] and max(froze["gaps"]) < 0.01
                 else "搜索确有收益，产品侧须配迟滞带才能定住锚点"))
    print(f"VERDICT-REPLICA : 复刻(s_engine) 与引擎 ceiling 逐位等 {eq}/{len(got)} ⇒ "
          + ("选框可全程 CPU 完成，最后一步才问引擎" if got and eq == len(got)
             else "✗ 有帧不等 ⇒ 锚点漂移或公式失配"))
    if rigid.get("rows"):
        pk = rigid["peaks"]
        dr = [abs(d) for d in rigid.get("dxs", [])] + [abs(d) for d in rigid.get("dys", [])]
        print(f"VERDICT-RIGID   : 帧 0 ROI 跨帧峰值 {['%.3f' % p for p in pk]}"
              f" 位移 {rigid.get('dxs')}×{rigid.get('dys')} px｜patch 对照峰值 "
              f"{['%.3f' % p for p in rigid.get('patch_peaks', [])]}"
              + ("　⚠ 选中项位于会自己动的内容上：低歧义≠可用锚"
                 if dr and max(dr) > 3 else "　⇒ 该 ROI 在窗内近似刚性"))
    else:
        print("VERDICT-RIGID   : 未测（帧 0 无选中 ROI）")
    if anchor:
        print(f"VERDICT-ANCHOR  : 各帧选中同一盒 = {anchor['same']}"
              f"｜偏移 {['(%+.0f,%+.0f)' % o for o in anchor['offs']]}"
              + ("　⇒ 常量可写死，位置通道口径自洽" if anchor["same"]
                 else "　⇒ 选框规则本身随动画漂，须先定住再做换算"))
    ms = [p["ms"] for p in got]
    print(f"VERDICT-COST    : 选中盒注册 {['%.1f' % m for m in ms]} ms（best-of-1，含选框后单次）"
          + ("　⇒ 远超 2 ms tick，重注册只能离帧调度" if ms and max(ms) > 2.0 else ""))
    print(f"VERDICT-NOTTESTED: 新 ROI 的**屏上**首锁、空桌面误锁峰值、位移后跟随 **本探针未测**"
          "　⇒ 上线判据不能只凭本件")
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())
