"""H18 · Qt 自己的窗口（气泡／向导）在 xcb 与 wayland 两条路上落点是否一致。

出处：`~/.Athena/projects/meapet/pool/wayland-native-product-scope.md` §2 反证表 **H4**
——"Qt 原生窗口（气泡／向导）在 wayland 下与 xcb 下等价"这一条是**未取**的那格，
也是该项停在 pool 的唯一原因（§5：promote 门槛＝这条取到 L3，或人工裁"不在承诺面内"）。

为什么必须独立取一次：桌宠本体走的是桥接层那条路（H12/H15/H17 量的都是它），
而气泡与向导是**普通 Qt 顶层窗口**。`widgets.py` 里 `DialogueBox.__init__` 那条
`Qt.X11BypassWindowManagerHint` 只在 `platformName() == "xcb"` 时加 ——
wayland 下这个标志没有对应物，气泡就退化成受合成器摆放策略支配的一个普通窗口。
niri 是**平铺**合成器，而 `probe_wd_translucency.py` 已实测过：Wayland 上 Qt 的
top-level `move()` 不被采纳（挪 800 px 屏幕零变化）。两条凑在一起的后果没人体检过。

量法（每一步都对应一条本系列已经踩过的坑）：
1. **落点只认 grim 两帧之差**，不认 Qt 自述。A10 的 H1 已经证伪过四条自述通道
   （`mapToGlobal`／`windowHandle().position()`／`geometry()`／`frameGeometry`
   在全平铺下**全部报原点**）——所以自述在这里只是"信念"，是被测对象之一，不是尺子。
2. **背景帧在 show 之前取，之后绝不 hide→show**。H16 实测 hide→show 会让底下的
   终端重新排版一次（51 万像素变化），当场冲垮判读。
3. **采集期间一个字都不许往终端打**。本探针跑在用户桌面里那个**全屏终端**中，
   终端的每一次刷新都进 grim 帧。真机踩过一回：两个请求位之间把上一位读数 print
   出去，下一对差帧就被终端刷新淹没——"变化区域"宽到 1366 px，三个位报出
   (-620,-380)/(-187,252)/(-883,-108) 这种荒唐残差。于是读数一律先缓冲，
   出口只放在"这一对帧已经抓完"之后，且下一位开抓前再等一次刷新落定。
4. **落点 = 拿窗口自己的尺寸在差帧掩码上滑一个窗、取覆盖最多处**，不是变化像素的
   并集 bbox。并集对孤点零抵抗：光标闪一下、别的窗口动一个字符，bbox 左上一角
   就被顶到 (0,0)。滑窗自带覆盖率，够不着阈值就是"这么大一块新东西不在屏上"，
   当场报量具断，不硬凑一个落点。
5. **一个请求位不够**。三位的残差全一样 = 系统性偏移；各走各的 = 合成器在摆位。
   三位还必须互不重叠：位若重叠，交集区两次都是气泡 ⇒ 差帧里那里没变化，
   读数只剩没盖住的那条边。
6. **环会进差帧，读数自带 ±4 px 余量**。原以为气泡带 `WA_ShowWithoutActivating`
   （产品自己就这么设）就不改焦点、niri 那条"焦点环画成窗口底下实心矩形"的干扰进不来；
   实测**这条预期不成立**：niri 对气泡自报 `focused=True`，而差帧亮区实测 207×89
   ＝窗口 199×81 每边各多 4 px，正是环的宽度。于是滑窗在那个 207×89 里可平移，
   落点读数有 **±4 px** 的余量（与 niri `tile_pos` 相差 4 px 即此源）。
   ⇒ 判红阈值若比 4 px 更严，量的就是环的位置而不是窗口的位置；本探针判的是数百像素级
   的"落没落在请求位"，不被它影响，但**别拿这 4 px 去归因任何东西**。
   向导同理且更糊：它会重排整幅，落点读数只报不判。
7. **只在 scale 1.0 上判红**。物理／逻辑混算的错法在 §12e 量过（把逻辑当物理用会错
   289–588 px），本探针不重做那套换算：scale≠1 时只出数，判据降级为"未判"。

用法（人在环；`--both` 由本脚本自己按两个后端各起一子进程）：
    QT_QPA_PLATFORM=xcb     .venv/bin/python scripts/fidus_positioning_probe/probe_h18_qt_native_ab.py
    QT_QPA_PLATFORM=wayland .venv/bin/python scripts/fidus_positioning_probe/probe_h18_qt_native_ab.py
    .venv/bin/python scripts/fidus_positioning_probe/probe_h18_qt_native_ab.py --both [--eyeball 6]

退出码：0＝两臂都量到了且判据成立／不判；1＝量具断（抓不到帧、窗口没上屏）；2＝判据红。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))

import probe_a10_geometry as A10  # noqa: E402
import probe_h12_product_path as H12  # noqa: E402

# 三个互不重叠的请求位（本机 1366×768，气泡 ~199×81）——为什么三位、为什么隔开，见头注 5。
BUBBLE_REQS = ((620, 380), (200, 200), (900, 560))
DIFF_THRESH = 12          # 均值差过这个值算变了：压掉压缩噪声与光标闪烁
FILL_FLOOR = 0.60         # 滑窗覆盖率低于此 ⇒ 那么大一块新东西不在屏上 = 量具断
NOISE_CAP = 500           # 一整对帧的差像素少于这个数 ⇒ 画面根本没动（终端刷新那点量）
RELAID_FRACTION = 0.35    # 差像素超过这么多屏 ⇒ 不是"窗口盖上去"，是平铺整幅重排
SETTLE_MS = 400           # 每位开抓前等刷新落定（含倾倒上一位读数的副作用）
OUTPUT = os.environ.get("H18_OUTPUT", "eDP-1")   # 本机单输出；多输出屏请显式指定


# ------------------------------------------------------------- 缓冲式读数
_PENDING: "list[str]" = []


def fact(label, value) -> None:
    """攒一条读数。**不**打印——见头注 3。"""
    _PENDING.append(f"  [{label}] {value}")


def dump() -> None:
    """唯一的打印出口，只允许在"这一对帧已抓完"之后调用。"""
    if not _PENDING:
        return
    sys.stdout.write("\n".join(_PENDING) + "\n")
    sys.stdout.flush()
    _PENDING.clear()


# ------------------------------------------------------------- 两帧之差
def change_mask(before, after, sign: int = 0):
    """方向化的差帧掩码。

    `sign`：0＝任何变化（找落点用它）；+1＝只算变亮、-1＝只算变暗。窗口被 `move()`
    到新位置时，一次差帧里同时有"新盖"和"露出"两坨，0 会一起框进来——靠头注 4 的
    滑窗把它们分开，±只用来报"这块到底是长出来的还是消失的"。
    """
    if before is None or after is None or before.shape != after.shape:
        return None
    delta = (after.astype(np.int16) - before.astype(np.int16)).mean(axis=2)
    if sign > 0:
        return delta > DIFF_THRESH
    if sign < 0:
        return delta < -DIFF_THRESH
    return np.abs(delta) > DIFF_THRESH


def locate_box(mask, w: int, h: int):
    """拿 w×h 的窗在掩码上滑到覆盖最多处 → (x, y, 覆盖率)；放不下返回 None。"""
    if mask is None or w <= 0 or h <= 0 or w > mask.shape[1] or h > mask.shape[0]:
        return None
    integ = np.pad(mask.astype(np.int32), ((1, 1), (1, 1)))
    integ = integ.cumsum(0, dtype=np.int32).cumsum(1, dtype=np.int32)
    sums = integ[h:, w:] - integ[:-h, w:] - integ[h:, :-w] + integ[:-h, :-w]
    y, x = divmod(int(np.argmax(sums)), sums.shape[1])
    return (int(x), int(y), int(sums[y, x]) / float(w * h))


def bbox_of(mask):
    """掩码的并集 bbox —— 只当诊断报出来，绝不当落点用（头注 4）。"""
    if mask is None:
        return None
    rows = np.nonzero(mask.any(axis=1))[0]
    cols = np.nonzero(mask.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return (int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1),
            int(rows[-1] - rows[0] + 1), int(mask.sum()))


# ------------------------------------------------------------- 环境事实
def niri_scale() -> float:
    """输出 scale —— 只读。

    别写成 `niri msg output <名字> scale <值>`：那是**设置**命令（本机实测会索要 SCALE
    参数），探针没有改分辨率的授权。JSON 里 scale 在 `logical.scale`。
    """
    try:
        out = subprocess.run(["niri", "msg", "-j", "outputs"],
                             capture_output=True, timeout=5).stdout.decode()
        return float(json.loads(out)[OUTPUT]["logical"]["scale"])
    except Exception:
        return float("nan")


def niri_windows():
    """本进程的 niri 窗口事实（只读 `niri msg -j windows`，按 pid 认领）。"""
    try:
        out = subprocess.run(["niri", "msg", "-j", "windows"],
                             capture_output=True, timeout=5).stdout.decode()
        return [w for w in json.loads(out) if w.get("pid") == os.getpid()]
    except Exception:
        return []


def pump(app, ms: int) -> None:
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.008)


# ------------------------------------------------------------- 量
def measure(app, win, *, positions, tag, eyeball: int, search_sizes=()):
    """把窗口逐位摆过去，每个请求位出一条独立落点读数。

    只 `move()`，绝不 hide→show（头注 2）；`dump()` 只出现在 after 抓完之后（头注 3）。
    `search_sizes` 是**额外**的滑窗尺寸候选（窗口自述尺寸永远在列）；向导要多带一个
    "请求的 900×620"，因为"合成器给不给这个尺寸"本身就是问题，不能只拿自述当尺子。
    """
    from PyQt5.QtCore import QPoint

    readings = []
    prev_box = None
    first = True
    for i, at in enumerate(positions or [None]):
        pump(app, SETTLE_MS)                       # 让上一次倾倒读数的刷新落定
        before = A10.capture(f"h18_{tag}_p{i}_before")
        if before is None:
            fact(tag, f"位{i} 量具断：拿不到背景帧")
            dump()
            continue
        if first:
            first = False
            win.show()
        if at is not None:
            win.move(*at)
        pump(app, 1200)
        after = A10.capture(f"h18_{tag}_p{i}_after")
        if after is None:
            fact(tag, f"位{i} 量具断：抓不到前景帧")
            dump()
            continue

        # ↓ 纯计算 + 缓冲，不落终端
        geo = win.geometry()
        glob = win.mapToGlobal(QPoint(0, 0))
        any_mask = change_mask(before, after, 0)
        sizes = tuple(dict.fromkeys(tuple(search_sizes)
                                    + ((geo.width(), geo.height()),)))
        best = None
        for (w, h) in sizes:
            box = locate_box(any_mask, w, h)
            if box is None:
                fact(tag, f"位{i} 滑窗 {w}×{h} 放不下（屏比窗还小？）")
                continue
            fact(tag, f"位{i} 滑窗 {w}×{h} → {box[0]},{box[1]} 覆盖率 {box[2]:.2f}")
            if best is None or box[2] > best[2]:
                best = (box[0], box[1], box[2], w, h)
        npx = int(any_mask.sum()) if any_mask is not None else 0
        fact(tag, f"位{i} 请求={at} ｜Qt 自述 geometry={geo.x()},{geo.y()} "
                  f"{geo.width()}×{geo.height()} ｜mapToGlobal={glob.x()},{glob.y()}")
        fact(tag, f"位{i} 差帧并集 bbox={bbox_of(any_mask)} 共 {npx} px"
                  "（诊断，不当落点）")
        if i and npx < NOISE_CAP:
            # 自述已经改了、屏上却几乎什么都没有 ⇒ 这一位不是"没上屏"，是"没被挪动"：
            # 它还在上一位那里。这条判读要单独说，否则会被误记成"窗口消失了"。
            fact(tag, f"位{i} 整幅只变了 {npx} px ⇒ 窗口没被挪动（自述已改、画面没改），"
                      "它还停在上一位的落点上")
        for wnd in niri_windows():
            lay = wnd.get("layout") or {}
            fact(f"{tag}·niri", f"位{i} id={wnd.get('id')} floating={wnd.get('is_floating')} "
                 f"focused={wnd.get('is_focused')} title={wnd.get('title')!r} "
                 f"tile_pos={lay.get('tile_pos_in_workspace_view')} "
                 f"offset={lay.get('window_offset_in_tile')}")
        # 整幅重排的差帧里"窗口落在哪"这一说不成立：平铺让位会把全屏刷一遍，
        # 于是任何尺寸的滑窗都能拿到 ~1.00 覆盖率——那不是窗口，那是布局本身动了。
        relaid = any_mask is not None and npx > RELAID_FRACTION * any_mask.size
        if relaid:
            fact(tag, f"位{i} 变化覆盖 {npx / any_mask.size:.0%} 的屏 ⇒ 整幅重排"
                      "（平铺给新窗口腾位）。落点无从谈起，只有自述尺寸可报："
                      f"{geo.width()}×{geo.height()}（请求的是 "
                      f"{(search_sizes[0] if search_sizes else '?')}）")
            readings.append({"req": at, "box": None, "relaid": True, "fill": 0.0,
                             "geo": (geo.x(), geo.y(), geo.width(), geo.height())})
        elif best is not None and best[2] >= FILL_FLOOR:
            x0, y0, fill, w, h = best
            fact(tag, f"位{i} 落点={x0},{y0} {w}×{h}（覆盖率 {fill:.2f}）"
                 + (f" ⇒ 残差 ({x0 - at[0]}, {y0 - at[1]}) px" if at else ""))
            prev_box = (x0, y0, w, h)
            readings.append({"req": at, "box": prev_box, "fill": fill,
                             "geo": (geo.x(), geo.y(), geo.width(), geo.height())})
        elif at is not None and npx < NOISE_CAP and prev_box is not None:
            # 画面几乎没改 ⇒ 不是"窗口消失了"，是"窗口没跟着 move 走"：
            # 落点沿用上一位，这一位照样记账，只是它自己承认是 stale。
            fact(tag, f"位{i} 落点沿用上一位 {prev_box[0]},{prev_box[1]}"
                      "（窗口没跟着动）")
            readings.append({"req": at, "box": prev_box, "fill": 0.0, "stale": True,
                             "geo": (geo.x(), geo.y(), geo.width(), geo.height())})
        else:
            fact(tag, f"位{i} 最好一处覆盖率 "
                 f"{'—' if best is None else f'{best[2]:.2f}'} < {FILL_FLOOR}"
                 f" ⇒ 屏上没有这么大一块新东西 = 量具断，不产出落点")
        dump()
        if eyeball and best and best[2] >= FILL_FLOOR:
            _eyeball(app, eyeball, f"{tag}·位{i}", best[3], best[4])
    return readings


def _eyeball(app, secs, tag, w, h):
    """问人眼要打印，所以只允许发生在"没有帧在飞"的位置（头注 3 的同一纪律）。"""
    from PyQt5.QtCore import QTimer

    print(f"  [{tag}·人眼] 窗口看得见吗？它现在在的位置对得上刚才请求的哪一处？"
          f"尺寸 {w}×{h} 对得上吗？有没有标题栏／装饰？点它有没有反应"
          f"（气泡应当穿透、向导应当可点）？", flush=True)
    loop = QTimer()
    loop.setInterval(50)
    loop.timeout.connect(app.processEvents)
    loop.start()
    time.sleep(secs)
    loop.stop()


def arm_bubble(app, *, positions, eyeball: int):
    from PyQt5.QtCore import Qt

    from meapet.desktop.widgets import DialogueBox

    win = DialogueBox()
    bypass = int(Qt.X11BypassWindowManagerHint)    # 真值 0x400；Qt4 的 0x80 是另一个世界
    fact("气泡·旗标", f"platformName={app.platformName()} "
         f"｜X11BypassWindowManagerHint={'在' if int(win.windowFlags()) & bypass else '不在'}"
         f" ｜flags=0x{int(win.windowFlags()):x}")
    # show_text 自己会 show()（DialogueBox.show_text 末尾）——先用它把尺寸算出来，
    # 再按头注 2 藏回去，背景帧才干净；unmap 造成的排版变化全落在第一个 before 之前。
    win.show_text("定位取证 H18 气泡臂", 600000)
    win.hide()
    pump(app, 800)
    size = (win.width(), win.height())
    got = measure(app, win, positions=positions, tag="气泡", eyeball=eyeball,
                  search_sizes=(size,))
    dump()
    win.hide()
    return got


def arm_wizard(app, tmp, *, eyeball: int):
    """向导只读：配置与窗口态都指向 /tmp 里的副本，不落真文件、不点保存。"""
    from wizard.app import SetupWizard

    cfg = tmp / "wizard_config.json"
    cfg.write_text((REPO_ROOT / "config.example.json").read_text(encoding="utf-8"),
                   encoding="utf-8")
    win = SetupWizard(config_path=str(cfg), window_state_path=str(tmp / "wizard_state.json"))
    win.resize(900, 620)      # 与产品初始尺寸同源：量的是"合成器给不给这个尺寸"
    # 两个滑窗尺寸：请求的那个大（900×620），自述的那个由 measure 自己补
    got = measure(app, win, positions=None, tag="向导", eyeball=eyeball,
                  search_sizes=((900, 620),))
    dump()
    return got


def run_arm(app_name: str, eyeball: int, positions) -> int:
    from PyQt5.QtWidgets import QApplication

    os.environ["QT_QPA_PLATFORM"] = app_name
    app = QApplication.instance() or QApplication(sys.argv[:1])
    fact("臂", f"QT_QPA_PLATFORM={app.platformName()}")
    scale = niri_scale()
    fact("环境", f"scale={scale}（判红只在 1.0；其余只出数）")
    dump()
    rc = 0
    verdict = "量具断"
    with tempfile.TemporaryDirectory() as tdir:
        readings = arm_bubble(app, positions=positions, eyeball=eyeball)
        if len(readings) < len(positions):
            fact("判据", f"气泡只有 {len(readings)}/{len(positions)} 个请求位量到落点"
                        " ⇒ 至少一位没上屏 = 量具断")
            rc = max(rc, 1)
        boxed = [r for r in readings if r["box"] is not None]
        if boxed:
            errs = [(r["box"][0] - r["req"][0], r["box"][1] - r["req"][1])
                    for r in boxed]
            lands = [(r["box"][0], r["box"][1]) for r in boxed]
            stale = [i for i, r in enumerate(boxed) if r.get("stale")]
            fact("气泡·总账", f"残差逐位={errs} ｜落点={lands}"
                 + (f" ｜第 {stale} 位是沿用上一位（没跟着动）" if stale else ""))
            if scale != 1.0:
                verdict = f"未判（scale={scale}）残差 {errs}"
            elif all(abs(e[0]) <= H12.TOL_PX and abs(e[1]) <= H12.TOL_PX for e in errs):
                verdict = f"PASS 残差 {errs}"
            else:
                verdict = f"FAIL 残差 {errs}"
                rc = max(rc, 2)
        elif any(r.get("relaid") for r in readings):
            verdict = "气泡一上屏就整幅重排（平铺让位）⇒ 落点这一说不成立"
            rc = max(rc, 2)
        if not arm_wizard(app, Path(tdir), eyeball=eyeball):
            rc = max(rc, 1)
    fact("判据", ("气泡每个请求位都落准" if verdict.startswith("PASS")
                 else f"气泡落点判读：{verdict}") + f"（±{H12.TOL_PX} px）")
    fact("VERDICT", f"H18-{app_name}: {verdict} ⇒ 退出码 {rc}")
    dump()
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--both", action="store_true", help="按两个后端各起一子进程")
    ap.add_argument("--arm", choices=("xcb", "wayland"), help="只跑一个后端")
    ap.add_argument("--reqs", default="",
                    help="覆盖气泡请求位，形如 620,380;200,200（各位互不重叠）")
    ap.add_argument("--eyeball", type=int, default=0,
                    help="每个量到的请求位停下来 N 秒问人眼（人在环；0＝不问）")
    args = ap.parse_args()

    reqs = BUBBLE_REQS
    if args.reqs:
        reqs = tuple(tuple(int(v) for v in part.split(","))
                     for part in args.reqs.split(";") if part.strip())

    if args.both:
        rc = 0
        for arm in ("xcb", "wayland"):
            env = dict(os.environ, QT_QPA_PLATFORM=arm)
            r = subprocess.run([sys.executable, str(HERE / Path(__file__).name),
                                "--arm", arm, "--reqs", args.reqs,
                                *(["--eyeball", str(args.eyeball)]
                                  if args.eyeball else [])], env=env)
            rc = max(rc, r.returncode)
        print(f"\n=== H18 总退出码 {rc}（0=两臂都量到且不红，1=量具断，2=判据红）===")
        print("    H4 问的是\"两臂等价吗\"：把上面两条 VERDICT-H18-* 并排读。"
              "两臂残差同为 0 才算等价；一臂 0、一臂非 0 ⇒ H4 证伪。")
        return rc
    return run_arm(args.arm or os.environ.get("QT_QPA_PLATFORM", "xcb"),
                   args.eyeball, reqs)


if __name__ == "__main__":
    sys.exit(main())
