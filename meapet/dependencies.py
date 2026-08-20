"""运行依赖的规范化 requirement 字符串（纯标准库常量）。"""

from __future__ import annotations

import os
from typing import Mapping


PYQT_REQUIREMENT = "PyQt5>=5.15.9"
PILLOW_REQUIREMENT = "Pillow>=10.0"
REQUESTS_REQUIREMENT = "requests>=2.28"
HTTPX_REQUIREMENT = "httpx>=0.27.0"
JIEBA_REQUIREMENT = "jieba>=0.42"
WEBSOCKETS_REQUIREMENT = "websockets>=13,<16"
CRYPTOGRAPHY_REQUIREMENT = "cryptography>=42"
MCP_REQUIREMENT = "mcp>=1.27,<2"
UVICORN_REQUIREMENT = "uvicorn>=0.30"
PYOPENGL_REQUIREMENT = "PyOpenGL>=3.1.0"
TRANSLATORS_REQUIREMENT = "translators>=6.0.4,<7"
VOICE_INPUT_INSTALL_REQUIREMENTS = (
    "sherpa-onnx",
    "pyaudio",
    "modelscope",
)

# The project primarily serves users in mainland China. Keep the default in one
# place and allow callers to select PyPI or another index through environment.
DEFAULT_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"
DEFAULT_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"


def resolve_pip_index_url(
    environ: Mapping[str, str] | None = None,
) -> str:
    """返回安装依赖使用的索引；项目专用变量优先于 pip 全局变量。"""
    env = os.environ if environ is None else environ
    for key in ("MEA_PET_PIP_INDEX_URL", "PIP_INDEX_URL"):
        value = str(env.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    return DEFAULT_PIP_INDEX_URL


def resolve_torch_index_url(
    environ: Mapping[str, str] | None = None,
) -> str:
    """返回 PyTorch wheel 索引，允许项目或系统环境覆盖。"""
    env = os.environ if environ is None else environ
    for key in ("MEA_PET_TORCH_INDEX_URL", "TORCH_INDEX_URL"):
        value = str(env.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    return DEFAULT_TORCH_INDEX_URL
