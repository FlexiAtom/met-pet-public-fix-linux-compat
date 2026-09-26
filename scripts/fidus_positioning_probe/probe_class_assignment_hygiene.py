#!/usr/bin/env python3
"""类级污染面 · `Fidus.confidence_ceiling` 到底能不能"洗干净"（fixture-hygiene 的实据）

问什么（一条）：把 ceiling 读面当成可被夹具污染的类属性时，**什么动作等于恢复**？

fidus 侧提案 `meapet-class-assignment-fixture-hygiene` 建议的收口动作是
`del fidus.Fidus.confidence_ceiling`。本探针先量它到底做什么，再给出可用的写法 —— 因为
"删掉读面"与"恢复读面"在语义上相反，而它长得像同一行代码的两个方向。

实测结论（2026-09-25 本机，锚点见 ANCHOR）：
* `confidence_ceiling` 是 `getset_descriptor`，确实住在 `vars(Fidus)` 里 ⇒ 类级赋值可穿透。
* `vars(Fidus)` 是 **mappingproxy** ⇒ 没有 `.pop`，"摘下来再放回"只能 `vars(...)[name]` 读 +
  `setattr` 写。
* 实例**没有** `__dict__` ⇒ 无法用实例属性遮蔽；污染只可能发生在类上。
* `del Fidus.confidence_ceiling` 把 descriptor 整个摘走 ⇒ 之后 `hasattr(inst, …)` 为 False，
  即**公共读面在本进程余下时间里永久消失**。它是破坏，不是清理；且**从未污染时也一样**
  ——贵方 §5 写的"（若从未赋值则 no-op）"在本轮件上不成立，那一句会静默废掉这条判据用的尺子。
* 正确的收口 = 先存 descriptor、末尾 `setattr` 回去（逐位复现原生值）。

用法（纯 CPU，不上屏、不动合成器）：
```
.venv/bin/python scripts/fidus_positioning_probe/probe_class_assignment_hygiene.py
```

失效边界：只主张本机该 wheel 装配下 PyO3 类属性的行为；不主张 fidus 改码后仍如此（若哪天
把该属性换成 `#[pyclass(getter)]` 之外的实现，descriptor 类型会变，本件的存/取写法要跟着改）。
"""
from __future__ import annotations

import sys

import numpy as np

import fidus

ANCHOR = "v0.1.0-beta.1-58-ge4947aa"
NAME = "confidence_ceiling"


def fact(label, value) -> None:
    print(f"  {label}: {value}", flush=True)


def poisoned_template() -> np.ndarray:
    """一张能注册上、但 ceiling 会落到地板的 64×64 模板（只要注册成功就有原生值可比对）。"""
    r, c = np.mgrid[0:64, 0:64]
    return np.ascontiguousarray(np.dstack([
        r % 32 * 8, c % 32 * 8, ((r // 8) + (c // 8)) % 2 * 200,
        np.full((64, 64), 255)]).astype(np.uint8))


def main() -> int:
    cls = fidus.Fidus
    print("=== 类级污染面 · 存/取写法是否等于恢复 ===")
    anchor = getattr(fidus, "__git_commit__", None)
    fact("fidus.__git_commit__", f"{anchor!r} 期望 {ANCHOR!r} ⇒ "
         + ("命中" if anchor == ANCHOR else "✗ 不是本件的件"))
    fact("vars(Fidus) 类型", type(vars(cls)).__name__
         + ("　⇒ 无 .pop，只能读 + setattr" if not hasattr(vars(cls), "pop") else ""))
    desc = vars(cls).get(NAME)
    fact(f"vars(Fidus)[{NAME!r}]", f"{type(desc).__name__ if desc is not None else '不在 vars 里'}")
    if desc is None:
        print("VERDICT-SURFACE  : ✗ 该 wheel 没把这个名字放在类 dict 里 ⇒ 本问不产出")
        return 1

    try:
        eng = cls.build_wayland()
    except BaseException as exc:  # noqa: BLE001  build 需要显示环境；失败就只交类面结论
        fact("build_wayland", f"RAISED {type(exc).__name__} ⇒ 无引擎实例，实例侧读数不产出")
        print("VERDICT-SURFACE  : 类面已测，实例面本轮不可判")
        return 0

    native: list = []
    try:
        native.append(eng.confidence_ceiling)   # 注册前：应为 None
        eng.register_target(poisoned_template(), True)
        native_val = eng.confidence_ceiling
        native.append(native_val)
        fact("注册前", repr(native[0]))
        fact("注册后原生值", f"{native_val!r}")
    except BaseException as exc:  # noqa: BLE001
        fact("注册", f"RAISED {type(exc).__name__}: {str(exc)[:90]} ⇒ 无原生值，逐位比对降级")
        native_val = None

    # 候选 A0：**从未赋值**就 del —— 贵方 §5 的字面处方写着"（若从未赋值则 no-op）"。
    # 这一格必须单独跑：如果它是 no-op，那"随手加一句 teardown"是免费的；如果不是，那这条
    # 处方本身就是把公共读面删掉的破坏动作，加进任何夹具都会静默废掉 `advertised == applied`。
    delattr(cls, NAME)
    clean_alive = hasattr(eng, NAME)
    fact("候选 A0 · 干净态直接 delattr", f"实例 hasattr={clean_alive} ⇒ "
         + ("**不是 no-op**：读面被摘走" if not clean_alive else "确为 no-op（描述符还在）"))
    err_repr = "<未测>"
    if not clean_alive:
        try:
            err_repr = repr(getattr(eng, NAME))
        except BaseException as exc:  # noqa: BLE001
            err_repr = f"{type(exc).__name__}: {exc}"
        fact("  摘走后实例读", err_repr)
    setattr(cls, NAME, desc)                    # 先复原，再走污染流程
    fact("  A0 之后 setattr 复原", f"实例读到 {getattr(eng, NAME, '<抛/无>')!r}")

    # 污染：类级 float
    setattr(cls, NAME, 0.5)
    via_inst = getattr(eng, NAME, "<抛/无>")
    fact("污染 setattr(Fidus, NAME, 0.5)", f"实例读到 {via_inst!r} ⇒ "
         + ("类级污染**会**穿透到已存在的实例" if via_inst == 0.5 else "未穿透（本件假设不成立）"))

    # 候选收口 A：del（贵方提案的写法，在**已污染**态下）
    delattr(cls, NAME)
    alive = hasattr(eng, NAME)
    fact("候选 A · delattr(Fidus, NAME)（已污染态）", f"实例 hasattr={alive} ⇒ "
         + ("**读面整个消失**（破坏，不是恢复）" if not alive else "仍在"))

    # 候选收口 B：把原 descriptor 赋回去
    setattr(cls, NAME, desc)
    restored = getattr(eng, NAME, "<抛/无>")
    bitexact = restored == native_val if isinstance(restored, float) else None
    fact("候选 B · setattr(Fidus, NAME, 原 descriptor)",
         f"实例读到 {restored!r}；与原生逐位相等 = {bitexact}")
    if native_val is None:
        print("VERDICT-RESTORE  : 恢复面存在（类型层），数值层本轮无原生值可比 ⇒ 只主张类型恢复")
    else:
        print("VERDICT-RESTORE  : " + ("存 descriptor → setattr 回去 ⇒ **逐位复原**，这是可用收口"
              if bitexact else "✗ setattr 回去仍不等 ⇒ 该名字不可逆，夹具须避免污染"))
    print("VERDICT-DEL      : " + ("delattr 在**干净态与污染态都一样**——永久摘面 ⇒ 不得当 fixture "
          "teardown 用；贵方 §5「若从未赋值则 no-op」一句在本轮件上不成立"
          if (not alive and not clean_alive) else "delattr 后读面仍在（与预期不符，须回看）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
