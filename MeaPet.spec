# -*- mode: python ; coding: utf-8 -*-
"""MeaPet onedir PyInstaller spec (portable data under _internal)."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

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
elif sys.platform.startswith("linux"):
    raise SystemExit(
        f"缺 {LAYER_SHIM_NAME}：Linux 打包前先跑 `bash build_layer_shell.sh`"
        f"（产物应落在 {SPECPATH}）。带着缺件打出来的包没有穿透模式。"
    )
else:
    layer_binaries = []

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
    ],
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
