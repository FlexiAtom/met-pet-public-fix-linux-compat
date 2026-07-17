"""Project root and path helpers."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# meapet/ is inside project root
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent


def project_root() -> str:
    return str(PROJECT_ROOT)


def project_path(*parts: str) -> str:
    return str(PROJECT_ROOT.joinpath(*parts))


def get_data_dir() -> str:
    """Return a writable directory for runtime data (logs, cache, config saves).

    In source / development mode this is ``PROJECT_ROOT``.
    In PyInstaller frozen mode ``PROJECT_ROOT`` points to the read-only
    ``sys._MEIPASS`` temp directory, so we redirect to
    ``~/.meapet/`` instead.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        data_dir = Path.home() / ".meapet"
    else:
        data_dir = PROJECT_ROOT
    data_dir.mkdir(parents=True, exist_ok=True)
    return str(data_dir)
