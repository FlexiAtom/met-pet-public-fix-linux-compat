"""fidus 定位：切换穿透的那一刻，问合成器"我们究竟被摆在了哪里"。

Wayland 客户端查不到自己的全局坐标（`_layer_geometry()` 的 docstring 自陈此事），
而 layer-shell 又会改写我们请求的位置 —— 于是"我以为我在 (x,y)"与"我在 (x,y)"
是两件事。fidus 是屏幕贴片匹配引擎，用来测后者。本模块只做**测量与证伪**，
不碰 Qt、不碰线程、不做决策：调用方给帧与信念，本模块回一个 `Fix` 或 `None`。

判据一律照 `~/.Athena/projects/meapet/working/fidus-switch-positioning.md` §5：
* **喂先验**：`register_target(..., initial_center=...)`，且必须是**中心**（换算见 `_anchor_of`）。
  漏喂即整台机器落回无先验那条路，其可用模板边长上限在 1080p 上只剩 79 px。
* **位移闭环**：读数可信与否只认"自己摆一个已知位移、比对两次读数差"。
  `conf == confidence_ceiling` 在**正确锁定**上恒成立（实测 9/9 逐位相等），
  所以满值置信不背书正确，不能作为替代判据。
  闭环两端一律走**冷获取**（注册后读到定住）：H13 实测**更新路径**（搬走目标后继续读）
  会收敛到与贴片相关的固定偏置（本轮 96² 爬到 +11 px 仍未停），而冷读是 0.01–0.03 px。
* **失败即整段退回**：本模块任何一步不成立都返回 `None`，由调用方沿用合成数 ——
  拒绝切换会把用户关在自己的桌宠外面。

已知适用域（不是本模块能修的）：坐标空间是 layer-shell **可用区的逻辑坐标**，
`scale != 1.0` 与 ≥1920 的真机两侧我方都无硬件，见该件 §7。
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

import numpy as np

# 贴片边长阶梯：大到覆盖有特征的躯干，小到下界引擎会**安静地撒谎**。
# 首档 160 是 §12p.1 在真屏上坐实过的量级。下界 64 是 H14 在远偏先验（y+150，五档）上量出来的：
# `160²` 最坏 0.00 px、`96²` 2.81 px、`64²` 五档全退回（响亮＝安全），而 `48²`/`32²` 五档全"有值"
# 却报出最坏 **+173.33 px / +125.98 px** 的数**且照样过位移闭环**（不同先验给同一个错答案 ⇒
# 锁在真孪生峰上，闭环对它结构上失明）。宁要退回到合成数，不要跳到一个错位置。
SIZES: tuple[int, ...] = (160, 128, 96, 64)
STEP = 16                 # 左上角枚举步长（像素）
MIN_COVERAGE = 0.98       # "整盒在轮廓内"的宽容度：抗 alpha 边缘锯齿
CANDIDATES_PER_SIZE = 3   # 每档取离不透明质心最近的 K 盒送去注册（注册每发 4–93 ms）
ALPHA_MIN = 8             # 与探针同源的"算不透明"阈值
MOVE_PX = 48              # 闭环探针位移；容差取它的一半 ⇒ 抓到假峰(读数不动)必被拦
TOL_PX = MOVE_PX // 2
ACQUIRE_READS = 8         # 一次获取最多连读几发；H11 实测信念偏 440 px 时第 6 发才锁
STABLE_PX = 2.0           # 相邻两发差这么小就叫"定住"（H13 C 相：锁定后逐发逐位等）


@dataclass(frozen=True)
class Candidate:
    """一个候选贴片：盒像素 + 它在 surface 内的锚点偏移（盒中心 − surface 中心）。"""

    box: np.ndarray
    edge: int
    anchor: tuple[float, float]

    def belief_for(self, center: tuple[float, float]) -> tuple[float, float]:
        """把"surface 中心的信念"换算成"贴片中心的信念"——`initial_center` 要的是后者。"""
        return (center[0] + self.anchor[0], center[1] + self.anchor[1])


@dataclass(frozen=True)
class Fix:
    """测量结果：surface 中心（layer 逻辑坐标，探针位移**之前**那一帧的位置）。"""

    center: tuple[float, float]
    conf: float
    edge: int
    ceiling: float


class EngineError(Exception):
    """引擎侧的失败（拒注册 / 丢失目标 / 校准不成），一律导致本模块返回 `None`。"""


class FidusEngine:
    """`fidus.Fidus` 的线程亲和包装：一个线程上建、一个线程上用。

    wheel 不内置线程/取消（契约 H2 已裁），且跨线程误用抛的 `PanicException`
    派生自 `BaseException`、`except Exception` 接不住 —— 所以本类不做任何
    线程调度，只要求调用方从头到尾用同一个线程。
    """

    def __init__(self) -> None:
        import fidus  # 延迟导入：开关关闭时不该付出这份导入

        self._mod = fidus
        self._eng = None
        self.calibrated_ms: Optional[float] = None

    @property
    def untrackable(self) -> type[BaseException]:
        return getattr(self._mod, "FidusUntrackable", Exception)

    @property
    def target_lost(self) -> type[BaseException]:
        return getattr(self._mod, "FidusTargetLost", Exception)

    def _ensure(self):
        if self._eng is None:
            self._eng = self._mod.Fidus.build_wayland()
        return self._eng

    def calibrate(self) -> None:
        """每**会话**一次（实测 2.0–2.2 s + 屏幕闪现）。重挂 surface 不需要再付。"""
        import time

        eng = self._ensure()
        if self.calibrated_ms is not None:
            return
        t0 = time.perf_counter()
        try:
            eng.calibrate_once()
        except BaseException as exc:  # noqa: BLE001 - 引擎异常派生自 BaseException
            raise EngineError(f"calibrate_once: {type(exc).__name__}") from exc
        self.calibrated_ms = (time.perf_counter() - t0) * 1e3

    def register(self, box: np.ndarray, initial_center: tuple[float, float]) -> float:
        """注册并回 `confidence_ceiling`；被拒则抛 `EngineError`。"""
        eng = self._ensure()
        try:
            eng.register_target(box, False, initial_center=initial_center)
            return float(eng.confidence_ceiling)
        except BaseException as exc:  # noqa: BLE001
            raise EngineError(f"register_target: {type(exc).__name__}") from exc

    def estimate(self) -> tuple[float, float, float]:
        eng = self._ensure()
        try:
            x, y, conf = eng.estimate()
            return (float(x), float(y), float(conf))
        except BaseException as exc:  # noqa: BLE001
            raise EngineError(f"estimate: {type(exc).__name__}") from exc


def _integral(mask: np.ndarray) -> np.ndarray:
    h, w = mask.shape
    out = np.zeros((h + 1, w + 1), dtype=np.float64)
    out[1:, 1:] = np.cumsum(np.cumsum(mask.astype(np.float64), axis=0), axis=1)
    return out


def _box_sum(ii: np.ndarray, x0: int, y0: int, size: int) -> float:
    return float(ii[y0 + size, x0 + size] - ii[y0, x0 + size] - ii[y0 + size, x0] + ii[y0, x0])


def iter_candidates(frame: np.ndarray,
                    sizes: Sequence[int] = SIZES,
                    per_size: int = CANDIDATES_PER_SIZE) -> list[Candidate]:
    """枚举"整盒落在不透明轮廓内、且离轮廓质心最近"的候选贴片。

    排序键 = `(边长降, 距质心升)`：先试大的（覆盖更多信息），同尺寸里取贴中心的。
    锚点在这里就定死了 —— 它是**宿主自己选的盒**的几何性质，因此不需要从注册结果
    反推（§5 那条"待决"就是在问这个常数从哪来：答案是宿主侧轮廓，不是引擎）。
    """
    if frame.ndim != 3 or frame.shape[2] < 4:
        return []
    h, w = frame.shape[:2]
    mask = frame[..., 3] > ALPHA_MIN
    ys, xs = np.nonzero(mask)
    if len(xs) < 2:
        return []
    bx0, bx1 = int(xs.min()), int(xs.max()) + 1
    by0, by1 = int(ys.min()), int(ys.max()) + 1
    cx = float(xs.mean())
    cy = float(ys.mean())
    sub = mask[by0:by1, bx0:bx1]
    out: list[tuple[tuple[int, float], Candidate]] = []
    for size in sizes:
        if size > min(sub.shape) or size > min(w, h):
            continue
        ii = _integral(sub)
        area = float(size * size)
        boxes = [(x0, y0) for y0 in range(0, sub.shape[0] - size + 1, STEP)
                 for x0 in range(0, sub.shape[1] - size + 1, STEP)
                 if _box_sum(ii, x0, y0, size) / area >= MIN_COVERAGE]
        ranked = sorted(
            boxes,
            key=lambda p: (p[0] + bx0 + size / 2 - cx) ** 2
            + (p[1] + by0 + size / 2 - cy) ** 2)[:per_size]
        for (x0, y0) in ranked:
            (x0, y0) = (x0 + bx0, y0 + by0)
            box = np.ascontiguousarray(frame[y0:y0 + size, x0:x0 + size])
            anchor = (x0 + size / 2 - w / 2, y0 + size / 2 - h / 2)
            out.append(((-size, x0 + y0), Candidate(box, size, anchor)))
    return [c for _, c in sorted(out, key=lambda kv: kv[0])]


def select_template(engine: FidusEngine, frame: np.ndarray,
                    center: tuple[float, float],
                    sizes: Sequence[int] = SIZES,
                    log: Callable[[str, str], None] = lambda _k, _v: None
                    ) -> Optional[tuple[Candidate, float]]:
    """**先按尺寸降档，同档内取 ceiling 最大**的那只：ceiling 只在同一尺寸里可比。

    两条方向都要写死，因为两次都实机踩过：
    * 同档内取**最大** ceiling（= 自相似度最低）。取反会选到地板 `0.05` 的退化盒 ——
      H7 的 census 用 argmin 排的是 `s_engine` 本身，而 ceiling 与它反号。
    * 档间**先大后小，一旦有盒可注册就不再降档**。跨档比 ceiling 必输给小贴片
      （盒子越小越不自仿、上界越高），于是"全阶梯 argmax"实际等于"永远选最小档"。
      实机 H14 把这条打出来了：`y+150` 那五档，`160²` 最坏 **0.00 px**、`96²` **2.81 px**、
      `64²` 全部响亮退回、而 `32²` 报出 **(+97.07,+125.98)** 的数**且照样过了位移闭环**
      （不同先验给同一个错答案 ⇒ 是真孪生峰，不是先验相对artifact）。小贴片能**安静地撒谎**，
      大贴片只会**失败**；本功能的红线是"绝不跳到错位置"，所以宁可付大贴片的秒数。
    另按 §5 口径 3：ceiling 是注册期一次定值的状态量、运行期 `conf` 恒等于它 ⇒ 它不是健康度，
    只能用于选型；健康与否仍只认 `locate()` 里的位移闭环。
    """
    best: Optional[tuple[Candidate, float]] = None
    best_edge: Optional[int] = None
    tried = 0
    for cand in iter_candidates(frame, sizes):
        if best is not None and cand.edge != best_edge:
            break                     # 更大档已经有能注册的盒 ⇒ 小档不配再来比
        tried += 1
        try:
            ceiling = engine.register(cand.box, cand.belief_for(center))
        except EngineError as exc:
            log("候选被拒", f"{cand.edge}² @{cand.anchor} → {exc}")
            continue
        log("候选", f"{cand.edge}² @{tuple(round(v, 1) for v in cand.anchor)} "
                    f"ceiling={ceiling:.6f}")
        if best is None or ceiling > best[1]:
            best = (cand, ceiling)
            best_edge = cand.edge
    if best is None:
        log("模板选择", f"{tried} 个候选全被拒 ⇒ 退回")
    return best


def _settle(engine: FidusEngine, label: str,
            log: Callable[[str, str], None]) -> Optional[tuple[float, float, float]]:
    """连读到"相邻两发不差过 `STABLE_PX`"为止；一直不稳定就回 `None`。

    为什么不能只读一发（H13 的 A 相就是证据）：**更新路径**在目标被搬走后是逐发爬的，
    每发走 3–14 px，8 发内还没停；而**冷获取**（注册后头几发）在 0.01–0.03 px 上重合。
    判"锁住了"只能靠读数自己定住——`conf` 在正确锁定与钉死读数是同一个值。

    `FidusTargetLost` **不算终点**：每发失稳会把搜索窗再撑开 64 px
    （`half = 1.5·s + 48 + 64·lost_streak`），H11 实测信念偏 440 px 时正是靠这条
    在第 6 发锁上。所以异常只记最后一发、继续读到上限为止（H14 复证：偏 ≥150 px
    时首发即抛，一抛就退 = 把唯一那条"先验错得远"的救回路自己剪掉）。
    """
    prev: Optional[tuple[float, float, float]] = None
    last_err = ""
    for i in range(1, ACQUIRE_READS + 1):
        try:
            x, y, conf = engine.estimate()
        except EngineError as exc:
            last_err = str(exc)
            prev = None            # 失稳之后两发不连续，稳定判据重新开始算
            continue
        if prev is not None and max(abs(x - prev[0]), abs(y - prev[1])) <= STABLE_PX:
            log("读数", f"{label} 第 {i} 发定住 ({x:.2f},{y:.2f})")
            return (x, y, conf)
        prev = (x, y, conf)
    log("读数", f"{label} {ACQUIRE_READS} 发内没定住"
                f"{f'（末发 {last_err}）' if last_err else ''} ⇒ 退回")
    return None


def locate(engine: FidusEngine, frame: np.ndarray,
           believed_center: tuple[float, float], *,
           move: Callable[[float, float], None],
           sizes: Sequence[int] = SIZES,
           move_px: float = MOVE_PX,
           tol_px: float = TOL_PX,
           log: Callable[[str, str], None] = lambda _k, _v: None
           ) -> Optional[Fix]:
    """测出 surface 的真实中心；不可信就回 `None`（调用方整段退回合成数）。

    `move(dx, dy)` 由调用方提供：把 surface 摆到"当前位置 + 该增量"，**并在合成器
    提交之后**才返回（探针里靠 Qt 事件泵等这一手）。闭环要的就是它。

    协议是**两次冷获取**（H13 的 D 相：18/18 通过、离独立真值最坏 0.03 px）：
    注册→读到定住 `a`→摆 +`move_px`→**用 `a` 自己当先验重注册**→读到定住 `b`→
    验 `b − a ≈ 请求位移`。第二次的先验取引擎自己的稳态读数而不是我方信念，
    于是那一次冷读的先验几乎必真（偏差只剩"合成器有没有照请求摆"这一件事，
    而那正是闭环要测的）。

    返回的 `Fix.center` 取自 `a`——**探针位移之前**那一刻的中心，而 surface 停在位移之后；
    调用方紧接着就要用这个数去摆位，那一次摆位会把探针位移一起吃掉（§5.1 的 B 语义）。
    """
    engine.calibrate()
    picked = select_template(engine, frame, believed_center, sizes, log)
    if picked is None:
        return None
    cand, ceiling = picked
    engine.register(cand.box, cand.belief_for(believed_center))  # 选定后重新定窗
    a = _settle(engine, "获取", log)
    if a is None:
        return None
    log("获取读数", f"贴片中心 ({a[0]:.2f},{a[1]:.2f}) conf={a[2]} ceiling={ceiling}")

    move(move_px, 0.0)
    try:
        engine.register(cand.box, (a[0] + move_px, a[1]))
    except EngineError as exc:
        log("闭环重注册", f"{exc} ⇒ 退回")
        return None
    b = _settle(engine, "闭环", log)
    if b is None:
        return None
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx - move_px) > tol_px or abs(dy) > tol_px:
        log("位移闭环", f"读数差 ({dx:.2f},{dy:.2f}) vs 请求 ({move_px:.0f},0)"
                        f" 超容差 {tol_px} ⇒ 读数不可信，退回")
        return None

    # 读数盯的是贴片中心；锚点把它换算回 surface 中心。取位移**之前**那一发。
    return Fix(center=(a[0] - cand.anchor[0], a[1] - cand.anchor[1]),
               conf=a[2], edge=cand.edge, ceiling=ceiling)


# ─────────────────────────────────────────────  线程亲和的执行器
#
# wheel 不内置线程（契约 H2 已裁：宿主自持工作线程，对象在使用线程上创建），
# 且跨线程误用抛的 PanicException 派生自 BaseException ⇒ `except Exception` 接不住。
# 所以这里只允许**一条**fidus 线程：引擎在这条线程上建、在这条线程上用、
# 并且 `calibrate_once` 每会话只付一次。切换按钮每次都往队列里投一个作业即可。

_JOBS: "queue.Queue[Optional[tuple]]" = queue.Queue()
_LOCK = threading.Lock()
_THREAD: Optional[threading.Thread] = None
_ENGINE: Optional[FidusEngine] = None


def _worker_loop() -> None:
    global _ENGINE
    while True:
        job = _JOBS.get()
        if job is None:  # 只有测试收尾会投这个
            return
        frame, believed_center, move, on_done, log = job
        fix: Optional[Fix] = None
        try:
            if _ENGINE is None:
                _ENGINE = FidusEngine()
            fix = locate(_ENGINE, frame, believed_center, move=move, log=log)
        except BaseException as exc:  # noqa: BLE001 - 引擎异常不吃跨线程 Panic
            log("定位作业", f"{type(exc).__name__}: {exc} ⇒ 退回")
            fix = None
        try:
            on_done(fix)
        except BaseException as exc:  # noqa: BLE001 - 回调在 GUI 线程之外，绝不冒泡
            log("定位回调", f"{type(exc).__name__}: {exc}")


def request_locate(frame: np.ndarray,
                   believed_center: tuple[float, float], *,
                   move: Callable[[float, float], None],
                   on_done: Callable[[Optional[Fix]], None],
                   log: Callable[[str, str], None] = lambda _k, _v: None) -> None:
    """异步发起一次测量：立刻返回，结果（或 `None`）经 `on_done` 回到调用方。

    `on_done` 在 fidus 线程上被调用 —— 接收方自己 marshal 回 GUI 线程。
    """
    global _THREAD
    with _LOCK:
        if _THREAD is None or not _THREAD.is_alive():
            _THREAD = threading.Thread(target=_worker_loop, daemon=True,
                                       name="meapet-fidus")
            _THREAD.start()
    _JOBS.put((frame, believed_center, move, on_done, log))


def shutdown() -> None:
    """测试收尾用：请 fidus 线程退出（不等待，等不等属于调用方）。"""
    _JOBS.put(None)

