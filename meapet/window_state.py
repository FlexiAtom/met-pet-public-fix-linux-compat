"""本机窗口状态的轻量持久化，不混入用户功能配置。"""

from __future__ import annotations

from pathlib import Path

from meapet.config.store import load_json, save_json


_COORDINATE_LIMIT = 1_000_000
_DIMENSION_LIMIT = 32_768


def state_path_for_config(config_path: str, filename: str) -> str:
    """让便携版、源码版和测试配置各自使用同目录的窗口状态文件。"""
    return str(Path(config_path).resolve().with_name(filename))


def _bounded_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < minimum or number > maximum:
        return None
    return number


def normalize_pet_position(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    x = _bounded_int(
        value.get("x"),
        minimum=-_COORDINATE_LIMIT,
        maximum=_COORDINATE_LIMIT,
    )
    y = _bounded_int(
        value.get("y"),
        minimum=-_COORDINATE_LIMIT,
        maximum=_COORDINATE_LIMIT,
    )
    if x is None or y is None:
        return None
    return {"x": x, "y": y}


def normalize_wizard_geometry(value: object) -> dict[str, int | bool] | None:
    if not isinstance(value, dict):
        return None
    position = normalize_pet_position(value)
    width = _bounded_int(
        value.get("width"),
        minimum=1,
        maximum=_DIMENSION_LIMIT,
    )
    height = _bounded_int(
        value.get("height"),
        minimum=1,
        maximum=_DIMENSION_LIMIT,
    )
    maximized = value.get("maximized", False)
    if (
        position is None
        or width is None
        or height is None
        or not isinstance(maximized, bool)
    ):
        return None
    return {
        **position,
        "width": width,
        "height": height,
        "maximized": maximized,
    }


def load_pet_position(path: str) -> dict[str, int] | None:
    return normalize_pet_position(load_json(path, {}))


def save_pet_position(path: str, x: object, y: object) -> bool:
    position = normalize_pet_position({"x": x, "y": y})
    if position is None:
        return False
    try:
        Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
        save_json(path, position)
    except OSError:
        return False
    return True


def load_wizard_geometry(path: str) -> dict[str, int | bool] | None:
    return normalize_wizard_geometry(load_json(path, {}))


def save_wizard_geometry(path: str, value: object) -> bool:
    geometry = normalize_wizard_geometry(value)
    if geometry is None:
        return False
    try:
        Path(path).resolve().parent.mkdir(parents=True, exist_ok=True)
        save_json(path, geometry)
    except OSError:
        return False
    return True


__all__ = [
    "load_pet_position",
    "load_wizard_geometry",
    "normalize_pet_position",
    "normalize_wizard_geometry",
    "save_pet_position",
    "save_wizard_geometry",
    "state_path_for_config",
]
