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


def test_agent_link_requires_tool_schema_but_not_http_mcp_server():
    from meapet.bootstrap import required_runtime_dependencies

    dependencies = required_runtime_dependencies(
        {
            "llm": {
                "mode": "agent",
                "agent": {"kind": "agent_link"},
            },
            # 旧版本可能残留 enabled=true；Agent Link 仍只复用当前 WebSocket，
            # 不应因此要求或启动 Companion MCP 的 uvicorn。
            "agent_control": {"enabled": True},
        }
    )

    modules = _dependency_modules(dependencies)
    assert "websockets" in modules
    assert "mcp" in modules
    assert "uvicorn" not in modules
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


def test_agent_preflight_degrades_instead_of_blocking_on_missing_websockets(
    tmp_path,
):
    """websockets 缺失只降级，绝不能让整个桌宠打不开。"""

    from meapet.bootstrap import degraded_dependencies, ensure_pet_dependencies

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

    assert ready is True
    assert "websockets>=13,<16" in output.getvalue()
    assert [d.module for d in degraded_dependencies()] == ["websockets"]


def test_missing_gui_dependency_still_blocks_startup(tmp_path):
    """PyQt5 这类关键依赖缺失仍必须阻断，并给出可见说明。"""

    from meapet.bootstrap import ensure_pet_dependencies

    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"llm":{"mode":"direct"},"agent_control":{"enabled":false}}',
        encoding="utf-8",
    )
    output = StringIO()

    def fake_find_spec(module):
        return None if module == "PyQt5" else object()

    ready = ensure_pet_dependencies(
        tmp_path,
        stream=output,
        find_spec=fake_find_spec,
    )

    assert ready is False
    assert "PyQt5" in output.getvalue()


def test_startup_error_survives_none_stdio(monkeypatch):
    """窗口化打包下 stdout/stderr 均为 None，报错不能再被静默吞掉。"""

    from meapet import bootstrap

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    # 不得抛异常（旧实现在这里静默 no-op，正是 exe 双击无反应的成因）
    bootstrap.emit_startup_error("[MeaPet] unit-test startup failure")

    log_path = bootstrap.startup_error_log_path()
    assert log_path is not None
    assert "unit-test startup failure" in log_path.read_text(encoding="utf-8")
    log_path.unlink()


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


def test_agent_mode_falls_back_to_direct_without_websockets():
    """传输层不可用时运行期降级 direct，且绝不回写用户配置。"""

    source = (ROOT / "meapet" / "desktop" / "app.py").read_text(
        encoding="utf-8"
    )

    fallback_at = source.index('if mode == "agent" and not _websockets_available()')
    adapter_at = source.index("create_agent_adapter_from_config")

    # 降级必须发生在构造 Agent 适配器之前
    assert fallback_at < adapter_at

    # 降级只改本次运行的局部变量，不得触碰 config 或触发保存
    window = source[fallback_at:adapter_at]
    assert 'mode = "direct"' in window
    assert "_save_config" not in window
    assert 'llm_config["mode"]' not in window


def test_config_normalization_never_rewrites_mode_on_missing_websockets():
    """websockets 缺失不能改写持久化配置，否则会毁掉用户的 agent 设置。"""

    from meapet.config import store

    normalized = store.normalize_config(
        {"llm": {"mode": "agent", "backend": "hermes"}}
    )
    assert normalized["llm"]["mode"] == "agent"


def test_module_entry_uses_the_same_pre_gui_bootstrap():
    source = (ROOT / "meapet" / "__main__.py").read_text(encoding="utf-8")

    # _run 是 pet.py 里带可见化兜底的包装，main 本身没有 except 保护。
    assert "from pet import _run" in source
    assert "from meapet.desktop.app import main" not in source


def test_pet_entry_makes_pre_gui_failures_visible():
    """GUI 出现前的异常必须走可见化通道，不能静默退出。"""

    source = (ROOT / "pet.py").read_text(encoding="utf-8")

    assert "emit_startup_error" in source
    assert "except BaseException" in source
    # 入口必须调用带兜底的 _run，而不是裸 main()
    assert "SystemExit(_run())" in source


def test_windows_launcher_checks_the_complete_runtime_environment():
    launcher = (ROOT / "启动桌宠.bat").read_text(encoding="utf-8")

    assert "-m meapet.bootstrap --check all" in launcher
    assert "import PyQt5,PIL,requests,numpy,httpx,OpenGL,jieba" not in launcher


def test_fidus_is_not_in_the_launcher_managed_environment():
    """fidus 不进 `--check all` 那份表：那等于替一台装不出来的机器背书。

    该表的语义是"Windows 启动器负责补齐的完整运行环境"，而 fidus 只有 Linux 产物、
    走发布方声明的 Release 直链而非任何索引里的一个名字——`--check all` 在 Windows 上
    按这张表装它，只会把打包门整个拦死。缺件的真实后果由"随包分发"那条 standing 裁决
    管（落点在 spec），降级提示归 bootstrap，取件本身归 `meapet.fidus_channel`。
    """
    from meapet.bootstrap import all_runtime_dependencies

    assert "fidus" not in _dependency_modules(all_runtime_dependencies())


def test_enabled_fidus_switch_degrades_instead_of_blocking(tmp_path):
    """开关开着而现场没有 fidus：启动日志点名这条能力，但桌宠照常起来。"""
    from meapet import bootstrap

    deps_on = _dependency_modules(
        bootstrap.required_runtime_dependencies({"fidus": {"enabled": True}})
    )
    deps_off = _dependency_modules(
        bootstrap.required_runtime_dependencies({"fidus": {"enabled": False}})
    )
    assert "fidus" in deps_on
    assert "fidus" not in deps_off

    cfg = tmp_path / "config.json"
    cfg.write_text('{"fidus": {"enabled": true}}', encoding="utf-8")
    err = StringIO()
    ok = bootstrap.ensure_pet_dependencies(
        tmp_path,
        config_path=cfg,
        stream=err,
        find_spec=lambda name: None if name == "fidus" else object(),
    )
    assert ok is True
    assert "fidus" in err.getvalue()
    assert "切换点定位测量" in err.getvalue()


def test_fidus_degradation_names_the_fetch_command(tmp_path):
    """降级提示必须给出一条**真能执行**的取件命令。

    改造前这里只说 `pip install -r linux_requirements.txt`，而 fidus 刻意不在那份
    requirements 里 —— 等于把装机的人指向一条走不通的路。
    """
    from meapet import bootstrap

    message = bootstrap.format_degraded_dependencies(
        bootstrap.required_runtime_dependencies({"fidus": {"enabled": True}})
    )
    assert "--install-fidus" in message
    assert "linux_requirements" not in message


def test_other_degradations_do_not_carry_the_fidus_command():
    """反证：那句取件命令只跟着 fidus 出现，不能粘在每条降级提示上。"""
    from meapet import bootstrap

    message = bootstrap.format_degraded_dependencies(
        bootstrap.required_runtime_dependencies(
            {"llm": {"backend": "hermes", "mode": "agent"}}
        )
    )
    assert "--install-fidus" not in message


def test_packaging_gate_uses_the_same_hint_as_the_startup_log():
    """spec 与启动日志共用一句取件说明：两处各写一份，迟早有一份先过期。"""
    from meapet import bootstrap

    spec = (ROOT / "MeaPet.spec").read_text(encoding="utf-8")
    assert "fidus_install_hint" in spec
    assert "--install-fidus" in bootstrap.fidus_install_hint(executable=Path("/x/py"))


def test_bootstrap_does_not_import_the_fetch_path_on_import():
    """`meapet.bootstrap` 是 GUI 之前跑的，取件那一路（urllib/subprocess 之外的部分）
    只在显式 `--install-fidus` 时才拉起来。"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import meapet.bootstrap, sys;"
            "print('meapet.fidus_channel' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(ROOT),
    )
    assert result.stdout.strip() == "False"


def test_install_fidus_flag_runs_the_three_step_pin_and_reports(capsys, monkeypatch):
    from meapet import bootstrap, fidus_channel

    calls: list[tuple[object, ...]] = []

    def fake_install(*args, **kwargs):
        calls.append((args, kwargs))
        return "fidus 0.1.0-beta.2 已就位"

    monkeypatch.setattr(fidus_channel, "install_fidus", fake_install)
    assert bootstrap.main(["--install-fidus"]) == 0
    assert calls == [((), {})]
    assert "已就位" in capsys.readouterr().out


def test_install_fidus_flag_reports_failure_as_nonzero_exit(capsys, monkeypatch):
    from meapet import bootstrap, fidus_channel

    def boom(*args, **kwargs):
        raise fidus_channel.FidusChannelError("边车声明与钉住的不符")

    monkeypatch.setattr(fidus_channel, "install_fidus", boom)
    assert bootstrap.main(["--install-fidus"]) == 1
    assert "取件未完成" in capsys.readouterr().err
