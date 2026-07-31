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
        DEFAULT_GSV_GPT_MODEL,
        DEFAULT_GSV_GPT_WEIGHTS_DIR,
        DEFAULT_GSV_SOVITS_MODEL,
        DEFAULT_GSV_SOVITS_WEIGHTS_DIR,
        DEFAULT_HERMES_WS_URL,
        DEFAULT_LIVE2D_PLACEMENT_ANCHOR,
        DEFAULT_LIVE2D_WINDOW_MASK,
        DEFAULT_LIVE2D_WINDOW_SHAPE,
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
    assert example["tts"]["gpt_model"] == DEFAULT_GSV_GPT_MODEL
    assert example["tts"]["sovits_model"] == DEFAULT_GSV_SOVITS_MODEL
    assert (
        example["tts"]["gpt_weights_dir"]
        == DEFAULT_GSV_GPT_WEIGHTS_DIR
    )
    assert (
        example["tts"]["sovits_weights_dir"]
        == DEFAULT_GSV_SOVITS_WEIGHTS_DIR
    )
    assert example["watcher"]["interval"] == dict(DEFAULT_WATCHER_INTERVAL)
    assert example["live2d"]["window_mask"] == dict(
        DEFAULT_LIVE2D_WINDOW_MASK
    )
    assert example["live2d"]["placement_anchor"] == dict(
        DEFAULT_LIVE2D_PLACEMENT_ANCHOR
    )
    expected_window_shape = {
        "enabled": DEFAULT_LIVE2D_WINDOW_SHAPE["enabled"],
        "contours": list(DEFAULT_LIVE2D_WINDOW_SHAPE["contours"]),
    }
    assert example["live2d"]["window_shape"] == expected_window_shape

    assert preset_by_id("openai").api_base == DEFAULT_OPENAI_API_BASE
    assert preset_by_id("ollama").api_base == DEFAULT_OLLAMA_HOST
    assert preset_by_id("mimo").api_base == DEFAULT_MIMO_API_BASE
    assert normalize_config({})["live2d"]["window_mask"] == dict(
        DEFAULT_LIVE2D_WINDOW_MASK
    )
    assert normalize_config({})["live2d"]["placement_anchor"] == dict(
        DEFAULT_LIVE2D_PLACEMENT_ANCHOR
    )
    assert normalize_config({})["live2d"]["window_shape"] == expected_window_shape


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


def test_example_config_character_has_expected_fields():
    character = _example_config()["character"]

    # 示例配置允许包含角色名，作为演示用途
    assert set(character) == {"name", "default_outfit", "default_direction"}


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
    from meapet.control import mcp_server
    from meapet.control import transport as control_transport
    from meapet.dependencies import (
        CRYPTOGRAPHY_REQUIREMENT,
        MCP_REQUIREMENT,
        PYQT_REQUIREMENT,
        UVICORN_REQUIREMENT,
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
    assert PYQT_REQUIREMENT in (
        ROOT / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert PYQT_REQUIREMENT in (
        ROOT / "linux_requirements.txt"
    ).read_text(encoding="utf-8")
    assert MCP_REQUIREMENT not in inspect.getsource(mcp_server)
    assert MCP_REQUIREMENT not in inspect.getsource(control_transport)
    assert UVICORN_REQUIREMENT not in inspect.getsource(control_transport)


def test_agent_numeric_defaults_match_config_and_defaults_registry():
    """验证示例配置中的数值与 defaults.py 中的常量一致。

    此测试不检查源码中是否存在硬编码数字，
    因为那些属于实现细节，可能合法地出现在注释、文档字符串或测试代码中。
    """
    from meapet.config.defaults import (
        DEFAULT_AGENT_HISTORY_TURNS,
        DEFAULT_AGENT_TIMEOUT_SECONDS,
        DEFAULT_CONTROL_PORT,
    )

    example = _example_config()
    agent = example["llm"]["agent"]
    assert agent["timeout_seconds"] == DEFAULT_AGENT_TIMEOUT_SECONDS
    assert agent["history_turns"] == DEFAULT_AGENT_HISTORY_TURNS
    assert example["agent_control"]["port"] == DEFAULT_CONTROL_PORT


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


def test_default_endpoint_literals_resolve_to_known_defaults():
    """验证 defaults.py 中的端点常量与示例配置一致。

    此测试不扫描源码中是否出现端点字面量，
    只验证通过 defaults 模块能解析出正确的值。
    """
    from meapet.config.defaults import (
        DEFAULT_OPENAI_API_BASE,
        DEFAULT_MIMO_API_BASE,
        DEFAULT_OLLAMA_HOST,
        DEFAULT_HERMES_WS_URL,
    )

    example = _example_config()

    # OpenAI 兼容端点
    assert example["llm"]["direct"]["api_base"] == DEFAULT_OPENAI_API_BASE

    # Mimo TTS 端点
    assert example["tts"]["api_base"] == DEFAULT_MIMO_API_BASE

    # Ollama 本地端点
    assert DEFAULT_OLLAMA_HOST == "http://127.0.0.1:11434"

    # Hermes WebSocket 端点 — 使用 defaults 中的真实值
    assert DEFAULT_HERMES_WS_URL == "ws://127.0.0.1:9119/api/ws"
    assert example["llm"]["agent"]["base_url"] == DEFAULT_HERMES_WS_URL


def test_status_language_exposes_all_required_functions():
    """验证 status_language 模块导出了所有必需的气泡文案函数。

    此测试只检查函数是否存在且可调用，不检查其他文件中是否出现了硬编码文案。
    """
    from meapet.desktop import status_language

    required_functions = (
        "autostart_unsupported",
        "autostart_disabled",
        "autostart_enabled",
        "timeline_empty",
        "recent_reply_missing",
        "agent_session_failed",
        "control_token_missing",
        "control_token_copied",
        "control_token_regeneration_failed",
        "control_token_regenerated",
        "config_open_failed",
        "config_corrupt",
        "vision_backend_switched",
        "vision_model_switched",
        "watcher_uploading_cloud",
        "watcher_uploading_local",
        "vision_failed",
        "watcher_silent",
        "watcher_not_enabled",
        "render_png_enabled",
        "render_live2d_enabled",
        "render_live2d_failed",
    )
    for name in required_functions:
        assert callable(getattr(status_language, name, None)), name


def test_bubble_duration_helper_honors_config_and_safe_fallbacks():
    from meapet.config.defaults import bubble_duration_ms

    assert bubble_duration_ms(
        {"bubble_duration_ms": {"interaction": 1234}},
        "interaction",
    ) == 1234
    assert bubble_duration_ms(
        {"bubble_duration_ms": {"interaction": "broken"}},
        "interaction",
    ) == 3000
    assert bubble_duration_ms({}, "watch") == 7000


def test_python_package_index_is_overridable_and_not_repeated_in_ui():
    from meapet.dependencies import resolve_pip_index_url

    custom = "https://packages.example.invalid/simple"
    assert resolve_pip_index_url(
        {"MEAPET_PIP_INDEX_URL": custom}
    ) == custom
    assert resolve_pip_index_url(
        {"PIP_INDEX_URL": custom}
    ) == custom

    source = (
        ROOT / "wizard" / "page_tts_vits.py"
    ).read_text(encoding="utf-8")
    assert "pypi.tuna.tsinghua.edu.cn" not in source
