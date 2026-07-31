"""独立窗口状态文件的正规化与读写契约。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from meapet.window_state import (
    load_pet_position,
    load_wizard_geometry,
    normalize_pet_position,
    normalize_wizard_geometry,
    save_pet_position,
    save_wizard_geometry,
    state_path_for_config,
)


def test_state_path_lives_next_to_the_functional_config(tmp_path: Path) -> None:
    config_path = tmp_path / "portable" / "config.json"

    assert state_path_for_config(config_path, "pet_window_state.json") == str(
        config_path.parent.resolve() / "pet_window_state.json"
    )


def test_pet_position_round_trip_creates_parent_directory(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "nested" / "pet.json"

    assert save_pet_position(str(state_path), -120, 340)
    assert load_pet_position(str(state_path)) == {"x": -120, "y": 340}


def test_wizard_geometry_round_trip_preserves_normal_size_and_state(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "nested" / "wizard.json"
    expected = {
        "x": 40,
        "y": 32,
        "width": 1020,
        "height": 720,
        "maximized": True,
    }

    assert save_wizard_geometry(str(state_path), expected)
    assert load_wizard_geometry(str(state_path)) == expected


def test_damaged_or_out_of_range_window_state_is_ignored(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "window.json"
    state_path.write_text("{not-json", encoding="utf-8")
    assert load_pet_position(str(state_path)) is None
    assert load_wizard_geometry(str(state_path)) is None

    assert normalize_pet_position(None) is None
    assert normalize_pet_position({"x": True, "y": 1}) is None
    assert normalize_pet_position({"x": object(), "y": 1}) is None
    assert normalize_pet_position({"x": 1_000_001, "y": 1}) is None
    assert normalize_wizard_geometry(None) is None
    assert normalize_wizard_geometry(
        {
            "x": 1,
            "y": 2,
            "width": 0,
            "height": 720,
            "maximized": False,
        }
    ) is None
    assert normalize_wizard_geometry(
        {
            "x": 1,
            "y": 2,
            "width": 880,
            "height": 720,
            "maximized": "false",
        }
    ) is None


def test_invalid_state_is_not_written(tmp_path: Path) -> None:
    pet_path = tmp_path / "pet.json"
    wizard_path = tmp_path / "wizard.json"

    assert not save_pet_position(str(pet_path), True, 2)
    assert not save_wizard_geometry(str(wizard_path), {"x": 1})
    assert not pet_path.exists()
    assert not wizard_path.exists()


def test_state_save_failure_is_non_fatal(tmp_path: Path) -> None:
    geometry = {
        "x": 1,
        "y": 2,
        "width": 880,
        "height": 720,
        "maximized": False,
    }
    with patch("meapet.window_state.save_json", side_effect=OSError):
        assert not save_pet_position(str(tmp_path / "pet.json"), 1, 2)
        assert not save_wizard_geometry(
            str(tmp_path / "wizard.json"),
            geometry,
        )


def test_saved_state_contains_no_functional_configuration(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "wizard.json"
    assert save_wizard_geometry(
        str(state_path),
        {
            "x": 1,
            "y": 2,
            "width": 880,
            "height": 720,
            "maximized": False,
            "api_key": "must-not-be-copied",
        },
    )

    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(stored) == {"x", "y", "width", "height", "maximized"}
