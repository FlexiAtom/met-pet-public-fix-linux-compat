"""硬编码审计的回归测试。

这些断言只约束会造成行为漂移、机器绑定或虚假配置能力的值；角色台词、协议
字段名和公开服务商清单等领域常量不在本文件的清理范围内。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _example_config() -> dict:
    return json.loads(
        (ROOT / "config.example.json").read_text(encoding="utf-8")
    )


def test_public_defaults_are_canonical_and_match_the_example_config():
    from meapet.config.defaults import (
        DEFAULT_DIRECT_MODEL,
        DEFAULT_HERMES_WS_URL,
        DEFAULT_LIVE2D_WINDOW_MASK,
        DEFAULT_MIMO_API_BASE,
        DEFAULT_MIMO_TTS_MODEL,
        DEFAULT_OLLAMA_HOST,
        DEFAULT_OPENAI_API_BASE,
        DEFAULT_WATCHER_INTERVAL,
    )
    from meapet.config.providers import preset_by_id
    from meapet.config.store import normalize_config

    example = _example_config()
    direct = example["llm"]["direct"]

    assert direct["api_base"] == DEFAULT_OPENAI_API_BASE
    assert direct["model"] == DEFAULT_DIRECT_MODEL
    assert example["llm"]["agent"]["base_url"] == DEFAULT_HERMES_WS_URL
    assert example["tts"]["api_base"] == DEFAULT_MIMO_API_BASE
    assert example["tts"]["model"] == DEFAULT_MIMO_TTS_MODEL
    assert example["watcher"]["interval"] == dict(DEFAULT_WATCHER_INTERVAL)
    assert example["live2d"]["window_mask"] == dict(
        DEFAULT_LIVE2D_WINDOW_MASK
    )

    assert preset_by_id("openai").api_base == DEFAULT_OPENAI_API_BASE
    assert preset_by_id("ollama").api_base == DEFAULT_OLLAMA_HOST
    assert preset_by_id("mimo").api_base == DEFAULT_MIMO_API_BASE
    assert normalize_config({})["live2d"]["window_mask"] == dict(
        DEFAULT_LIVE2D_WINDOW_MASK
    )


def test_watcher_timer_uses_the_same_default_interval_as_normalization():
    from meapet.config.defaults import DEFAULT_WATCHER_INTERVAL
    from meapet.desktop.watch_ctrl import PetWatcherMixin

    class _Timer:
        started_with = None

        def start(self, milliseconds):
            self.started_with = milliseconds

    host = type(
        "WatcherHost",
        (),
        {"config": {}, "_watcher_timer": _Timer()},
    )()
    expected_min = DEFAULT_WATCHER_INTERVAL["min_ms"]
    expected_max = DEFAULT_WATCHER_INTERVAL["max_ms"]

    with mock.patch(
        "meapet.desktop.watch_ctrl.random.randint",
        return_value=expected_min,
    ) as randint:
        PetWatcherMixin._start_watcher_timer(host)

    randint.assert_called_once_with(expected_min, expected_max)
    assert host._watcher_timer.started_with == expected_min


def test_example_config_does_not_advertise_an_ignored_character_name():
    character = _example_config()["character"]

    assert "name" not in character
    assert set(character) == {"default_outfit", "default_direction"}


def test_chat_engine_has_no_dead_http_bridge_parameter():
    from meapet.chat.engine import ChatEngine

    assert "bridge_url" not in inspect.signature(ChatEngine).parameters
    engine = ChatEngine()
    assert not hasattr(engine, "bridge_url")


def test_gsv_runtime_discovery_has_no_developer_machine_or_dated_paths(
    tmp_path,
):
    from meapet.tts.service import MeaTTS, gsv_python_candidates

    source = inspect.getsource(MeaTTS.__init__)
    assert "20250604" not in source
    assert r"C:\Users" not in source

    conda_prefix = tmp_path / "conda-env"
    candidates = gsv_python_candidates(
        home=tmp_path / "home",
        environ={"CONDA_PREFIX": str(conda_prefix)},
        executable=tmp_path / "python",
        frozen=False,
    )

    assert str(conda_prefix / "python.exe") in candidates
    assert all("20250604" not in candidate for candidate in candidates)


def test_runtime_dependency_specs_have_one_python_source_of_truth():
    from meapet.bootstrap import all_runtime_dependencies
    from meapet.dependencies import (
        CRYPTOGRAPHY_REQUIREMENT,
        WEBSOCKETS_REQUIREMENT,
    )
    from wizard.agent_setup_help import _AGENT_DEPENDENCY_SPECS

    runtime_requirements = {
        dependency.module: dependency.requirement
        for dependency in all_runtime_dependencies()
    }
    assert runtime_requirements["websockets"] == WEBSOCKETS_REQUIREMENT
    assert runtime_requirements["cryptography"] == CRYPTOGRAPHY_REQUIREMENT
    assert _AGENT_DEPENDENCY_SPECS["hermes"][0][2] == WEBSOCKETS_REQUIREMENT
    assert (
        _AGENT_DEPENDENCY_SPECS["openclaw"][1][2]
        == CRYPTOGRAPHY_REQUIREMENT
    )


def test_llm_page_does_not_embed_a_second_legacy_palette():
    source = (ROOT / "wizard" / "page_llm.py").read_text(encoding="utf-8")

    assert "_INPUT_STYLE" not in source
    for legacy_color in (
        "#1B1D2E",
        "#E8EAF0",
        "#3A3D52",
        "#7C8AFF",
        "#9AA0B5",
        "#C8CCDC",
    ):
        assert legacy_color not in source


def test_default_endpoint_literals_stay_in_the_defaults_registry():
    consumers = (
        ROOT / "meapet" / "agent" / "factory.py",
        ROOT / "meapet" / "chat" / "engine.py",
        ROOT / "meapet" / "config" / "store.py",
        ROOT / "meapet" / "watcher" / "screen.py",
        ROOT / "wizard" / "page_backend.py",
        ROOT / "wizard" / "page_llm.py",
        ROOT / "wizard" / "page_tts.py",
        ROOT / "wizard" / "page_vision.py",
    )
    duplicated_literals = (
        "https://api.openai.com/v1",
        "https://api.xiaomimimo.com/v1",
        "http://127.0.0.1:11434",
        "ws://127.0.0.1:18789",
    )

    for path in consumers:
        source = path.read_text(encoding="utf-8")
        for literal in duplicated_literals:
            assert literal not in source, f"{path.relative_to(ROOT)}: {literal}"
