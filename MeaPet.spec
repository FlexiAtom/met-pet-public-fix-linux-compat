# -*- mode: python ; coding: utf-8 -*-
"""MeaPet onedir PyInstaller spec (portable data under _internal)."""

import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# 平台分档只用这一个判据，两处（桥接层 / fidus）共用：写三遍 `startswith` 就是三处
# 可以互相不一致的地方。
IS_LINUX = sys.platform.startswith("linux")

# live2d-py package data (native resources); project tree live2d/ is listed in datas.
try:
    live2d_pkg_datas = collect_data_files("live2d")
except Exception:
    live2d_pkg_datas = []

# PyOpenGL 的 platform 子模块：**选了哪条路取决于跑它的那个会话，不取决于构建平台**，
# 所以整族都得带上，不能只带"构建机那一档"。
# 实测缺口（2026-10-19 本机第一份 Linux onedir，#128 六项读数第 2 条当场红）：
#   contrib 的 hook-OpenGL.py 在 Linux 分支只补 `OpenGL.platform.glx`；而
#   OpenGL/platform/__init__.py:35-37 在 `WAYLAND_DISPLAY` 在场时把 guess 定成
#   "wayland"，PlatformPlugin 选中的是 **egl**。于是包里有 glx、没有 egl ⇒
#   plugin.load() 抛 ModuleNotFoundError ⇒ _load() 失败后 PLATFORM 仍是 None ⇒
#   产品侧 live2d_widget.py:250 拿到 `None()`，报的是看不懂的
#   "TypeError: 'NoneType' object is not callable"，然后静默回退 PNG。
#   源码态同一条 import 是好的（实测 `OpenGL.platform.PLATFORM` = EGLPlatform 实例），
#   所以这条**只在冻结版暴露**——和上面桥接层那格是同一类盲区。
# 用 collect_submodules 而不是手写 egl+glx 两个名字：将来 PyOpenGL 多加一档
# （osmesa 之外再来个新的）会被自动收进来，不需要有人记得回来改这里。
# try/except：构建机没装 PyOpenGL 时返回空，别把构建拦在一条本不该是门票的缺件上
# （没装 PyOpenGL 的构建机本来就打不出 Live2D 那条路）。
try:
    pyopengl_platform_imports = collect_submodules("OpenGL.platform")
except Exception:
    pyopengl_platform_imports = []

# layer-shell 桥接（Linux / Niri 点击穿透）。它是 ctypes 直读的裸 .so，不是 Python
# 扩展模块，也没有任何 Python 侧 import 指向它——依赖分析**看不见**它。不写这几行，
# 冻结版就没有 Wayland 那条路，而且这条缺失只在真机上暴露，离线测试一律绿。
# 落点 "." = _internal，与 meapet/desktop/wayland_layer.py::shim_candidates()
# 的第一条候选（打包目录）对齐；文件名一致性由 tests/test_layer_shim_packaging.py 钉住。
LAYER_SHIM_NAME = "liblayer_shell_shim.so"
_layer_shim = Path(SPECPATH) / LAYER_SHIM_NAME
if _layer_shim.is_file():
    layer_binaries = [(str(_layer_shim), ".")]
elif IS_LINUX:
    raise SystemExit(
        f"缺 {LAYER_SHIM_NAME}：Linux 打包前先跑 `bash build_layer_shell.sh`"
        f"（产物应落在 {SPECPATH}）。带着缺件打出来的包没有穿透模式。"
    )
else:
    layer_binaries = []

# fidus（切换点定位）随包分发的**平台分档**（2026-09-28 人工 standing 裁决）：
#   Linux   ⇒ 必须在场。wheel 走发行渠道；没装上＝打包缺陷 ⇒ 拦构建，别让缺件出厂。
#   非 Linux ⇒ **显式排除**，直到 fidus 宣布支持该平台。
# "排除"必须写出来，不能靠"这台构建机恰好没装"：眼下 Windows 侧确实是空场，那份"不带"
# 是构建机环境的**副产品**——谁哪天装了个来源不明的 fidus，它就被静默收进 Windows 产物，
# 而那条路在非 Linux 上没有已知的可用引擎。写成 excludes 之后，"不带"是一个可被测试钉住、
# 也可被将来一句话撤销的**决定**。
# 运行面不受影响：`fidus_position.have_engine()` 只在菜单「定位与穿透」那一刻查 ⇒
# 排除掉的效果是那个开关响亮拒绝，不影响开工。
fidus_excludes = [] if IS_LINUX else ["fidus"]
if IS_LINUX:
    if importlib.util.find_spec("fidus") is None:
        from meapet.bootstrap import fidus_install_hint

        raise SystemExit(
            "本环境没有 fidus ⇒ 这个包不会有「切换点定位」这条能力\n"
            "（Linux 打包前先把它装上）：\n" + fidus_install_hint()
        )
else:
    print(
        "[MeaPet.spec] fidus 按裁决不进非 Linux 包（显式 excludes，撤销见本文件注释）",
        file=sys.stderr,
    )

a = Analysis(
    ["pet.py"],
    pathex=[],
    binaries=layer_binaries,
    datas=[
        ("live2d", "live2d"),
        ("sprites", "sprites"),
        ("models/GPT_weights", "models/GPT_weights"),
        ("models/SoVITS_weights", "models/SoVITS_weights"),
        ("GPT-Sovits", "GPT-Sovits"),
        ("config.example.json", "."),
        ("vits_models", "vits_models"),
        ("vits_core", "vits_core"),
        ("vits_requirements.txt", "."),
        ("dic", "dic"),
        ("meapet/assets", "meapet/assets"),
        ("meapet/tools", "meapet/tools"),
    ]
    + live2d_pkg_datas,
    hiddenimports=[
        # certifi CA bundle (belt-and-suspenders; shared client uses OS trust store)
        "certifi",
        "pkg_resources",
        # Agent WebSocket 传输：ws_transport 顶层导入 websockets.exceptions，
        # 连接时才 import websockets.asyncio.client。漏打这些会让依赖门禁
        # 判定 Agent 模式不可用，冻结版曾因此直接静默退出。
        "websockets",
        "websockets.exceptions",
        "websockets.asyncio.client",
        # Companion MCP 服务（agent_control.enabled 时按需导入）
        "mcp",
        "mcp.server",
        "mcp.server.session",
        # wizard package (dynamically imported from _reopen_setup_wizard)
        "wizard.app",
        "wizard.pages",
        "wizard.styles",
        "wizard.platform_info",
        "wizard.connection_test",
        "wizard.env_utils",
        "wizard.widgets",
        "wizard.page_env",
        "wizard.page_llm",
        "wizard.page_backend",
        "wizard.page_tts",
        "wizard.page_tts_vits",
        "wizard.page_tts_gsv",
        "wizard.page_tts_mimo",
        "wizard.page_vision",
        # desktop modules loaded dynamically
        "meapet.desktop.live2d_widget",
        # live2d-py 只认 1.0 这一代（人工 2026-10-17 裁决：不背两代兼容债，安装面钉 >=1.0.0）。
        # 这一格列 `live2d` 是因为桌宠**不静态 import 它**——`meapet/live2d_api.py` 在 try 里
        # import，PyInstaller 的静态图对"闸门 + 动态加载"这条组合不放心，显式列一次最省。
        # 旧名 `live2d.v3` 已从这里删掉：v1.0.0 发布正文写着「Removed the live2d.v3 and
        # live2d.v2cpp modules」，留着它只会在每次构建多一条 warning，并给下一个人留下
        # "这代还活着"的假印象。
        # `live2d.v2`（1.0.0 wheel 里 96 个 .py 有 88 个在它下面）**不列**，实测没有任何 import
        # 边：wheel 内无一处 `import live2d.v2`，`live2d/__init__.py` 唯一的 import 是
        # `from ._live2d import *`，而 `_live2d.so` 里 `PyImport_Import*`/`import_module`
        # 两种字符串各 0 次（原生码不回头 import Python 模块）。原生 `init()` 只是把包目录
        # 交给 `init_internal` 去读着色器资源，那是上面 `collect_data_files("live2d")` 的活。
        "meapet.live2d_api",
        "live2d",
        "meapet.desktop.status_panel",
        "meapet.desktop.timeline_viewer",
        "meapet.desktop.chat_input",
        "meapet.desktop.dialogs",
        # agent adapters
        "meapet.agent.agent_link",
        "meapet.agent.openclaw",
        "meapet.agent.hermes",
        # doctor 那一格 `--agent-script` 用的假 Agent 端。它**不在** `tests/`（PyInstaller 不收
        # 那目录），而是在产品侧 `meapet/agent/`，所以冻结包里也得带上；它走的是 websockets 的
        # **服务器**侧，上面那三行只带了 client——漏了这一行，冻结版跑到那一格是
        # ModuleNotFoundError，不是读数。
        "meapet.agent.fake_agent",
        "websockets.asyncio.server",
        # in-process VITS
        "meapet.tts.engines.vits_runtime",
        "scipy",
        "scipy.io",
        "scipy.io.wavfile",
        # misc dynamic imports
        "translators",
        "jieba",
    ]
    + pyopengl_platform_imports,
    hookspath=["."],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "test",
        "unittest",
        "pydoc",
        "doctest",
    ] + fidus_excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MeaPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MeaPet",
)
