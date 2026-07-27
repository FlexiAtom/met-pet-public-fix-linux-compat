"""启动依赖检查的回归测试。

这些测试覆盖一个容易被配置中心掩盖的问题：如果可选运行模式的依赖在
GUI 导入期间就被加载，那么用户根本没有机会打开配置中心修复配置。
"""

from __future__ import annotations

import subprocess
import sys
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _dependency_modules(dependencies: object) -> set[str]:
    return {dependency.module for dependency in dependencies}


def test_direct_mode_does_not_require_agent_or_control_dependencies():
    from meapet.bootstrap import required_runtime_dependencies

    dependencies = required_runtime_dependencies(
        {
            "llm": {"mode": "direct"},
            # 控制服务只会在 Agent 模式启动；切回直连后，即使旧配置仍保留
            # enabled=true，也不能让 MCP 依赖阻断桌宠。
            "agent_control": {"enabled": True},
        }
    )

    modules = _dependency_modules(dependencies)
    assert "websockets" not in modules
    assert "cryptography" not in modules
    assert "mcp" not in modules
    assert "uvicorn" not in modules


def test_agent_and_control_modes_add_their_runtime_dependencies():
    from meapet.bootstrap import required_runtime_dependencies

    dependencies = required_runtime_dependencies(
        {
            "llm": {
                "mode": "agent",
                "agent": {"kind": "openclaw"},
            },
            "agent_control": {"enabled": True},
        }
    )

    modules = _dependency_modules(dependencies)
    assert {"websockets", "cryptography", "mcp", "uvicorn"} <= modules


def test_hermes_does_not_require_openclaw_cryptography():
    from meapet.bootstrap import required_runtime_dependencies

    dependencies = required_runtime_dependencies(
        {
            "llm": {
                "mode": "agent",
                "agent": {"kind": "hermes"},
            },
            "agent_control": {"enabled": False},
        }
    )

    modules = _dependency_modules(dependencies)
    assert "websockets" in modules
    assert "cryptography" not in modules


def test_legacy_agent_config_is_detected_before_normalization():
    from meapet.bootstrap import required_runtime_dependencies

    dependencies = required_runtime_dependencies(
        {
            "llm": {
                "backend": "openclaw",
                "agent": {"kind": "openclaw"},
            }
        }
    )

    modules = _dependency_modules(dependencies)
    assert "websockets" in modules
    assert "cryptography" in modules


def test_missing_dependency_message_is_available_before_gui_import(tmp_path):
    from meapet.bootstrap import (
        RuntimeDependency,
        format_missing_dependencies,
    )

    message = format_missing_dependencies(
        [
            RuntimeDependency(
                module="websockets",
                requirement="websockets>=13,<16",
                purpose="Hermes/OpenClaw Agent",
            )
        ],
        project_root=tmp_path,
        executable=Path(sys.executable),
    )

    assert "websockets>=13,<16" in message
    assert str(Path(sys.executable)) in message
    assert "linux_requirements.txt" in message
    assert "Traceback" not in message


def test_agent_preflight_reports_missing_websockets_without_importing_gui(
    tmp_path,
):
    from meapet.bootstrap import ensure_pet_dependencies

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"llm":{"mode":"agent"},"agent_control":{"enabled":false}}',
        encoding="utf-8",
    )
    output = StringIO()

    def fake_find_spec(module):
        return None if module == "websockets" else object()

    ready = ensure_pet_dependencies(
        tmp_path,
        stream=output,
        find_spec=fake_find_spec,
    )

    assert ready is False
    assert "websockets>=13,<16" in output.getvalue()
    assert "启动桌宠.bat" in output.getvalue()


def test_importing_config_store_does_not_import_websocket_agent_stack():
    """直连模式所需的配置模块不能被 Agent 可选依赖卡死。"""

    script = r"""
import builtins

real_import = builtins.__import__

def reject_websockets(name, *args, **kwargs):
    if name == "websockets" or name.startswith("websockets."):
        raise ModuleNotFoundError("websockets intentionally unavailable")
    return real_import(name, *args, **kwargs)

builtins.__import__ = reject_websockets
import meapet.config.store
print("config-store-ready")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "config-store-ready"


def test_pet_entry_checks_dependencies_before_importing_desktop_app():
    source = (ROOT / "pet.py").read_text(encoding="utf-8")

    check_at = source.index("ensure_pet_dependencies")
    desktop_import_at = source.index("from meapet.desktop.app import main")

    assert check_at < desktop_import_at
    assert source.index("def main(") < desktop_import_at


def test_module_entry_uses_the_same_pre_gui_bootstrap():
    source = (ROOT / "meapet" / "__main__.py").read_text(encoding="utf-8")

    assert "from pet import main" in source
    assert "from meapet.desktop.app import main" not in source


def test_windows_launcher_checks_the_complete_runtime_environment():
    launcher = (ROOT / "启动桌宠.bat").read_text(encoding="utf-8")

    assert "-m meapet.bootstrap --check all" in launcher
    assert "import PyQt5,PIL,requests,numpy,httpx,OpenGL,jieba" not in launcher
