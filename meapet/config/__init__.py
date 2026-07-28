"""配置包的轻量公开入口。

保持包初始化无副作用，避免 ``utils -> config.defaults`` 又反向加载完整 store。
公开函数在真正访问时才导入。
"""

from __future__ import annotations

from importlib import import_module


__all__ = ["load_config", "normalize_config", "save_config"]


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(".store", __name__), name)
    globals()[name] = value
    return value
