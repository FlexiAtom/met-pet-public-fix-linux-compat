"""Qt 平台插件闸门 —— 在 `QApplication` 之前把「必 abort」换成一句真话，能窄修的就修掉。

人工 2026-10-16 现报：`python pet.py` 起不来，终端只有 Qt 自己那两句
`qt.qpa.plugin: Could not load the Qt platform plugin "wayland"/"xcb" ... even though it was found.`
加一句 `已中止（核心已转储）`。`QApplication` 外面那圈 try/except 接不住——Qt 走的是
`qFatal → abort()`，不是 Python 异常。

本案的判据不是猜的，三条各自有实测（存 `~/.Athena/projects/meapet/reference/
qt-qpa-plugin-mismatch/01…12*.txt`）：

1. **混用的形状**：`libQt5Core`/`libQt5Gui` 从打包目录加载（pyenv 那份 PyQt5，RPATH
   `$ORIGIN/Qt5/lib`），而 `QT_PLUGIN_PATH` 把平台插件的搜索抢到了系统那一份；系统插件要
   `QPlatformVulkanInstance` 那组 `Qt_5_PRIVATE_API` 符号，打包那一族里零个导出者。
2. **可以当场证明，不必等 abort**：`ctypes.CDLL`（默认 `RTLD_NOW`）与 `QPluginLoader`
   （带 `ResolveAllSymbolsHint`）同一语义，实测逐字复现 Qt 那句 `undefined symbol: …`；
   前提是**先 import 产品真正会 import 的那几个模块**（只 import `QtCore` 会假阴性——本案
   自己踩过，读数 07 与 08 的差别就是它）。
3. **修法只走窄的那一条**：改指 `QT_QPA_PLATFORM_PLUGIN_PATH`（只管平台插件），
   **保留** `QT_PLUGIN_PATH`——后者那目录里挂着 fcitx5 输入法插件，实测三个 IM 插件在打包
   core 下都能加载，撤掉等于把「输入框里能打中文」这件真东西拿走。窄修法两档实测
   `OK platform=xcb` / `OK platform=wayland`。

三条判据少任何一条都会退化成「看见两套 Qt 就动手」的猜测，而那会去改一个**本来能跑**的口子。
所以这里只在「不是同一套」**且**「那一套一个平台插件都加载不出来」**且**「内置那一套确实
加载得出来」三条同时成立时才动，且只动本进程的环境。

可关：`MEA_PET_QPA_NO_REPAIR=1` ⇒ 只说实话，一律不改。**这一档必须排在验内置那一套之前**：
实测「把内置那一只 `.so` 用 ctypes 映射进来」这一步单独就足以让后面的 `QApplication` 起来
（什么都不改也 `OK platform=xcb`，第二次去加载系统那只反而 `LOAD-OK`；存 `10-causal-arms.txt`
与 `11-mapped-family.txt`）。所以验它 = 已经在动手，标了「只说不改」再去验它就是假话。
代价是这一档不再分辨「内置那一套本来也起不来」——那一格要改指才验得到。

失效边界：全部读数取于本机 Arch + niri + 这两个具体前缀。Windows / PyInstaller 打包态没有
这套 `QT_PLUGIN_PATH`，按「同一套就不动」应当整段静默，但**那是没量过的**，故本模块非
Linux 直接返回，不做任何跨平台推定。
"""
from __future__ import annotations

import ctypes
import os
import sys
from typing import Callable, MutableMapping, Optional, Sequence

ENV_NO_REPAIR = "MEA_PET_QPA_NO_REPAIR"
ENV_QPA_PLATFORM_PLUGIN_PATH = "QT_QPA_PLATFORM_PLUGIN_PATH"
ENV_PLUGIN_PATH = "QT_PLUGIN_PATH"
ENV_QPA_PLATFORM = "QT_QPA_PLATFORM"
ENV_WAYLAND_DISPLAY = "WAYLAND_DISPLAY"

#: 与 Qt 一致：平台插件就在 `<plugins>/platforms` 这一层（内置那一层由 QLibraryInfo 给出）。
PLATFORMS_SUBDIR = "platforms"

#: 两条永久出口都是实测过的形状（`reference/qt-qpa-plugin-mismatch/05-matrix.txt`）。
_EXITS = "永久出口二选一：用项目自己的 `.venv/bin/python` 起，或 `env -u QT_PLUGIN_PATH python pet.py`"


def search_candidates(
    env: MutableMapping[str, str], builtin_plugins: str
) -> list[tuple[str, str]]:
    """按 Qt 自己的搜索顺序列出候选 `platforms` 目录，附来源标签。

    顺序是 Qt 的，不是我们挑的：`QT_QPA_PLATFORM_PLUGIN_PATH`（单目录，直接就是 platforms）
    → `QT_PLUGIN_PATH`（冒号分隔的列表，每项下面找 `platforms/`）→ 内置。
    """
    out: list[tuple[str, str]] = []
    single = (env.get(ENV_QPA_PLATFORM_PLUGIN_PATH) or "").strip()
    if single and os.path.isdir(single):
        out.append((os.path.realpath(single), ENV_QPA_PLATFORM_PLUGIN_PATH))
    for raw in (env.get(ENV_PLUGIN_PATH) or "").split(os.pathsep):
        entry = raw.strip()
        if not entry:
            continue
        # `<条目>/platforms` 优先——`QT_PLUGIN_PATH` 的一项指的是插件根，不是 platforms。
        # 只有它本身确实是 platforms 目录（有人直接指到那一层）才退到第二读法。
        # 顺序颠倒过一回，判据就把"插件根"当成 platforms 去数文件，数出零个候选，
        # 于是必死那一格被读成"没有插件可验"（本条测试钉住它）。
        nested = os.path.join(entry, PLATFORMS_SUBDIR)
        if os.path.isdir(nested):
            out.append((os.path.realpath(nested), ENV_PLUGIN_PATH))
        elif os.path.isdir(entry):
            out.append((os.path.realpath(entry), ENV_PLUGIN_PATH))
    if builtin_plugins:
        nested = os.path.join(builtin_plugins, PLATFORMS_SUBDIR)
        out.append((os.path.realpath(nested if os.path.isdir(nested) else builtin_plugins),
                    "Qt 内置"))
    return out


def _platform_plugin_files(directory: str) -> list[str]:
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []
    return [
        os.path.join(directory, n)
        for n in names
        if n.startswith("libq") and n.endswith(".so")
    ]


def preferred_plugin_names(env: MutableMapping[str, str]) -> list[str]:
    """Qt 大概率真会去用的那几只插件的文件名，按优先级；用来从一堆加载失败里挑**该说的那句**。

    取的是 Qt 自己的选路口径：`QT_QPA_PLATFORM` 显式设了就按它（它允许 `wayland,xcb` 这种
    回退表，也允许 `xcb:--foo` 这种带参写法，所以按逗号拆、每段再砍掉冒号后的参数），没设就
    在 Wayland 会话里先试 wayland，最后落 xcb。键与文件名不是一回事：本机两套目录里
    `wayland` 那只的文件名实测是 `libqwayland-generic.so`，所以两种写法都排进去。
    """
    keys: list[str] = []
    explicit = (env.get(ENV_QPA_PLATFORM) or "").strip()
    for part in explicit.split(","):
        token = part.split(":", 1)[0].strip()
        if token:
            keys.append(token)
    if not explicit and (env.get(ENV_WAYLAND_DISPLAY) or "").strip():
        keys.append("wayland")
    keys.append("xcb")
    out: list[str] = []
    for key in keys:
        for name in (f"libq{key}.so", f"libq{key}-generic.so"):
            if name not in out:
                out.append(name)
    return out


def decide(
    candidates: Sequence[tuple[str, str]],
    probe: Callable[[str], tuple[bool, str]],
    no_repair: bool = False,
    prefer: Sequence[str] = (),
) -> dict:
    """纯判据：给定候选目录与一个「这个 `.so` 加载得起来吗、加载不起来时那句话是什么」的
    探针，得出要不要改指。判据三条同时成立才动，少一条就只是说话。

    返回 `{"kind": ..., "dir": ..., "source": ..., "reasons": [...], "first_error": str,
    "first_error_plugin": str}`；`kind` 为 `matched` / `noop` / `repaired` / `warned`。
    """
    builtin = next((d for d, src in candidates if src == "Qt 内置"), None)
    winner = candidates[0] if candidates else None
    if winner is None or builtin is None:
        return {"kind": "noop", "dir": None, "source": None, "reasons": ["没有可比的目录"],
                "first_error": "", "first_error_plugin": ""}
    if winner[0] == builtin:
        # 同一套 ⇒ 一个字都不说，一个字节都不改。能跑的口子今天能跑，明天也一样。
        return {"kind": "matched", "dir": winner[0], "source": winner[1], "reasons": [],
                "first_error": "", "first_error_plugin": ""}

    winning_files = _platform_plugin_files(winner[0])
    if not winning_files:
        return {
            "kind": "noop",
            "dir": winner[0],
            "source": winner[1],
            "reasons": ["那一套里没有平台插件可验"],
            "first_error": "",
            "first_error_plugin": "",
        }
    # 「全部加载失败」才动手：只有一部分失败说明这一套里还有 Qt 用得上的东西，
    # 那不是本案那一格，改了反而会把能跑的口子带偏。
    verdicts = [probe(p) for p in winning_files]
    if any(ok for ok, _ in verdicts):
        return {
            "kind": "noop",
            "dir": winner[0],
            "source": winner[1],
            "reasons": ["那一套里至少有一个平台插件加载得起来，不是混用那一格"],
            "first_error": "",
            "first_error_plugin": "",
        }
    # 那句要挑 Qt 真会去用的那一只的错，不是目录里排最前的：今天排最前的是 eglfs，
    # 而人终端里那两句说的是 wayland 与 xcb——报错了插件，人照那句去补系统包会补错那一族。
    by_name = {os.path.basename(p): err for p, (_, err) in zip(winning_files, verdicts)}
    chosen = next((n for n in prefer if by_name.get(n)), "") or next(
        (n for n, err in by_name.items() if err), "")
    first_error = by_name.get(chosen, "")

    if no_repair:
        # 这里**不验内置那一套**。验它就是把那些 `.so` 映射进本进程，而实测那一步本身就足以
        # 让后面的 `QApplication` 起来（探针单独跑、一个环境变量都不改：`OK platform=xcb`）。
        # 标了「只说不改」还顺手把进程救活，那句话就成了假话——存 10/11 两份读数。
        return {
            "kind": "warned",
            "dir": winner[0],
            "source": winner[1],
            "reasons": [f"{ENV_NO_REPAIR}=1 ⇒ 只说不改（内置那一套一次都没验：验它等于先把它"
                        "加载进本进程，而那本身就足以让程序起来，改了就不是「不改」了）"],
            "first_error": first_error,
            "first_error_plugin": chosen,
        }

    target = builtin  # `search_candidates` 已经把内置那一项归一到 platforms 层
    target_files = _platform_plugin_files(target)
    target_verdicts = [probe(p) for p in target_files]
    if not target_files or not any(ok for ok, _ in target_verdicts):
        return {
            "kind": "warned",
            "dir": winner[0],
            "source": winner[1],
            "reasons": ["内置那一套也验不出可加载的平台插件 ⇒ 不指过去"],
            "first_error": first_error,
            "first_error_plugin": chosen,
        }
    return {"kind": "repaired", "dir": target, "source": winner[1], "reasons": [],
            "first_error": first_error, "first_error_plugin": chosen}


def _loaded_qt_core() -> Optional[str]:
    if sys.platform != "linux":
        return None
    try:
        with open("/proc/self/maps", "r", encoding="utf-8", errors="replace") as fp:
            for line in fp:
                fields = line.split()
                if fields and fields[-1].endswith("libQt5Core.so.5"):
                    return fields[-1]
    except OSError:
        return None
    return None


def _builtin_plugins_path() -> Optional[str]:
    try:
        from PyQt5.QtCore import QLibraryInfo
    except Exception:
        return None
    try:
        return QLibraryInfo.location(QLibraryInfo.PluginsPath)
    except Exception:
        return None


def _ctypes_probe(path: str) -> tuple[bool, str]:
    """`RTLD_NOW` 语义与 `QPluginLoader` 一致（它带 `ResolveAllSymbolsHint`），
    所以这里拿到的那句 `undefined symbol: …` 就是 Qt 自己想说的那句。"""
    try:
        ctypes.CDLL(path)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def ensure_platform_plugins_loadable(
    report: Callable[[str], None],
    env: Optional[MutableMapping[str, str]] = None,
    probe: Callable[[str], tuple[bool, str]] = _ctypes_probe,
    builtin_plugins: Optional[str] = None,
) -> dict:
    """闸门入口：命中就改指**这一次调用所在进程**的 `QT_QPA_PLATFORM_PLUGIN_PATH`，
    并把话说完。

    必须在 `QApplication(...)` **之前**调用——那道 `qFatal` 之后没有任何出口能再说话。
    """
    if sys.platform != "linux":
        return {"kind": "skip_platform", "dir": None, "source": None, "reasons": [],
                "first_error": ""}

    real_env = os.environ if env is None else env
    builtin = _builtin_plugins_path() if builtin_plugins is None else builtin_plugins
    if not builtin:
        return {"kind": "noop", "dir": None, "source": None, "reasons": ["取不到 Qt 内置插件目录"],
                "first_error": ""}

    candidates = search_candidates(real_env, builtin)
    core = _loaded_qt_core()
    outcome = decide(
        candidates,
        probe,
        no_repair=(real_env.get(ENV_NO_REPAIR) or "").strip() == "1",
        prefer=preferred_plugin_names(real_env),
    )
    outcome["core"] = core
    outcome["builtin"] = next(
        (d for d, src in candidates if src == "Qt 内置"), os.path.realpath(builtin)
    )
    outcome["winning"] = candidates[0][0] if candidates else None

    if outcome["kind"] == "matched":
        return outcome
    if outcome["kind"] == "noop":
        if outcome["reasons"]:
            report(
                f"[qpa] 平台插件目录与已加载的 Qt 不是同一套（{outcome['winning']} vs "
                f"{outcome['builtin']}），但这一格不是必死那一格：" + outcome["reasons"][0]
            )
        return outcome

    report(
        f"[qpa] 本进程里有两套 Qt：libQt5Core 来自 {core}，"
        f"而 Qt 会用的平台插件目录是 {outcome['winning']}（来自 {outcome['source']}）"
        f"——与已加载那一套同一路的目录是 {outcome['builtin']}"
    )
    if outcome["first_error"]:
        plugin = outcome.get("first_error_plugin") or ""
        report(
            f"[qpa] 那一套里的平台插件全部加载失败，"
            + (f"按 Qt 选路先试的那只 {plugin} 逐字：" if plugin else "第一个原因逐字：")
            + outcome["first_error"]
        )
        report(
            "[qpa] 这一发在 `QApplication` 里会变成 qFatal + abort（核心已转储），"
            "catch 不到，所以话先在这里说完"
        )
    if outcome["kind"] == "warned":
        for line in outcome["reasons"]:
            report(f"[qpa] {line}")
        report(f"[qpa] {_EXITS}")
        return outcome

    real_env[ENV_QPA_PLATFORM_PLUGIN_PATH] = outcome["dir"]
    outcome["applied"] = outcome["dir"]
    report(
        f"[qpa] 已把**本进程**的 {ENV_QPA_PLATFORM_PLUGIN_PATH} 指到 {outcome['dir']}"
        "（只改这一个进程，没动你的环境；`QT_PLUGIN_PATH` 原样留着，输入法那一档不受影响；"
        f"要这一档关掉就设 {ENV_NO_REPAIR}=1）"
    )
    report(f"[qpa] {_EXITS}")
    return outcome
