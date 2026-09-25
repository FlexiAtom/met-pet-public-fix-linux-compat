#!/usr/bin/env python3
"""H5 · fidus 新公共读面 `confidence_ceiling` 的验真 + "门只采样 ≤16 px" 盲区的**实锁测试**

出处：fidus 入站件 `~/.Athena/projects/meapet/pool/fidus-ceiling-and-settle-answer.md`
（§1 第三条、追加 D/F、H-2）。该件把 `Fidus.confidence_ceiling` 作为正式公共面交付，并建议
我方把 §12h 的宿主复刻链**降级为交叉校验**；同时它自己登记了一条盲区：注册期自相似度只在
平移半径 **2/4/8/16 px** 上采样 ⇒ 周期 >16 px 的模板可以注册成功且 ceiling 看着很高，而
"这种模板究竟锁到真位还是锁到偏移孪生峰"——**fidus 侧明写未测**。

五问（每问一条可判真伪的判据）：
* **D 只读冒烟**：锚点是否 `v0.1.0-beta.1-43-gcf46078`；公共面是否**只多**一个
  `confidence_ceiling`（对拍 `dir()` 差集）；注册前该值是否 `None`。
* **A 同一性**：官方 ceiling 与宿主复刻 `f32(max(1−s, 0.05))` 是否**逐位**同值？
  等 ⇒ 复刻链可降级为交叉校验（fidus 建议成立）；某格不等 ⇒ 复刻不忠实，§12h 结论要改。
* **B 盲区存在性**：造一个"右半复制左半"的模板 ⇒ 它在 lag 64 处有**精确孪生峰**，
  但门的窗口（≤16 px）看不见。测它的 ceiling 有多高。
* **C 实锁（本探针的主菜，fidus 未测的那半）**：把 B 那块真的上屏、标定、连读 `estimate()`。
  读数落在真位 ⇒ 盲区是**软告警**；读数落在 ±64 px 的孪生位 ⇒ 盲区是**硬失效**，
  "判稳看相邻两条互差"这条规则**救不了它**（锁到孪生峰后相邻两条可以完全一致）。
* **C3 等分峰实锁**：C 的窗口尺寸＝模板尺寸 ⇒ 孪生位落在窗口边上、**并非**真等分峰。C3 把屏上内容
  换成 4 份平铺（模板仍是同一块 `[L|L]`）⇒ 三个峰的图像块**逐字节相同**，外观无法区分。
  三相（首锁 / 亚周期位移 +20 px / 移回）各连读若干条，看锁的是同一个峰还是会跳。
  对应 fidus 池 `ceiling-metric-lag-window` 的"未证-1"。

真实宠物帧普查（`--real`）：产品自己的 Live2D 帧里有没有 B 那一格。

用法：
```
WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \
  .venv/bin/python scripts/fidus_positioning_probe/probe_h5_ceiling_surface.py
```
可选 `--real N`（普查 N 帧真实动画）、`--no-twin`、`--no-field`、`--est K`。

失效边界：本机单输出 scale=1.0、niri；`--no-twin` 时全程**不标定、不上屏 ⇒ 零输出 mutation**
（`register_target` 收的是调用方自持的离屏渲染，docstring 原文 "the caller's own offscreen render"）。
C 问只主张"这一张周期性模板在这一台机器上的锁点"，不主张 fidus 的搜索顺序在所有素材上都如此。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(HERE), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PyQt5.QtWidgets import QApplication  # noqa: E402

import fidus  # noqa: E402
import probe_a10_geometry as A10  # noqa: E402
import probe_h1_coexistence as H1  # noqa: E402
import probe_h3_targetability as H3  # noqa: E402
import probe_h4_ceiling_infer as H4  # noqa: E402

ANCHOR = "v0.1.0-beta.1-47-gf297a54"   # 本件重跑时的锚点；换轮必改并全量重跑（§12h-3）
GATE_RADII = (2, 4, 8, 16)          # fidus 注册期门的采样半径（其文档原文）
BEYOND_RADII = (20, 24, 32, 48, 64, 96)  # 门**看不见**的那些 lag
CEIL_FLOOR = 0.05                   # 与 fidus `fidus-estimate/src/lib.rs` 同值，只用于复算
SCREEN_OK_PX = 3.0                  # 独立尺子判"真位就在这里"


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.17g}" if isinstance(v, float) else str(v)


def rgba_of(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    return np.ascontiguousarray(
        np.concatenate([rgb, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
    )


# ─────────────────────────────────────────────  模板池
def pat_halves(seed: int = 4242) -> np.ndarray:
    """128×64，右半**逐像素复制**左半 ⇒ lag 64 处 NCC 恰为 1.0（精确孪生峰），而门只看 ≤16 px。"""
    rng = np.random.default_rng(seed)
    half = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    return rgba_of(np.concatenate([half, half], axis=1))


def pat_tile(period: int = 16, seed: int = 99) -> np.ndarray:
    """周期 `period` px 的随机纹样平铺 + 一个唯一标记块：结构上重复，但门的采样恰好撞上它。"""
    rng = np.random.default_rng(seed)
    tile = rng.integers(0, 256, size=(period, period, 3), dtype=np.uint8)
    reps = 200 // period
    body = np.tile(tile, (reps, reps, 1))[:200, :200]
    body[4:28, 4:28] = rng.integers(0, 256, size=(24, 24, 3), dtype=np.uint8)  # 唯一标记
    return rgba_of(body)


def pat_plain(seed: int = 20260924) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rgba_of(rng.integers(0, 256, size=(200, 200, 3), dtype=np.uint8))


CASES: dict[str, tuple[str, object]] = {
    "a10": ("A10 原图（§12h 逐位命中的那条）", lambda: rgba_of(A10.make_pattern()[..., :3])),
    "plain": ("逐像素噪声（无结构）", pat_plain),
    "blocks": ("7×7 随机色块 ×32px（§12h 锚点之一）", H4.pat_blocks),
    "tile16": ("16 px 平铺 + 唯一标记（门的采样窗刚好够着周期）", pat_tile),
    "halves": ("128×64 右半复制左半（周期 64 px，门**看不见**）", pat_halves),
}


# ─────────────────────────────────────────────  独立尺子（宿主侧，不经 fidus）
def replica(rgba: np.ndarray) -> tuple[str, float | None, float | None, dict | None]:
    """复刻链给出**两把**尺子：旧"仅细半径"（verdict / s / 预测 ceiling）+ 新"含密铺保留轴"。

    自 `47-gf297a54` 起引擎判据含轴向密铺扫描 ⇒ 只报旧尺子会把"副本落后于引擎"误读成
    "引擎改了度量"（§12m.3 实测：旧尺子在 `plain`/`halves` 上系统性偏高）。
    两把都留，是因为"旧副本何时开始失真"本身就是这条换轮欠账要回填的数。
    `H6` 惰性导入：它在模块级 `import probe_h5_ceiling_surface`，写在文件头会成环。
    """
    import probe_h6_dense_gate as H6  # noqa: PLC0415
    verdict, worst, _per = H3.localizability(rgba)
    if worst is None:
        return verdict, None, None, None
    return (verdict, worst, float(np.float32(max(1.0 - worst, CEIL_FLOOR))),
            H6.replica_gate(rgba))


def beyond_gate(rgba: np.ndarray) -> list[tuple[int, float]]:
    """在门**采不到**的 lag 上量自相似度：越大 ⇒ 越存在门看不见的孪生峰。"""
    plane = H3.luma709(rgba[..., :3])
    out: list[tuple[int, float]] = []
    for r in BEYOND_RADII:
        vals = [v for (dx, dy) in ((r, 0), (0, r), (r, r), (r, -r))
                if (v := H3._self_ncc(plane, dx, dy)) is not None]
        if vals:
            out.append((r, max(vals)))
    return out


def truth_center(pattern: np.ndarray) -> tuple[float, float] | None:
    """grim 抓屏 + 宿主侧 NCC ⇒ 图案在屏幕上的**真实中心**（绝对基准，不经 fidus）。"""
    shot = A10.capture("h5_truth")
    if shot is None:
        return None
    peak, x, y = A10.Correlator(H3.luma709(shot[..., :3])).peak(H3.luma709(pattern[..., :3]))
    if peak < 0.5:
        return None
    h, w = pattern.shape[:2]
    return x + w / 2.0, y + h / 2.0


# ─────────────────────────────────────────────  各问
def arm_surface(cases: list[str]) -> dict:
    print("\n=== D · 只读冒烟（锚点 / 公共面差集 / 注册前 None）===")
    anchor = getattr(fidus, "__git_commit__", None)
    fact("fidus.__git_commit__", f"{anchor!r} 期望 {ANCHOR!r} ⇒ "
         + ("命中" if anchor == ANCHOR else "✗ 不是本件的件"))
    eng = fidus.Fidus.build_wayland()
    fact("注册前 confidence_ceiling", repr(eng.confidence_ceiling))
    none_before = eng.confidence_ceiling is None
    cls = {n for n in dir(fidus.Fidus) if not n.startswith("_")}
    fact("类公共面", sorted(cls))
    has_getter = "confidence_ceiling" in cls
    return {"eng": eng, "anchor_ok": anchor == ANCHOR, "none_before": none_before,
            "has_getter": has_getter, "cases": cases}


def reg(eng, rgba: np.ndarray, amb: bool = False) -> tuple[float | None, str | None]:
    """注册并回读 ceiling，返回 `(官方 ceiling, 备注)`。备注口径三种，**调用方别合并**：

    * `None` —— 干净注册。
    * `"REFUSED:X"` —— 被门拒且未走通逃逸门（`--amb` 关，或开了仍被拒）⇒ 本格无 ceiling 可读。
    * `"AMBIG:X"` —— 被拒后**走 `ambiguous=True` 注册成功** ⇒ ceiling 有值但被压到地板，
      conf 通道同时失效（见下面的 docstring）。

    `--amb` 存在的理由：C2/C3 两臂的模板（`halves` 与 4 份平铺场）在新判据下**从可注册变成被拒**，
    不带这个旋钮那两臂就整体跑不了 ⇒ "旧轮答过的问题"在新轮变成"没答过"。
    但开了它，实锁读数**只回答"锁到哪"，不回答"conf 有没有告警"** ⇒ 两种口径都留、分开记，
    混成一行就是假结论。
    """
    try:
        eng.register_target(rgba, False)
        return eng.confidence_ceiling, None
    except BaseException as exc:  # noqa: BLE001  被门拒就是一格判决
        name = type(exc).__name__
        # 顺手证一件契约相关的物**：注册抛异常后 getter 报的是**上一次的** ceiling 还是 None。
        # 若仍报旧值，消费方"注册失败 ⇒ 读 ceiling 判断该不该用"这条路就是错的（会拿到陈旧上界）。
        try:
            stale = eng.confidence_ceiling
        except BaseException as exc3:  # noqa: BLE001
            stale = f"读面也抛 {type(exc3).__name__}"
        fact("  被拒后 getter", f"{stale!r} ⇒ "
             + ("注册失败后 getter **仍报上一次的值** ⇒ 不可拿它判『该不该用这条读数』"
                if isinstance(stale, float) else
                "getter 回到 None ⇒ 可当失败信号" if stale is None else str(stale)))
        if not amb:
            return None, f"REFUSED:{name}"
        try:
            eng.register_target(rgba, True)
        except BaseException as exc2:  # noqa: BLE001
            return None, f"REFUSED:{name}→{type(exc2).__name__}"
        return eng.confidence_ceiling, f"AMBIG:{name}"


def arm_identity(eng, cases: dict[str, np.ndarray], amb: bool = False) -> list[dict]:
    print("\n=== A/B · 官方 ceiling vs 宿主复刻（旧仅细半径 / 新含密铺）+ 门盲区扫描 ===")
    rows: list[dict] = []
    for key, rgba in cases.items():
        verdict, s, cap_fine, dense = replica(rgba)
        cap_new = None if dense is None else dense["cap"]
        off, note = reg(eng, rgba, amb)
        pred_refused = None if dense is None else dense["refused"]
        if note and note.startswith("REFUSED"):
            fact(key, f"注册未成功 ⇒ {note}（--amb {'关' if not amb else '开'}；"
                      f"新复刻预判 refused={pred_refused}）")
            rows.append({"key": key, "cap": cap_fine, "cap_new": cap_new, "refused": note,
                         "pred_refused": pred_refused})
            continue
        beyond = beyond_gate(rgba)
        worst_beyond = max((v for _r, v in beyond), default=None)
        flag = (off is not None and off >= 0.9
                and worst_beyond is not None and worst_beyond >= 0.98)
        eq_old = off == cap_fine
        eq_new = off == cap_new
        fact(f"[{key}]", f"复刻 s={_fmt(s)} verdict={verdict} 旧预测={_fmt(cap_fine)} "
                         f"新预测={_fmt(cap_new)} 官方={_fmt(off)} ⇒ "
                         + ("两把都对" if eq_new and eq_old else
                            "仅新对（旧副本已失真）" if eq_new else
                            "仅旧对（本格密铺未命中）" if eq_old else "两把都不对 ✗")
                         + (f"　[{note}]" if note else ""))
        fact(f"[{key}] 门盲区 lag>16 自相似", f"{[(r, round(v, 4)) for r, v in beyond]}"
             + ("　⚠ 盲区命中（ceiling 高 + 存在近 1.0 的窗外孪生峰）" if flag else ""))
        rows.append({"key": key, "cap": cap_fine, "cap_new": cap_new, "official": off,
                     "eq": eq_new, "eq_old": eq_old, "note": note,
                     "at": None if dense is None else dense["at"],
                     "worst_beyond": worst_beyond, "blindspot": flag})
    return rows


def arm_real(eng, app: QApplication, n: int, amb: bool = False) -> list[dict]:
    print(f"\n=== C1 · 真实宠物帧普查（产品自己的几何算法渲染 {n} 帧）===")
    frames, rinfo, host, widget = _render(app, n)
    fact("渲染信息", {k: v for k, v in sorted(rinfo.items())
                     if k in ("renderer", "window_px", "ready")})
    fact("本臂与 H6 的分工", "换轮对帧集的**权威**普查在 `probe_h6_dense_gate.py --dump/--replay`"
         "（那件用落盘字节做同批 A/B，本件跨进程渲染不可复现）。本臂只补 H6 不算的那一列："
         "lag>16 的窗外次峰 ⇒ 盲区命中。")
    rows: list[dict] = []
    for i, frame in enumerate(frames):
        row = {"key": f"frame{i}"}
        # 喂法与 H3 同一批：裁外接框**不够**（H3 实测 alpha>0 区内仍有大片平坦透明 ⇒ 被门拒），
        # 所以 raw / alpha_crop / patch 三种喂法各量一遍，看真实素材的 ceiling 落在哪种口径上。
        for variant in ("raw", "alpha_crop", "patch"):
            fed = H3.make_feed(frame, None, variant)
            verdict, s, cap_fine, dense = replica(fed)
            cap_new = None if dense is None else dense["cap"]
            off, note = reg(eng, fed, amb)
            if note and note.startswith("REFUSED"):
                lbl = f"帧{i} {variant} {fed.shape[1]}x{fed.shape[0]}"
                fact(lbl, f"{note}（复刻 verdict={verdict} s={_fmt(s)}）")
                # 单独记一行：VERDICT-REFUSED 要数到**格**而不是只数 patch 喂法，
                # 否则会漏报"raw/alpha_crop 被拒、patch 可注册"这种最常见的形态。
                rows.append({"key": lbl, "cap": cap_fine, "cap_new": cap_new,
                             "refused": note, "pred_refused": dense["refused"]})
                continue
            worst_beyond = max((v for _r, v in beyond_gate(fed)), default=None)
            flag = (off is not None and off >= 0.9
                    and worst_beyond is not None and worst_beyond >= 0.98)
            fact(f"帧{i} {variant} {fed.shape[1]}x{fed.shape[0]}",
                 f"ceiling={_fmt(off)}（旧复刻={_fmt(cap_fine)} "
                 f"{'等' if off == cap_fine else '不等'}／新复刻={_fmt(cap_new)} "
                 f"{'等' if off == cap_new else '不等'}） 窗外次峰={_fmt(worst_beyond)}"
                 + ("　⚠ 盲区命中" if flag else "") + (f"　[{note}]" if note else ""))
            if variant == "patch":
                row.update({"cap": cap_fine, "cap_new": cap_new, "official": off,
                            "eq": off == cap_new, "eq_old": off == cap_fine,
                            "worst_beyond": worst_beyond, "blindspot": flag})
        rows.append(row)
    del host, widget
    return rows


def _render(app: QApplication, n: int):
    from probe_h3_targetability import render_live2d_frames  # noqa: PLC0415
    return render_live2d_frames(app, n)


def arm_twin(eng, app: QApplication, template: np.ndarray, est: int,
             amb: bool = False) -> dict:
    """fidus 明写"未测"的那半：窗外有精确孪生峰的模板，实际锁到哪。"""
    print("\n=== C2 · 孪生峰实锁（上屏 + 标定 ⇒ 本问属输出 mutation）===")
    h, w = template.shape[:2]
    px, py = 480, 300
    win = H4.TargetWindow(A10.qimage_from(template))
    win.move(px, py)
    win.show()
    H4.pump(app, 900)
    truth = truth_center(template)
    if truth is None:
        fact("独立尺子", "✗ 抓屏里找不到图案 ⇒ 本问不产出")
        return {"verdict": "no-truth"}
    fact("独立尺子（grim+NCC 真中心）", f"({truth[0]:.2f}, {truth[1]:.2f})")
    off, note = reg(eng, template, amb)
    if note and note.startswith("REFUSED"):
        fact("本臂中止", f"{note} ⇒ 未走通注册，**不读 ceiling 也不 estimate**"
             "（读到的会是上一次注册的陈旧值）。要答这问请加 --amb。")
        win.close()
        H4.pump(app, 120)
        return {"verdict": "refused"}
    fact("注册后官方 ceiling", f"{_fmt(off)}" + (f"　[{note}]" if note else ""))
    try:
        cal = eng.calibrate_once()
        fact("calibrate_once", f"scale={getattr(cal, 'scale', '?')}")
    except BaseException as exc:  # noqa: BLE001
        fact("calibrate_once", f"RAISED ⇒ {type(exc).__name__}")
        return {"verdict": "calibrate-failed"}
    errs: list[tuple[float, float]] = []
    confs: list[float] = []
    for _ in range(est):
        x, y, c = eng.estimate()
        confs.append(float(c))
        errs.append((float(x) - truth[0], float(y) - truth[1]))
        H4.pump(app, 260)
    fact(f"estimate ×{est} 相对真位的误差", [(round(dx, 2), round(dy, 2)) for dx, dy in errs])
    advertised = eng.confidence_ceiling
    fact("advertised == applied（同进程、真机匹配帧）",
         f"官方 ceiling={_fmt(advertised)} 观察 conf={[_fmt(c) for c in confs]} ⇒ "
         + ("逐位相等 ⇒ 注册期公告的上界就是运行期实际施加上界"
            if advertised in confs else
            "✗ 观察 conf 不等于公告 ceiling ⇒ 要么未被钳（conf 是真实测量置信），"
            "要么 advertised≠applied（那是对 fidus `fused_sim` 断言的外部反证）"))
    step = w / 2.0  # 128×64 两半 ⇒ 孪生位偏移 = 半宽 = 64 px
    twin = [i for i, (dx, dy) in enumerate(errs) if abs(abs(dx) - step) <= 2.0 and abs(dy) <= 2.0]
    home = [i for i, (dx, dy) in enumerate(errs)
            if abs(dx) <= SCREEN_OK_PX and abs(dy) <= SCREEN_OK_PX]
    out = {"verdict": ("locked-home" if home and not twin else
                       "locked-twin" if twin else "neither"),
           "home": home, "twin": twin, "step_px": step}
    fact("判读", f"落在真位的读数 {len(home)}/{len(errs)}、落在 ±{step:.0f} px 孪生位的 "
                f"{len(twin)}/{len(errs)} ⇒ {out['verdict']}")
    fact("本问的**分辨率限制**", "窗口尺寸==模板尺寸 ⇒ 孪生位落在**窗口边上**、并非等分峰。"
                                "⇒ 这一格只答'首锁在有无先验时去哪'，不答'多个等分峰时锁哪个'（那是 C3）。")
    win.close()
    H4.pump(app, 120)
    return out


def pat_field(copies: int = 4, period: int = 64, seed: int = 4242):
    """屏上放 `copies` 份同一个块、模板只取其中 2 份 ⇒ 屏上有 `copies−1` 个**逐字节相同**的峰。

    与 `pat_halves` **同一个 `seed`、同一个块** ⇒ 模板完全相同（旧轮官方 ceiling
    `0.97752565145492554`；`47-gf297a54` 起该模板**被门拒**，要靠 `--amb` 才注册得上，见 §12m.3），
    区别只在**屏上内容**：C2 里窗口只有模板那么大 ⇒ 孪生位在窗口边上、并非等分峰；
    C3 里窗口是 4 份平铺 ⇒ 峰与峰之间**外观上不可区分**，任何外观度量都选不出唯一答案。
    """
    rng = np.random.default_rng(seed)
    blk = rng.integers(0, 256, size=(period, period, 3), dtype=np.uint8)
    field = rgba_of(np.concatenate([blk] * copies, axis=1))
    tmpl = rgba_of(np.concatenate([blk, blk], axis=1))
    return field, tmpl


def arm_field(eng, app: QApplication, est: int, shift_px: float = 20.0,
              amb: bool = False) -> dict:
    """fidus 池 `ceiling-metric-lag-window` 未证-1 的那半：屏上有**逐字节相同**的多峰时锁哪、会不会跳。

    两处刻意的设计：
    * **位移走 layer 门面 `set_position`，不走 Qt `move()`**——本轮实测 `move()` 被 niri 忽略
      （请求 (480,300) 的窗被摆在屏幕正中 (683,384)），拿它当"真位移"会造出一条假读数。
    * **每一相都用独立尺子重测表面位置**，不拿"请求位移"当真值 ⇒ 位移没生效会被立刻看见。
    """
    print("\n=== C3 · 屏上有多个**逐字节相同**的峰时的实锁（上屏 + 标定 ⇒ 属输出 mutation）===")
    field, tmpl = pat_field()
    period = 64
    th, tw = tmpl.shape[:2]
    fh, fw = field.shape[:2]
    px, py = 300, 220
    n_peak = fw // period - 1
    host = H1.HostWindow()
    host.show()
    H4.pump(app, 600)
    backend = H1.FACADE.get_backend()
    backend.enable(host, fw, fh, px, py)
    qf = H1.qimage_from(field)
    backend.update_pixels(qf)
    H4.pump(app, 1200)
    f_true = None
    for i in range(4):  # 门面首帧缺陷（§12j-5 / §12k-5）：未见就再提交一帧
        f_true = truth_center(field)
        if f_true is not None:
            break
        fact("可见性", f"第 {i + 1}/4 次未见 ⇒ 再提交一帧")
        backend.update_pixels(qf)
        H4.pump(app, 1200)
    if f_true is None:
        fact("独立尺子", "✗ 屏幕上找不到场 ⇒ 本问不产出")
        backend.destroy_context()
        return {"verdict": "no-truth"}

    def cands(left: float) -> list[float]:
        return [left + k * period + tw / 2.0 for k in range(n_peak)]

    def measure(tag: str) -> tuple[float | None, float | None]:
        t = truth_center(field)
        left = None if t is None else t[0] - fw / 2.0
        fact(tag, f"独立尺子实测表面左缘 = {'—' if left is None else f'{left:.2f}'}"
                  "（每相重测，不采信请求位移）")
        return left, (None if t is None else t[1])

    left0, cy0 = measure("首相")
    if left0 is None or cy0 is None:
        fact("独立尺子", "✗ 首相就找不到场 ⇒ 本问不产出")
        backend.destroy_context()
        return {"verdict": "no-truth"}
    shot = A10.capture("h5_field_cands")
    crops = []
    if shot is not None and left0 is not None:
        for cx in cands(left0):
            x0, y0 = int(round(cx - tw / 2.0)), int(round(cy0 - th / 2.0))
            crops.append(shot[y0:y0 + th, x0:x0 + tw, :3])
    ident = len(crops) > 1 and all(np.array_equal(crops[0], c) for c in crops[1:])
    fact("独立尺子", f"场左缘={left0:.2f} ⇒ 峰候选中心 x = {[round(c, 2) for c in cands(left0)]}")
    fact("这些峰**是否真的等分**", f"屏上对应图像块逐字节相同 = {ident}"
         + ("　⇒ 外观上不可区分：选哪一个只能来自搜索顺序/先验，与内容无关" if ident else
            "　⇒ ✗ 块不相同，下面的分类要打折"))

    off, note = reg(eng, tmpl, amb)
    if note and note.startswith("REFUSED"):
        fact("本臂中止", f"{note} ⇒ 未走通注册，**不读 ceiling 也不 estimate**"
             "（读到的会是上一次注册的陈旧值）。要答这问请加 --amb。")
        backend.destroy_context()
        return {"verdict": "refused"}
    fact("注册后官方 ceiling", f"{_fmt(off)}" + (f"　[{note}]" if note else ""))
    try:
        cal = eng.calibrate_once()
        fact("calibrate_once", f"scale={getattr(cal, 'scale', '?')}")
    except BaseException as exc:  # noqa: BLE001
        fact("calibrate_once", f"RAISED ⇒ {type(exc).__name__}")
        backend.destroy_context()
        return {"verdict": "calibrate-failed"}

    def phase(label: str, left: float) -> list[dict]:
        cs = cands(left)
        rows: list[dict] = []
        for _ in range(est):
            try:
                x, y, c = eng.estimate()
            except BaseException as exc:  # noqa: BLE001
                fact(f"  {label}", f"estimate 抛 {type(exc).__name__}")
                continue
            k = min(range(len(cs)), key=lambda i: abs(float(x) - cs[i]))
            rows.append({"k": k, "resid": float(x) - cs[k], "x": float(x), "conf": float(c)})
            fact(f"  {label}", f"读数=({float(x):.2f},{float(y):.2f}) ⇒ 峰 #{k} "
                              f"对该峰残差={float(x) - cs[k]:+.2f} px conf={_fmt(float(c))}")
            H4.pump(app, 240)
        return rows

    p1 = phase("相1·首锁", left0)
    backend.set_position(int(round(px + shift_px)), py)
    H4.pump(app, 900)
    left1, _ = measure("相2")
    real_shift = (left1 - left0) if (left1 is not None and left0 is not None) else None
    fact("位移是否真生效", f"请求 +{shift_px:.0f} px ⇒ 独立尺子实测表面位移 "
         f"{'—' if real_shift is None else f'{real_shift:+.2f} px'}")
    p2 = phase("相2·亚周期位移后", left1 if left1 is not None else left0)
    backend.set_position(px, py)
    H4.pump(app, 900)
    left2, _ = measure("相3")
    p3 = phase("相3·移回后", left2 if left2 is not None else left0)
    backend.destroy_context()

    seq = p1 + p2 + p3
    if not seq:
        return {"verdict": "no-readings"}
    idx = [r["k"] for r in seq]
    rmax = max(abs(r["resid"]) for r in seq)
    verdict = ("tied-peak-hop" if len(set(idx)) > 1 else
               "tied-peak-offset" if rmax > SCREEN_OK_PX else "tied-peak-stable")
    out = {"verdict": verdict, "peaks": idx, "ident": bool(ident), "resid_max_abs": rmax}
    fact("判读", f"三相峰序号 {[r['k'] for r in p1]} / {[r['k'] for r in p2]} / {[r['k'] for r in p3]}"
                f"；对最近峰残差最大 {rmax:.2f} px ⇒ {verdict}")
    if p1 and p2 and real_shift is not None:
        got = p2[-1]["x"] - p1[-1]["x"]
        fact("跟得住吗（稳态读数差 vs 实测表面位移）",
             f"读数差 {got:+.2f} px vs 表面位移 {real_shift:+.2f} px ⇒ "
             + ("同峰同号 ⇒ 位移被如实跟随" if abs(got - real_shift) <= SCREEN_OK_PX else
                f"差 {got - real_shift:+.2f} px ⇒ 未如实跟随（跳锁或漏跟）"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="H5: ceiling 读面验真 + 门盲区实锁")
    ap.add_argument("--real", type=int, default=0, help="普查几帧真实 Live2D 动画（0=跳过）")
    ap.add_argument("--no-twin", action="store_true",
                    help="跳过孪生峰实锁（那问要上屏+标定）；不跳则全程零输出 mutation")
    ap.add_argument("--est", type=int, default=5, help="孪生实锁连读几条")
    ap.add_argument("--no-field", action="store_true",
                    help="跳过 C3（屏上多等分峰实锁）")
    ap.add_argument("--amb", action="store_true",
                    help="被门拒的模板改走 register_target(img, ambiguous=True) 再跑 C2/C3"
                         "（ceiling 落地板 ⇒ 读数只答『锁到哪』，不答『conf 有没有告警』）")
    args = ap.parse_args()

    app = QApplication(sys.argv)
    cases = {k: np.ascontiguousarray(mk()) for k, (_lbl, mk) in CASES.items()}
    d = arm_surface(list(cases))
    eng = d["eng"]
    rows = arm_identity(eng, cases, args.amb)
    real_rows = arm_real(eng, app, args.real, args.amb) if args.real else []
    twin = {"verdict": "skipped"}
    field = {"verdict": "skipped"}
    if not args.no_twin:
        twin = arm_twin(eng, app, cases["halves"], args.est, args.amb)
        if not args.no_field:
            field = arm_field(eng, app, args.est, amb=args.amb)

    all_rows = rows + real_rows
    eq_n = sum(1 for r in all_rows if r.get("eq"))
    reg_n = sum(1 for r in all_rows if "official" in r)
    blind = [r["key"] for r in all_rows if r.get("blindspot")]
    print("\n[VERDICT]")
    surface_ok = d["anchor_ok"] and d["has_getter"] and d["none_before"]
    print(f"VERDICT-SURFACE : anchor={'命中' if d['anchor_ok'] else '✗'} "
          f"getter存在={d['has_getter']} 注册前None={d['none_before']} "
          f"⇒ {'公共读面如期交付' if surface_ok else '✗ 与件不符'}")
    print(f"VERDICT-IDENTITY: 官方 ceiling 与宿主复刻逐位相等 {eq_n}/{reg_n}"
          + ("　⇒ 复刻链可降级为交叉校验（fidus 建议成立）" if eq_n == reg_n and reg_n else
             "　⇒ ✗ 有格不等，§12h'宿主可自算'须限定"))
    print(f"VERDICT-BLINDSPOT: 窗外（lag>16 px）存在近 1.0 孪生峰而 ceiling 仍 ≥0.9 的格 = "
          f"{blind or '无'} ⇒ 盲区{'确实可命中（fidus D/F 告警在我方素材上落地）' if blind else '本次未命中'}")
    stale = [r["key"] for r in all_rows if "official" in r
             and not r.get("eq_old") and r.get("eq")]
    both_bad = [r["key"] for r in all_rows if "official" in r
                and not r.get("eq_old") and not r.get("eq")]
    print(f"VERDICT-STALE  : 旧「仅细半径」副本失真而新副本命中的格 = {stale or '无'}"
          f"　⇒ {'换轮后只有含密铺的复刻能当交叉校验（§12m.3 在本件复现）' if stale else '本组素材未命中密铺加严'}")
    if both_bad:
        print(f"VERDICT-STALE  : ✗ 两把副本都不等的格 = {both_bad} ⇒ 复刻链本身要重看，别归因给判据")
    refused = [(r["key"], r["refused"], r.get("pred_refused"))
               for r in all_rows if r.get("refused")]
    amb_used = [r["key"] for r in all_rows if str(r.get("note", "")).startswith("AMBIG")]
    print(f"VERDICT-REFUSED: 被门拒的格 = {[(k, n) for k, n, _p in refused] or '无'}"
          f"　宿主侧预判 refused 命中 = {sum(1 for _k, _n, p in refused if p is True)}/{len(refused)}"
          + (f"　其中走 --amb 才注册上的 = {amb_used}" if amb_used else ""))
    print(f"VERDICT-TWIN   : {twin.get('verdict')} "
          + {"locked-home": "⇒ 锁在真位：盲区是**软告警**（ceiling 别当唯一凭据即可）",
             "locked-twin": "⇒ 锁在孪生位：盲区是**硬失效**，'看相邻两条互差'救不了它",
             "neither": "⇒ 既非真位也非孪生位，须回头看读数",
             "skipped": "⇒ 本次跳过（--no-twin）：fidus 那句'未测'仍开放",
             "refused": "⇒ 模板在新判据下被拒且未开 --amb ⇒ 本问**未答**，加 --amb 重跑",
             }.get(str(twin.get("verdict")), ""))
    fv = str(field.get("verdict"))
    fpk = field.get("peaks")
    print(f"VERDICT-FIELD  : {fv} "
          + ("⇒ 全程锁在同一个峰" + (f" #{fpk[0]}" if fpk else "")
             + ("（最左＝扫描起点）" if fpk and fpk[0] == 0 else "")
             + f"、对最近峰残差 ≤{field.get('resid_max_abs', 0):.2f} px"
             if fv == "tied-peak-stable" else "")
          + ("⇒ 三相之间**跳峰**：等分外观下锁点不稳定" if fv == "tied-peak-hop" else "")
          + ("⇒ 不跳但偏离任何峰心 >3 px：读数不可当绝对位置" if fv == "tied-peak-offset" else "")
          + ("⇒ 独立尺子没抓到 ⇒ 本问不产出" if fv == "no-truth" else "")
          + ("⇒ 标定失败 ⇒ 本问不产出" if fv == "calibrate-failed" else "")
          + ("⇒ estimate 一次都没成功返回 ⇒ 本问不产出" if fv == "no-readings" else "")
          + ("⇒ 本次跳过（--no-field / --no-twin）" if fv == "skipped" else ""))
    fact("分辨率限制", f"官方 ceiling 只在**注册期**算一次（docstring 原文 'a constant for the "
                      f"lifetime of one register_target'）⇒ 它不是逐帧健康度，逐帧健康度仍是 "
                      f"estimate() 自己的返回。")
    del eng
    return 0


if __name__ == "__main__":
    sys.exit(main())
