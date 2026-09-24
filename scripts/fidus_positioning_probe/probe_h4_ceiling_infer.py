#!/usr/bin/env python3
"""H4 · 宿主能否**自己算出** `confidence_ceiling`（不依赖 fidus 暴露那个 getter）

问什么（一条，纯判据问题）：`estimate()` 返回的 conf 恒等于 `f32(max(1 − s, 0.05))`（s 由 meapet
复刻链从模板像素算出）吗？

* 若**等号跨模板成立** ⇒ conf 低不是"匹配差"，而是"模板本身可歧义" ⇒ 宿主无需 fidus 改码就能区分
  "被钳"与"真弱测量"（这正是 12f-9 诉 3 想解决的问题）。
* 若某模板 conf **明显高于**该预测 ⇒ 等号不成立 ⇒ 复刻链不忠实，诉 3 仍是硬缺口。

起点是一条已命中的预测：H2/A10 那张 200×200 图案的 conf 恒为 `0.10667813569307327`，
而 `float(np.float32(1 − s_复刻)) == 0.10667813569307327` **逐位相等**（`s_复刻=0.893321861584331`）。
本探针把这条单点证据摊成多模板：a10 原图、随机色块、逐像素噪声（s 低 ⇒ 预测 ceiling 高，
才是能证伪"conf 是固定标志值"的那一格）、周期条纹（预测被 0.98 门拒）；再加一条**反向臂**
`noise_blur`：注册清晰图、屏上只显示模糊版 ⇒ 得分掉下来而 ceiling 不变 ⇒ 用来判"conf ≠ ceiling
时能不能读作未被钳"。

用法（真机，显示一个静止目标窗、抓几次屏）：
```
WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland \
  .venv/bin/python scripts/fidus_positioning_probe/probe_h4_ceiling_infer.py
```

失效边界：只主张"本机该 wheel `8684b72` 装配下 conf 与复刻 ceiling 的数值关系"；不主张落点精度
（§12e）、不主张线程行为（§12g）。等号成立方向可用于判"被钳"，**不成立方向不可判别**（既可能是
"未被钳"也可能是"复刻漂移"）——这条不对称写进结论，不当两向判据卖。
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

from PyQt5.QtCore import QEventLoop, Qt  # noqa: E402
from PyQt5.QtGui import QPainter  # noqa: E402
from PyQt5.QtWidgets import QApplication, QWidget  # noqa: E402

import fidus  # noqa: E402
import probe_a10_geometry as A10  # noqa: E402
import probe_h3_targetability as H3  # noqa: E402

TARGET_X0, TARGET_Y0 = 640, 360
MIN_CONFIDENCE_CEILING = 0.05  # 与 fidus `fidus-estimate/src/lib.rs:164` 同值，此处只用于复算预测


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


# ─────────────────────────────────────────────  模板池（决定预测 ceiling 的高/低）
def pat_noise(seed: int = 20260924) -> np.ndarray:
    """逐像素随机 ⇒ 位移自相关极低 ⇒ 预测 ceiling 接近 1。"""
    rng = np.random.default_rng(seed)
    a = rng.integers(0, 256, size=(200, 200, 3), dtype=np.uint8)
    alpha = np.full((200, 200, 1), 255, dtype=np.uint8)
    return np.ascontiguousarray(np.concatenate([a, alpha], axis=2))


def pat_blocks(seed: int = 7) -> np.ndarray:
    """32×32 随机色块（中等自相似，且天然低 NCC 敏感 ⇒ 常落在中间预测值）。"""
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, size=(7, 7, 3), dtype=np.uint8)
    a = np.kron(small, np.ones((32, 32, 1), dtype=np.uint8))[:200, :200]
    alpha = np.full((200, 200, 1), 255, dtype=np.uint8)
    return np.ascontiguousarray(np.concatenate([a, alpha], axis=2))


def pat_stripe(seed: int = 11) -> np.ndarray:
    """周期 24 px 竖条纹：结构性高自相似（用来撞 `MAX_SELF_SIMILARITY=0.98` 的拒绝路径）。"""
    x = np.arange(200) // 24 % 2
    band = np.where(x[None, :, None] > 0, 250, 20).astype(np.uint8)
    a = np.repeat(band, 200, axis=0)
    a = (a * np.array([[[1.0, 0.4, 0.7]]], dtype=np.float32)).astype(np.uint8)
    alpha = np.full((200, 200, 1), 255, dtype=np.uint8)
    return np.ascontiguousarray(np.concatenate([a, alpha], axis=2))


def pat_blur_display(rgba: np.ndarray) -> np.ndarray:
    """屏上喂法：**注册那张清晰图**、显示这份模糊过的 ⇒ 得分下降但天花板不变（ceiling 只在注册期算）。"""
    return np.ascontiguousarray(H3.blur_rgba(rgba, 0.9, r=2))


PATTERNS: dict[str, tuple[str, object, object]] = {
    "a10": ("A10 原图（已知 conf 恒 0.106678…）", lambda: A10.make_pattern(), None),
    "blocks": ("7×7 随机色块 ×32px", pat_blocks, None),
    "noise": ("逐像素噪声（预测 ceiling≈1）", pat_noise, None),
    "stripe": ("周期竖条纹（预测被 0.98 门拒）", pat_stripe, None),
    # 判据的**另一半**：注册清晰图、只把**屏上**那份模糊掉 ⇒ 测量得分掉下来 ⇒ 若模型真是
    # `conf = min(raw, ceiling)`，这里必须看到 conf **严格小于**预测 ceiling（且仍逐帧稳定）。
    # 若 conf 依然恰等于 ceiling ⇒ conf 其实与得分无关，"被钳/未被钳"这条判据不成立。
    "noise_blur": ("注册清晰噪声、屏上显示模糊版（预测 conf < ceiling）", pat_noise, pat_blur_display),
}


def predict_cap(rgba: np.ndarray) -> dict:
    verdict, worst, _per = H3.localizability(rgba)
    if worst is None:
        return {"verdict": verdict, "s": None, "cap_f32": None}
    cap = max(1.0 - worst, MIN_CONFIDENCE_CEILING)
    return {"verdict": verdict, "s": worst, "cap_f32": float(np.float32(cap))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="all", help="逗号分隔的模板键；all=全跑")
    ap.add_argument("--est", type=int, default=4, help="每个模板 estimate 次数")
    args = ap.parse_args()

    keys = list(PATTERNS) if args.only == "all" else [k for k in args.only.split(",") if k in PATTERNS]
    print("=== H4 · 宿主自算 confidence_ceiling 是否等于观察 conf ===")
    app = QApplication(sys.argv)

    rows: list[dict] = []
    eng = None
    win = TargetWindow(None)
    win.move(TARGET_X0, TARGET_Y0)
    win.show()
    for key in keys:
        label, mk, disp_mk = PATTERNS[key]
        rgba = np.ascontiguousarray(mk())
        disp = np.ascontiguousarray(disp_mk(rgba)) if disp_mk is not None else rgba
        pred = predict_cap(rgba)
        print(f"\n[{key}] {label}")
        fact("复刻链 s(注册的那张)", f"{pred['s']!r} verdict={pred['verdict']}")
        fact("预测 ceiling=f32(max(1−s,0.05))", pred["cap_f32"])
        if disp_mk is not None:
            fact("注册≠显示", "注册清晰版、屏上喂模糊版 ⇒ 得分应下降而 ceiling 不变（只在注册期算）")

        # 上屏：目标窗换成这张图，先过存在性闸门再谈任何读数（§12g-4 那条教训的固化）。
        win.set_image(A10.qimage_from(disp))
        if _gate(app, disp) is None:
            fact("存在性闸门", "✗ 未上屏或被遮 ⇒ 本模板不产出 conf 读数（失败路径的 conf 不是判据）")
            rows.append({"key": key, "pred": pred, "fatal": "no-screen"})
            continue

        if eng is None:
            t0 = time.perf_counter()
            eng = fidus.Fidus.build_wayland()
            fact("build_wayland", f"{(time.perf_counter() - t0) * 1000:.0f} ms")

        confs: list[float] = []
        pos: list[tuple] = []
        note = ""
        try:
            eng.register_target(rgba, False)
        except BaseException as exc:  # noqa: BLE001  被 0.98 门拒就是本模板的判决
            note = f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')[:120]}"
            fact("register_target", f"RAISED ⇒ {note}")
            rows.append({"key": key, "pred": pred, "register": "refused", "note": note})
            continue
        try:
            cal = eng.calibrate_once()
            fact("calibrate_once",
                 f"scale={getattr(cal, 'scale', '?')} rms={getattr(cal, 'rms_residual_px', '?')}")
            for _ in range(args.est):
                x, y, c = eng.estimate()
                confs.append(float(c))
                pos.append((round(float(x), 3), round(float(y), 3)))
        except BaseException as exc:  # noqa: BLE001
            note = f"{type(exc).__name__}: {str(exc).replace(chr(10), ' ')[:120]}"
            fact("序列", f"RAISED ⇒ {note}（已得 conf {len(confs)} 条）")

        fact("观察 conf", confs)
        fact("观察落点", pos)
        cap = pred["cap_f32"]
        eq = [c == cap for c in confs] if cap is not None else []
        lt = [c < cap for c in confs] if cap is not None else []
        if eq:
            fact("conf vs 预测 ceiling（逐位）",
                 f"相等 {sum(eq)}/{len(eq)}、严格小于 {sum(lt)}/{len(lt)}"
                 + ("⇒ conf 就是被注册期 ceiling 钳住" if all(eq) else
                    "⇒ 全部低于 ceiling ⇒ conf 跟的是**测量得分**，ceiling 是上界而非恒等值"
                    if all(lt) else "⇒ 混合，需分帧看"))
        rows.append({"key": key, "pred": pred, "confs": confs, "eq": eq, "lt": lt, "note": note})

    print("\n[判决] 跨模板汇总")
    for r in rows:
        p = r.get("pred", {})
        confs = r.get("confs") or []
        eq, lt = r.get("eq") or [], r.get("lt") or []
        verdict = ("refused" if r.get("register") == "refused"
                   else "no-conf" if not confs
                   else "全等 ceiling" if all(eq)
                   else "全小于 ceiling" if all(lt) else "混合")
        fact(f"  {r['key']}", f"s={_fmt(p.get('s'))} 预测={_fmt(p.get('cap_f32'))} "
             f"观察={[_fmt(c) for c in confs]} ⇒ {verdict} {r.get('note', '')[:60]}")
    fact("判别力所在", "只有**预测 ceiling > 0.5** 的模板才真能证伪'conf 是固定标志值'：若那里也逐位相等，"
         "则 conf 随模板而变 ⇒ §12e-4'conf 只取两个值'读法需限定为'该模板 ceiling 恒定'")
    fact("两向性", "等号成立 ⇒ 判'被钳'；`noise_blur` 臂负责另一半：注册清晰、屏上喂模糊 ⇒ "
         "若 conf 落到 ceiling 之下，则'不等'确实可读作'未被钳、这是真实测量置信'，判据双向成立")
    fact("不对称告警（若 noise_blur 仍等于 ceiling）",
         "那么 conf 与得分无关、恒等于 ceiling ⇒ 等号不成立方向不可判，须回头改判据")
    return 0


def _fmt(v) -> str:
    return "—" if v is None else f"{v:.17g}" if isinstance(v, float) else str(v)


class TargetWindow(QWidget):
    """一只静止目标窗，**可换图**：四张模板共用同一扇窗 ⇒ 排除"多开窗改变可见性"这个 §12g-4 的坑。"""

    def __init__(self, img):
        super().__init__()
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.im = img
        if img is not None:
            self.setFixedSize(img.width(), img.height())

    def set_image(self, img) -> None:
        self.im = img
        self.setFixedSize(img.width(), img.height())
        self.raise_()
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        if self.im is None:
            return
        p = QPainter(self)
        p.drawImage(0, 0, self.im)
        p.end()


def pump(app, ms: int) -> None:
    end = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < end:
        app.processEvents(QEventLoop.AllEvents, 20)


def _gate(app, rgba):
    pump(app, 900)
    shot = A10.capture("h4_gate")
    if shot is None:
        return None
    m = A10.measure(shot, rgba, [1.0])
    fact("存在性闸门", f"peak={m['peak']:.4f} @({m['x']},{m['y']}) {m['w']}x{m['h']}")
    return shot if m["peak"] > 0.5 else None


if __name__ == "__main__":
    sys.exit(main())
