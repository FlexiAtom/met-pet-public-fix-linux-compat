#!/usr/bin/env python3
"""H6 · 判据变更（`f81487f`，随 canonical `32fa716d` 交付）后的**宿主侧复刻重定义**

出处：fidus 入站七件，本探针回应其中四件的反证空格——
* `meapet-h4-replica-now-stale`：§2 反证表第 1 行"五臂加密铺后重跑，逐臂报 `s_fine_only` 与 `s_fine+dense`"。
* `meapet-secondary-defense-rescope`：§2 两条"待贵方回填"（引擎覆盖段两条尺子是否一致；周期 > 模板半程是否真的测不到）。
* `meapet-untrackable-registration-path`：§2 帧级普查（一批帧从 `Ok + 地板` 变 `Err`）与**单调性**反证（有无"旧 Err 新 Ok"）。
* `meapet-fixture-dtype-fingerprint`：§2 反证表第 2 行（打印 `blk` 与模板的 sha256 前 16 位）。

**本探针全程 CPU-only**：不抓屏、不上屏、不标定 ⇒ 零输出 mutation，可与桌面探针并排跑。
密铺规则**逐字照抄** fidus 件 `meapet-h4-replica-now-stale` §2 给出的公式（其已声明"贵方照它算不会与引擎取舍错位"），
我方只做一件事：把它变成可执行代码并与引擎 `confidence_ceiling` **逐位对表**。

八问：
* **F 公式自证**：fidus 公布的三个尺度（`400×300` 全保、`600×400` 只保竖轴、`900×600` 两轴皆弃）能否由该式复算出来。
* **A 失配方向**：五臂的 `s` 加密铺后是否上升（上升 ⇒ 旧复刻**低估**歧义 ⇒ 危险侧）。
* **B 两条尺子对表**：复刻 `s_engine` 预测的 cap 与引擎 `confidence_ceiling` 是否逐位相同（同 ⇒ 复刻可继续做交叉校验）。
* **C 逃生门口径**：**被默认策略拒**的臂在 `ambiguous=True` 下是否落到地板 `0.05`；未拒臂是否**不改值**
  （逃生门只撤拒绝、不动 ceiling——第一版把这两件事混成一句"全部落地板"，判决因此是错的而非证据）。
* **D 缺口 1**：模板只含一份内容、屏上重复 ⇒ 引擎与复刻**是否同样测不到**（ceiling 近满值）。
* **E 缺口 3**：**只在对角有精确周期、两轴皆非周期**的模板 ⇒ 预计引擎给高 ceiling 而真值 `s≈1`，**双方皆盲**。
* **O 夹具指纹**：同 seed 的 `dtype=np.uint8` 与 `int64→astype` 两条路径是不是同一块图
  （不是 ⇒ 跨侧对 ceiling 前必须先对 `blk` sha）。
* **G 成本**：注册耗时随模板尺寸怎么走（fidus 实测 `400×300` 45 ms；我方据此定"重注册不得进 2 ms tick"）。

用法：
```
QT_QPA_PLATFORM=wayland .venv/bin/python scripts/fidus_positioning_probe/probe_h6_dense_gate.py
```
可选 `--frames N`（跑 N 帧真实 Live2D 素材的注册普查）、`--no-real`。

失效边界：判据层复刻**与合成器无关**，但它只回答"能否注册、ceiling 多少"，不回答"锁到哪个峰"——
后者仍是真机问题（`probe_h5_ceiling_surface.py` 的 C 臂族）。密铺预算若 fidus 后续改动，本文件的常量会失配；
`REPLICA` 那条逐位判决就是这类失配的**哨兵**，锚点变更须重跑（§12h-3）。
两个本轮踩过的坑，留作口径：① 引擎的 ceiling 是 **f32**，地板是 `0.050000000745058…` 而非 `0.05`，
用 float64 字面量比 ⇒ **恒不等**（判决会假红）；② "造一个斜向周期"必须让**两轴都非周期**，
两个指标都取模的构造会顺带造出轴向周期，测的其实是引擎已覆盖的那一段。
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
import probe_h5_ceiling_surface as H5  # noqa: E402

ANCHOR = "v0.1.0-beta.1-47-gf297a54"
WHEEL_SHA = "32fa716de68952d1ccc2a914692b99bbdc2441f400415e4c00c1591b1bb08feb"
SO_SHA = "1c695ea38a6037ee78511c0bd4a19a2440e8e0e6ee719983d8e8b078370e49a9"
FINE_RADII = (2, 4, 8, 16)        # fidus 保留未动的那四个细半径
DENSE_BUDGET = 60_000_000         # 像素积预算（fidus 件 §2 原文数字）
MAX_SELF_SIM = H3.MAX_SELF_SIMILARITY   # 0.98：默认策略的拒绝线
CEIL_FLOOR = 0.05


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


def _f(v) -> str:
    return "—" if v is None else f"{v:.17g}" if isinstance(v, float) else str(v)


def rgba_of(rgb: np.ndarray) -> np.ndarray:
    h, w = rgb.shape[:2]
    return np.ascontiguousarray(
        np.concatenate([rgb, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
    )


# ───────────────────────────────────  密铺 + 预算（照抄 fidus 公式）
def axis_lags(n: int) -> list[int]:
    """`l ∈ [2, floor(n/2)]`，排除那四个已由细半径覆盖的半径（fidus 原文措辞）。"""
    return [l for l in range(2, n // 2 + 1) if l not in FINE_RADII]


def axis_work(n: int, m: int) -> int:
    """该轴的工作量 `Σ (n − l) * m`——fidus 的计价单位是**像素积**。"""
    return sum((n - l) * m for l in axis_lags(n))


def plan_axes(w: int, h: int) -> dict:
    """两轴分别计价 → 由便宜到贵累加 → 超预算的**整轴放弃**（不降分辨率、不抽样）。"""
    priced = sorted([("横", axis_work(w, h)), ("竖", axis_work(h, w))], key=lambda t: t[1])
    kept: list[str] = []
    total = 0
    for name, cost in priced:
        if total + cost <= DENSE_BUDGET:
            total += cost
            kept.append(name)
    return {"cost": dict(priced), "total": total, "kept": kept,
            "dropped": [n for n, _c in priced if n not in kept]}


def dense_worst(plane: np.ndarray, kept: list[str]) -> tuple[float | None, int, tuple[int, int]]:
    """保留轴上"每个滞后取其自身重叠区全像素"的 NCC 最大值。返回 (worst, 采样数, 命中滞后的轴/量)。"""
    h, w = plane.shape
    best: float | None = None
    best_at: tuple[int, int] = (0, 0)
    n_samp = 0
    for axis in kept:
        n, m = (w, h) if axis == "横" else (h, w)
        for lag in axis_lags(n):
            v = H3._self_ncc(plane, lag, 0) if axis == "横" else H3._self_ncc(plane, 0, lag)
            n_samp += 1
            if v is not None and (best is None or v > best):
                best, best_at = float(v), (lag, 0 if axis == "横" else 1)
    return best, n_samp, best_at


def replica_gate(rgba: np.ndarray) -> dict:
    """新判据下的宿主侧复刻：`s_engine = max(细半径 worst, 密铺保留轴 worst)`。

    细半径那一遍**逐字沿用** `H3.localizability`（fidus 声明未改动），只追加密铺那一遍。
    """
    h, w = rgba.shape[:2]
    verdict_fine, fine_worst, _per = H3.localizability(rgba)
    plane = H3.luma709(rgba[..., :3])
    plan = plan_axes(w, h)
    if fine_worst is None:
        return {"verdict": verdict_fine, "fine": None, "dense": None, "s": None,
                "cap": None, "refused": False, "plan": plan, "n_dense": 0, "at": (0, 0)}
    dense, n_dense, at = dense_worst(plane, plan["kept"])
    s = float(max(fine_worst, dense if dense is not None else -1.0))
    cap = float(np.float32(max(1.0 - s, CEIL_FLOOR)))
    return {"verdict": verdict_fine, "fine": float(fine_worst), "dense": dense, "s": s,
            "cap": cap, "refused": s >= MAX_SELF_SIM, "plan": plan, "n_dense": n_dense, "at": at}


# ───────────────────────────────────  各问
def arm_identity() -> dict:
    print("\n=== 0 · 交付面自证（锚点 / 内嵌 sha / 新签名 / 新文档面在件）===")
    so = Path(fidus.__file__).with_name("fidus.abi3.so")
    wheel = Path.home() / ".Athena/projects/meapet/reference/artifacts" / (
        "fidus-0.1.0.dev0-cp310-abi3-manylinux_2_35_x86_64.whl")
    anchor = getattr(fidus, "__git_commit__", None)
    fact("fidus.__git_commit__", f"{anchor!r} 期望 {ANCHOR!r} ⇒ "
         + ("命中" if anchor == ANCHOR else "✗ 不是本批的件"))
    so_sha = hashlib.sha256(so.read_bytes()).hexdigest()
    fact("已装 .so sha256", f"{so_sha[:16]}… 期望 {SO_SHA[:16]}… ⇒ "
         + ("命中" if so_sha == SO_SHA else "✗ 不一致"))
    fact("交付件 sha256", f"{hashlib.sha256(wheel.read_bytes()).hexdigest()} == 已登记 canonical? ⇒ "
         + ("命中" if hashlib.sha256(wheel.read_bytes()).hexdigest() == WHEEL_SHA else "✗ 不一致"))
    import inspect  # noqa: PLC0415
    sig = str(inspect.signature(fidus.Fidus.register_target))
    doc = fidus.Fidus.register_target.__doc__ or ""
    fact("register_target 签名", sig + ("　⇒ 逃生门 `ambiguous` 已在件" if "ambiguous" in sig else "　✗ 无"))
    for probe, lbl in (("every** horizontal and vertical lag", "密铺覆盖已写进文档面"),
                       ("Compare against where", "判稳规则已改为「与我自己摆窗对照」"),
                       ("class-level", "§3-1 类级赋值那句（fidus 声明**未随本轮重打**）")):
        hit = probe in doc or probe in (fidus.Fidus.confidence_ceiling.__doc__ or "")
        fact(f"文档面 · {lbl}", "在件" if hit else "不在件")
    return {"anchor_ok": anchor == ANCHOR, "so_ok": so_sha == SO_SHA,
            "has_ambiguous": "ambiguous" in sig}


def arm_formula() -> list[dict]:
    print("\n=== F · 密铺预算公式能否复算 fidus 公布的三个尺度 ===")
    rows: list[dict] = []
    published = {(400, 300): "两轴全保（其称实测 45 ms）",
                 (600, 400): "只保竖轴",
                 (900, 600): "两轴皆弃"}
    mine = {(128, 64): "C3 模板", (160, 160): "产品 patch 喂法", (461, 614): "产品整窗 raw 喂法"}
    for (w, h), note in {**published, **mine}.items():
        p = plan_axes(w, h)
        in_published = (w, h) in published
        kept = p["kept"]
        want = {"两轴全保（其称实测 45 ms）": ["竖", "横"], "只保竖轴": ["竖"], "两轴皆弃": []}[note] \
            if in_published else None
        eq = None if want is None else (sorted(kept) == sorted(want))
        fact(f"{w}×{h}", f"横轴 {p['cost']['横']:,} 竖轴 {p['cost']['竖']:,} 像素积 ⇒ "
                         f"保留 {kept or '无'}｜预算内累计 {p['total']:,}"
             + (f" ⇒ 与贵方公布值{'一致' if eq else '✗ 不一致'}" if in_published else f"（{note}）"))
        rows.append({"wh": (w, h), "kept": kept, "published": in_published, "eq": eq, "plan": p})
    return rows


def arm_arms(eng) -> list[dict]:
    print("\n=== A/B/C · 五臂：细半径 vs 细半径+密铺 vs 引擎实际 ceiling ===")
    rows: list[dict] = []
    for key, (lbl, maker) in H5.CASES.items():
        arr = np.ascontiguousarray(maker())
        g = replica_gate(arr)
        emsg = ""
        try:
            eng.register_target(arr, False)
            off, refused = float(eng.confidence_ceiling), False
        except BaseException as exc:  # noqa: BLE001
            off, refused = None, True
            emsg = type(exc).__name__ + ": " + str(exc).replace("\n", " ")[:60]
        try:
            eng.register_target(arr, True)
            amb = float(eng.confidence_ceiling)
        except BaseException as exc:  # noqa: BLE001
            amb = None
            fact(f"[{key}] ambiguous=True 也拒", type(exc).__name__)
        fine = g["fine"]
        fine_cap = None if fine is None else float(np.float32(max(1.0 - fine, CEIL_FLOOR)))
        up = g["dense"] is not None and g["s"] > g["fine"]
        fact(f"[{key}] {lbl}", f"旧复刻 s={_f(g['fine'])} 预测 cap={_f(fine_cap)} |"
                               f" 密铺 {g['plan']['kept']} 命中 lag={g['at'][0]} s={_f(g['dense'])}"
                               f" ⇒ 新 s={_f(g['s'])} 预测 cap={_f(g['cap'])}"
                               f" | 引擎 {'REFUSED ' + emsg if refused else _f(off)}")
        rows.append({"key": key, "fine": g["fine"], "fine_cap": fine_cap, "dense": g["dense"],
                     "s": g["s"], "cap": g["cap"], "official": off, "ambiguous": amb,
                     "refused": refused, "rose": up,
                     "eq_official": (not refused) and off == g["cap"]})
    rose = [r["key"] for r in rows if r["rose"]]
    fact("**失配方向**", f"加密铺后 `s` 上升的臂 = {rose or '无'} ⇒ "
         + ("旧复刻确会**低估**歧义（fidus §危险侧成立）" if rose else "本次五臂未复现上升"))
    both = [r for r in rows if not r["refused"]]
    eqn = sum(1 for r in both if r["eq_official"])
    fact("**两条尺子对表**", f"未拒臂里复刻 cap 与引擎 ceiling 逐位相等 {eqn}/{len(both)}")
    return rows


def arm_dtype(eng) -> dict:
    """缺口 0 · 夹具指纹：同一 seed 在两种 dtype 取数路径下是**两块不同的图**。

    对应 fidus 件 `meapet-fixture-dtype-fingerprint` §2 反证表第 2 行（"打印 blk 与模板的
    sha256 前 16 位"）。这条不是为了对数字，是为了让**两侧任何一次 ceiling 分歧都能被判成
    "判据不同"而非"素材不同"**——先把素材钉死。
    """
    print("\n=== 0b · 夹具 dtype 指纹（同 seed 两条取数路径 ⇒ 两块图 ⇒ 两个 ceiling）===")
    out: dict[str, dict] = {}
    for lbl, mk in (("dtype=np.uint8（我方）", lambda: np.random.default_rng(4242).integers(
                        0, 256, size=(64, 64, 3), dtype=np.uint8)),
                    ("int64 再 astype（贵方）", lambda: np.random.default_rng(4242).integers(
                        0, 256, size=(64, 64, 3)).astype(np.uint8))):
        blk = mk()
        one = rgba_of(blk)
        four = rgba_of(np.concatenate([blk] * 4, axis=1))
        g = replica_gate(one)
        try:
            eng.register_target(one, False)
            off, refused = float(eng.confidence_ceiling), False
        except BaseException:  # noqa: BLE001
            off, refused = None, True
        fact(lbl, f"blk sha={hashlib.sha256(blk.tobytes()).hexdigest()[:16]}"
                  f" 四联屏 sha={hashlib.sha256(four.tobytes()).hexdigest()[:16]}"
                  f" ⇒ 模板 64×64 复刻 s={_f(g['s'])} cap={_f(g['cap'])}"
                  f" | 引擎 {'REFUSED' if refused else _f(off)}")
        out[lbl] = {"blk": hashlib.sha256(blk.tobytes()).hexdigest()[:16],
                    "s": g["s"], "cap": g["cap"], "official": off}
    fact("两路径同图?", "是" if len({v["blk"] for v in out.values()}) == 1
         else "否 ⇒ **任何跨侧 ceiling 对表都必须先报 blk sha**")
    return out


def arm_gaps(eng) -> dict:
    print("\n=== D/E · 两条真缺口：周期 > 模板半程；斜向重复 ===")
    out: dict[str, dict] = {}
    blk = np.random.default_rng(4242).integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
    single = rgba_of(blk)                      # 模板只含一份 ⇒ 屏上周期 64 **大于**模板自身
    g = replica_gate(single)
    try:
        eng.register_target(single, False)
        off, refused = float(eng.confidence_ceiling), False
    except BaseException:  # noqa: BLE001
        off, refused = None, True
    fact("缺口1 模板 64×64 单块（屏上 4 块平铺）",
         f"复刻 s={_f(g['s'])} cap={_f(g['cap'])} | 引擎 "
         + ("REFUSED" if refused else f"ceiling={_f(off)}")
         + ("　⇒ 引擎与复刻**同盲**：模板内无该滞后的重叠可测（fidus 预期成立）"
            if not refused and off is not None and off > 0.9 else ""))
    out["gap1"] = {"cap": g["cap"], "official": off, "refused": refused}

    # 缺口 3 的**唯一**合法构造：精确周期取对角 (24,24)，两轴皆非周期。
    # 按移位不变量参数化：u = r−c（查表不环绕 ⇒ 轴向非周期），t = (r+c) mod 48（环绕）。
    # (a,b) 是周期 ⇔ a=b 且 2a ≡ 0 (mod 48) ⇔ a=b ∈ 24Z ⇒ 只有对角周期。
    # 上一版写 `tile[(r−c)%32, (r+c)%32]`：两个指标**都**取模 ⇒ 横向 32 px 也是精确周期
    # ⇒ 测的是"轴向覆盖"而非斜向缺口，结论无效，故重写（这是本探针自己的反证抓到的）。
    h = w = 128
    rr = np.arange(h)[:, None]
    cc = np.arange(w)[None, :]
    table = np.random.default_rng(7).integers(0, 256, size=(2 * h - 1, 48, 3), dtype=np.uint8)
    diag = np.ascontiguousarray(table[(rr - cc) + (h - 1), (rr + cc) % 48])
    d = rgba_of(diag)
    gd = replica_gate(d)
    plane = H3.luma709(d[..., :3])
    true_diag = H3._self_ncc(plane, 24, 24)
    axial = [float(v) for v in (H3._self_ncc(plane, l, 0) for l in (24, 48)) if v is not None]
    try:
        eng.register_target(d, False)
        ofd, refused_d = float(eng.confidence_ceiling), False
    except BaseException:  # noqa: BLE001
        ofd, refused_d = None, True
    fact("缺口3 模板 128×128（精确周期只在对角 (24,24)）",
         f"真值 s(24,24)={_f(None if true_diag is None else float(true_diag))}"
         f" | 轴向 s(24,0)/(48,0)={_f(max(axial) if axial else None)}"
         f" ⇒ 两轴复刻 s={_f(gd['s'])} cap={_f(gd['cap'])} | 引擎 "
         + ("REFUSED" if refused_d else f"ceiling={_f(ofd)}"))
    blind = (not refused_d) and ofd is not None and ofd > 0.9 and (true_diag or 0.0) > 0.98
    fact("  判决", "⇒ **双方皆盲成立**：模板内确有 s≈1 的精确重复，引擎与我方的两轴复刻却都给高 ceiling"
        if blind else "⇒ 本构造未复现「双方皆盲」，按上面三个数逐项核")
    out["gap3"] = {"cap": gd["cap"], "official": ofd, "refused": refused_d,
                   "true_diag": None if true_diag is None else float(true_diag), "blind": blind}
    return out


def arm_cost(eng) -> list[dict]:
    print("\n=== G · 注册成本随模板尺寸（决定「重注册即刷新」能否进 tick）===")
    print("    每尺寸 3 次取**最快**（GC / 其它臂的缓存会抬高读数，取最快才是该操作的下界）")
    rows: list[dict] = []
    rng = np.random.default_rng(11)
    for (w, h) in [(128, 64), (160, 160), (400, 300), (461, 614), (600, 400), (900, 600)]:
        arr = rgba_of(rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8))
        samples: list[float] = []
        outcome = ""
        for _ in range(3):
            t0 = time.perf_counter()
            try:
                eng.register_target(arr, True)
                outcome = f"ok ceiling={_f(eng.confidence_ceiling)}"
            except BaseException as exc:  # noqa: BLE001
                outcome = type(exc).__name__
            samples.append((time.perf_counter() - t0) * 1000.0)
        ms = min(samples)
        p = plan_axes(w, h)
        tri = " ".join(f"{s:.1f}" for s in samples)
        fact(f"{w}×{h}", f"{ms:7.1f} ms 最快（三次 {tri}）"
             f"{outcome}｜保留轴 {p['kept'] or '无'}"
             + ("　⚠ 超过 2 ms GUI tick 预算" if ms > 2.0 else ""))
        rows.append({"wh": (w, h), "ms": ms, "samples": samples,
                     "kept": p["kept"], "outcome": outcome})
    return rows


VARIANTS = ("raw", "alpha_crop", "patch")


def collect_feeds(app, n: int) -> tuple[list[tuple[str, np.ndarray]], dict]:
    """渲染 n 帧真实素材、三种喂法各一份。

    **这些帧不可跨进程复现**（Live2D 含时序：同一 seed 两次跑出来的 alpha_crop 尺寸就不同），
    所以要做"同一批帧过两个引擎"的 A/B，只有一条路：**落盘**（`dump_feeds`）再回放。
    跨会话比两轮的 Ok/Err 计数不是同批对照，只是两次独立采样。
    """
    from probe_h3_targetability import render_live2d_frames  # noqa: PLC0415
    frames, rinfo, host, widget = render_live2d_frames(app, n)
    cells = [(f"{i}/{v}", H3.make_feed(frame, None, v))
             for i, frame in enumerate(frames) for v in VARIANTS]
    del host, widget
    return cells, rinfo


def dump_feeds(dirpath: Path, cells: list[tuple[str, np.ndarray]]) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    for label, arr in cells:
        np.save(dirpath / f"{label.replace('/', '_')}.npy", arr, allow_pickle=False)
    (dirpath / "digest.txt").write_text(feeds_digest(cells) + "\n", encoding="utf-8")


def load_feeds(dirpath: Path) -> list[tuple[str, np.ndarray]]:
    """按 (帧号, 喂法) 排序回读——排序必须与 `collect_feeds` 同构，否则指纹对不上。"""
    out: list[tuple[str, np.ndarray]] = []
    for i in range(9999):
        for v in VARIANTS:
            p = dirpath / f"{i}_{v}.npy"
            if p.exists():
                out.append((f"{i}/{v}", np.load(p)))
    return out


def feeds_digest(cells: list[tuple[str, np.ndarray]]) -> str:
    """素材指纹：让"结论变了"能归因到**判据**而不是**喂进去的图**（夹具 dtype 那件的教训一般化）。"""
    body = "|".join(f"{k}={hashlib.sha256(a.tobytes()).hexdigest()[:16]}" for k, a in cells)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def census(eng, cells: list[tuple[str, np.ndarray]], rinfo: dict | None = None) -> dict:
    """逐格注册（默认策略 + 逃生门各一次）并与我方复刻对表。

    `--replay` 下这是**唯一**可跨引擎比的函数：两侧唯一不同的就是传进来的 `eng`。
    """
    if rinfo:
        fact("渲染信息", {k: v for k, v in sorted(rinfo.items()) if k in ("renderer", "window_px")})
    tally = {v: [0, 0] for v in VARIANTS}
    regress: list[str] = []
    rose: list[str] = []
    engine_out: dict[str, str] = {}
    for label, fed in cells:
        i, variant = label.split("/")
        g = replica_gate(fed)                      # 我方复刻：与引擎无关，恒作对表用
        ok_default, err, off = False, "", None
        try:
            eng.register_target(fed, False)
            off = float(eng.confidence_ceiling)
            ok_default = True
        except BaseException as exc:  # noqa: BLE001
            err = type(exc).__name__
        try:
            eng.register_target(fed, True)
            amb = float(eng.confidence_ceiling)
        except BaseException:  # noqa: BLE001
            amb = None
        tally[variant][0 if ok_default else 1] += 1
        engine_out[label] = f"ok {_f(off)}" if ok_default else f"ERR {err}"
        # 注意：`rose` 比的是**我方复刻**的细半径值与加密铺后的值，复刻是我自己的代码，
        # 两次回放必然一致 ⇒ 它**不是**引擎证据，只是"这批素材里哪些格被密铺加严了"的清单。
        # 跨引擎真正要比的是 `engine_out` 那一列（VERDICT-CENSUS 的引擎列）。
        if g["dense"] is not None and g["s"] > g["fine"]:
            rose.append(label)
        # 单调性检验：默认被拒者，显式逃生门若给出**高于复刻**的 ceiling ⇒ fidus"只加不减"有反例
        if not ok_default and amb is not None and amb > g["cap"] + 1e-9:
            regress.append(label)
        fact(f"{label} {fed.shape[1]}×{fed.shape[0]}"
             f" sha={hashlib.sha256(fed.tobytes()).hexdigest()[:16]}",
             f"引擎 {'ok ceiling=' + _f(off) if ok_default else 'REFUSED(' + err + ')'}"
             f"｜逃生门 ceiling={_f(amb)}｜复刻 s 细={_f(g['fine'])} 新={_f(g['s'])}"
             f" 保留轴={g['plan']['kept'] or '无'}")
    digest = feeds_digest(cells)
    fact("计数（引擎默认策略）", {k: f"ok {v[0]} / refused {v[1]}" for k, v in tally.items()})
    fact("单调性反例（逃生门反而高于复刻）", regress or "无")
    fact("我方复刻里密铺加严的格", f"{len(rose)} 格：{rose[:8] or '无'}（复刻列，非引擎证据）")
    fact("素材指纹", digest)
    return {"tally": tally, "regress": regress, "rose": rose,
            "fingerprint": digest, "engine_out": engine_out}


def arm_real(eng, n: int, app) -> dict:
    print(f"\n=== C' · 产品帧级普查：新判据下有多少帧从 Ok 变 Err（{n} 帧 × 3 喂法）===")
    cells, rinfo = collect_feeds(app, n)
    return census(eng, cells, rinfo)


def main() -> int:
    ap = argparse.ArgumentParser(description="H6: 判据变更后的宿主侧复刻重定义")
    ap.add_argument("--frames", type=int, default=6, help="真实 Live2D 帧数（0=跳过普查）")
    ap.add_argument("--no-real", action="store_true", help="跳过帧级普查")
    ap.add_argument("--dump", metavar="DIR", help="渲染并落盘喂给引擎的帧（供跨引擎同批 A/B）")
    ap.add_argument("--replay", metavar="DIR", help="回读落盘帧、只跑普查（PYTHONPATH 选引擎）")
    args = ap.parse_args()

    from PyQt5.QtWidgets import QApplication  # noqa: PLC0415
    app = QApplication(sys.argv)
    eng = fidus.Fidus.build_wayland()
    anchor = getattr(fidus, "__git_commit__", None)
    if bool(args.dump) and bool(args.replay):
        ap.error("--dump 与 --replay 互斥")
    if args.dump:
        cells = collect_feeds(app, args.frames)[0]
        dump_feeds(Path(args.dump), cells)
        fact("已落盘", f"{args.dump}：{len(cells)} 格，素材指纹={feeds_digest(cells)}")
        return 0
    if args.replay:
        cells = load_feeds(Path(args.replay))
        print(f"\n=== C' · 同批回放（引擎由 PYTHONPATH 决定）{len(cells)} 格 ===")
        fact("本进程引擎锚点", repr(anchor))
        r = census(eng, cells)
        print("\n[VERDICT]")
        print(f"VERDICT-CENSUS  : 锚点={anchor} 素材指纹={r['fingerprint']}"
              f" 引擎默认计数={r['tally']} 逃生门反例={r['regress'] or '无'}")
        print("VERDICT-CELLS   : "
              + " ".join(f"{k}:{v.split()[0]}" for k, v in sorted(r["engine_out"].items())))
        return 0
    d = arm_identity()
    fp = arm_dtype(eng)
    rows_f = arm_formula()
    rows_arms = arm_arms(eng)
    gaps = arm_gaps(eng)
    rows_cost = arm_cost(eng)
    real = {} if (args.no_real or not args.frames) else arm_real(eng, args.frames, app)

    print("\n[VERDICT]")
    if real:
        cnt = real["tally"]
        print(f"VERDICT-CENSUS  : 锚点={anchor} 帧指纹={real['fingerprint']} 计数={cnt}"
              f" 单调性反例={real['regress'] or '无'} 密铺使 s 上升={len(real['rose'])} 格")
    idy = "命中" if d["anchor_ok"] else "✗"
    so = "命中" if d["so_ok"] else "✗"
    print(f"VERDICT-IDENTITY: anchor={idy} .so={so} ambiguous 形参={d['has_ambiguous']} ⇒ "
          + ("新判据交付面与登记一致" if d["anchor_ok"] and d["so_ok"] else "✗ 与登记不符"))
    pub = [r for r in rows_f if r["published"]]
    print(f"VERDICT-FORMULA : 贵方公布的 {len(pub)} 个尺度由公式复算全部"
          + ("一致 ⇒ 密铺/预算可被宿主自算，无需 fidus 扩公共面"
             if all(r["eq"] for r in pub) else "✗ 有不一致，须对表"))
    rose = [r["key"] for r in rows_arms if r["rose"]]
    print(f"VERDICT-STALE   : 五臂里加密铺后 `s` 上升 = {rose or '无'} ⇒ "
          + ("旧复刻链**确会低估**歧义，判据须改读 getter" if rose
             else "本次未上升 ⇒ fidus 的危险侧论证在我方素材上未落地"))
    both = [r for r in rows_arms if not r["refused"]]
    eqn = sum(1 for r in both if r["eq_official"])
    print(f"VERDICT-REPLICA : 复刻(细+密铺+预算) 与引擎 ceiling 逐位相等 {eqn}/{len(both)} ⇒ "
          + ("照抄公式的复刻**可**继续作交叉校验" if both and eqn == len(both) else "✗ 有格不等"))
    esc = [r for r in rows_arms if r["refused"]]
    keep = [r for r in rows_arms if not r["refused"]]
    floor32 = float(np.float32(CEIL_FLOOR))   # 引擎是 f32：拿 float64 的 0.05 比会**恒不等**
    ok_esc = bool(esc) and all(r["ambiguous"] == floor32 for r in esc)
    ok_keep = all(r["ambiguous"] == r["official"] for r in keep)
    print(f"VERDICT-ESCAPE  : 被拒臂 {[r['key'] for r in esc]} 逃生门后 ceiling ="
          f" {[_f(r['ambiguous']) for r in esc]} ⇒ "
          + ("落地板 0.05，贵方文档口径成立" if ok_esc else "✗ 有格不落地板")
          + ("；未拒臂逃生门不改值" if ok_keep else "；✗ 未拒臂逃生门反而改值"))
    g1, g3 = gaps["gap1"], gaps["gap3"]
    print(f"VERDICT-DTYPE   : 两条取数路径的 blk sha = {sorted(set(v['blk'] for v in fp.values()))}"
          + "　⇒ 不同图；跨侧对 ceiling 前先对 blk sha")
    print(f"VERDICT-GAP1    : 单块模板（屏上 4 块）引擎 ceiling={_f(g1['official'])}"
          + ("　⇒ 近满值：该形**任何模板侧指标都发现不了**，防线只能靠 v6-7"
             if not g1["refused"] else "　⇒ 被拒"))
    print(f"VERDICT-GAP3    : 只有对角周期的模板 真值 s={_f(g3['true_diag'])}"
          f" 引擎 ceiling={_f(g3['official'])} ⇒ "
          + ("双方皆盲：新判据不覆盖斜向，我方两轴复刻同样不覆盖" if g3["blind"]
             else ("被拒" if g3["refused"] else "本构造未复现，见明细")))
    slow = [r for r in rows_cost if r["ms"] > 2.0]
    slow_lbl = [f"{r['wh'][0]}x{r['wh'][1]}" for r in slow]
    peak_ms = max(r["ms"] for r in rows_cost)
    print(f"VERDICT-COST    : 注册 >2 ms 的尺寸 = {slow_lbl}"
          f"｜最大 {peak_ms:.1f} ms ⇒ "
          + ("重注册不得进 GUI tick（fidus 提醒成立）" if slow else "全部进得了 tick"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
