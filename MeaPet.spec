# -*- mode: python ; coding: utf-8 -*-
"""MeaPet onedir PyInstaller spec (portable data under _internal)."""

import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# 平台分档只用这一个判据，两处（桥接层 / fidus）共用：写三遍 `startswith` 就是三处
# 可以互相不一致的地方。
IS_LINUX = sys.platform.startswith("linux")

# live2d-py package data (native resources); project tree live2d/ is listed in datas.
try:
    live2d_pkg_datas = collect_data_files("live2d")
except Exception:
    live2d_pkg_datas = []

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
        raise SystemExit(
            "本环境没有 fidus ⇒ 这个包不会有「切换点定位」这条能力"
            "（Linux 打包前先把那只 wheel 装上）"
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
        "meapet.desktop.status_panel",
        "meapet.desktop.timeline_viewer",
        "meapet.desktop.chat_input",
        "meapet.desktop.dialogs",
        # agent adapters
        "meapet.agent.agent_link",
        "meapet.agent.openclaw",
        "meapet.agent.hermes",
        # in-process VITS
        "meapet.tts.engines.vits_runtime",
        "scipy",
        "scipy.io",
        "scipy.io.wavfile",
        # misc dynamic imports
        "translators",
        "jieba",
    ],
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
