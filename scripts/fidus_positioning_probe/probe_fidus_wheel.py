#!/usr/bin/env python3
"""cp312 fidus wheel 的 L1 契约探针（定位线，非桌面、非 L3）。

能证明什么（agents-rules §10 证据等级）
--------------------------------------
* **L1**：§12b 交付形态（Python wheel + `import`）在 meapet 的 `.venv`（CPython 3.12）
  里成立——pip 装得进、import 得起、原生 `.so` 载得动、公开 API 面可枚举。这直接让
  提案 §3 的 **A9**（"fidus 结果能被 MeaPet ctypes 直接调用"=不成立）**作废**：调用通路
  不再是 ctypes，而是 wheel+import。
* **L1**：无合成器时 `build_wayland()` 的**失败形态**是响亮的 `FidusInitError`
  （`FidusError` 子类），非段错误、非挂起——即提案 §5-5"非 Wayland = 显式 Error"
  在轮子边界可被 meapet `except FidusError` 兜住。

不能证明什么（不静默）
--------------------
* **L3 精度 / 帧率 / 长稳**：`calibrate_once`/`estimate` 会连真实合成器、开 layer-shell
  面并在用户桌面上做 ≥56–140 次整幅截屏（秒级冻结）——属人工授权的桌面 mutation
  （§10.4 / §11.4），本探针**绝不触碰**。
* **A10**（分数缩放 / 多输出下 request→actual 映射是否失效 = 本机制收益前提）：
  本探针零桌面改动，**给不出** A10 的任何数字。

调用形态的关键事实（本探针自证，纠正任何"`Fidus()` 直接构造"的误记）
--------------------------------------------------------------------
`Fidus` 不能直接实例化（pyo3 无 `#[new]`）；唯一工厂是 staticmethod `Fidus.build_wayland()`，
而它**连接合成器**。`register_target/calibrate_once/estimate/gate_status` 全是**实例方法**
⇒ **没有任何"离线可得一个实例"的廉价探针路径**；连 `gate_status` 都要先 `build_wayland`。

跑法：`.venv/bin/python scripts/fidus_positioning_probe/probe_fidus_wheel.py`
（无参数；不建窗口、不截屏、不连合成器。）
"""
from __future__ import annotations

import os
import subprocess
import sys

# 失败形态用例：在**剥离 WAYLAND_*** 的子进程里连一次，验证干净失败而非崩/挂。
# 自我 fork 而非就地调用：本脚本很可能运行在用户真实 Wayland 会话里，就地 build_wayland()
# 会在那台桌面上开 layer-shell 面（=未授权的可见副作用）。剥离环境让子进程必定走"无合成器"
# 这条确定性的错误分支。
_CHILD = (
    "import fidus\n"
    "try:\n"
    "    o = fidus.Fidus.build_wayland()\n"
    "    print('UNEXPECTED-OK', repr(o))\n"
    "except BaseException as e:\n"
    "    print('RAISED', type(e).__name__, isinstance(e, fidus.FidusError))\n"
)


def _fail_mode() -> tuple[str, int]:
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAYLAND_DISPLAY", "WAYLAND_SOCKET")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        r = subprocess.run(
            [sys.executable, "-c", _CHILD],
            capture_output=True, text=True, env=env, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return ("TIMEOUT-挂起(15s)", -1)
    line = next((ln for ln in r.stdout.splitlines()
                 if ln.startswith(("RAISED", "UNEXPECTED-OK"))), r.stdout.strip() or "<无输出>")
    return (line, r.returncode)


def main() -> int:
    print(f"interpreter: {sys.version.split()[0]}  ({sys.executable})")
    try:
        import fidus
    except Exception as exc:  # 装不上就是最响亮的失败
        print(f"IMPORT-FAIL: {type(exc).__name__}: {exc}")
        print("→ 先 `pip install --no-deps <cpNN-cpNN wheel.whl>`；"
              "若 venv 与 wheel 的 cp tag 不符会 ModuleNotFoundError(版本代理坑)。")
        return 2

    print("\n== A9 作废证据：wheel+import 通路 ==")
    print("version:", fidus.__version__)
    print("native .so:", getattr(getattr(fidus, "fidus", None), "__file__", "n/a"))

    print("\n== 公开 API 面（__all__）==")
    print(sorted(getattr(fidus, "__all__", [])))

    print("\n== 调用形态自证：Fidus 直接构造 vs build_wayland 工厂 ==")
    try:
        fidus.Fidus()
        print("Fidus(): 竟然可构造 —— 与 pyo3 无 #[new] 的假设矛盾，需重判")
    except TypeError as exc:
        print("Fidus() ->", type(exc).__name__, ":", exc, "（预期：不能直接实例化）")
    print("build_wayland 是 staticmethod?:",
          type(fidus.Fidus.__dict__.get("build_wayland")).__name__ == "staticmethod")
    for m in ("register_target", "calibrate_once", "estimate", "gate_status"):
        print(f"Fidus.{m}: {type(fidus.Fidus.__dict__.get(m)).__name__}")

    print("\n== CalibInfo 只读属性 ==")
    print([a for a in dir(fidus.CalibInfo) if not a.startswith("_")])

    print("\n== 异常层级（meapet 可 except 的兜底）==")
    print("FidusError 子类:",
          sorted(n for n in dir(fidus)
                 if n.endswith(("Error", "Lost", "Calibrated", "NoTarget", "Untrackable"))
                 and n != "FidusError"))

    print("\n== L1 失败形态：剥离 WAYLAND_* 的子进程连一次 ==")
    line, rc = _fail_mode()
    print(f"child: {line}   rc={rc}")
    clean = line.startswith("RAISED") and "True" in line and rc == 0
    print("判定:", "OK 响亮失败=FidusError 子类" if clean
          else "非预期：需人工看这条（崩/挂/意外成功都会落这里）")
    return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
