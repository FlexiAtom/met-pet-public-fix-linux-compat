"""GUI 导入前可运行的 MeaPet 启动依赖检查。

这个模块必须保持为纯标准库实现。启动脚本依赖它在 PyQt5 或某个功能依赖
尚未安装时给出可执行的修复指引，因此不要在模块顶层导入任何第三方包。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence, TextIO

from meapet.dependencies import (
    CRYPTOGRAPHY_REQUIREMENT,
    HTTPX_REQUIREMENT,
    JIEBA_REQUIREMENT,
    MCP_REQUIREMENT,
    PILLOW_REQUIREMENT,
    PYOPENGL_REQUIREMENT,
    PYQT_REQUIREMENT,
    REQUESTS_REQUIREMENT,
    TRANSLATORS_REQUIREMENT,
    UVICORN_REQUIREMENT,
    WEBSOCKETS_REQUIREMENT,
)


@dataclass(frozen=True)
class RuntimeDependency:
    """一个可通过导入名探测的运行时依赖。"""

    module: str
    requirement: str
    purpose: str


# 缺失后界面根本无法显示的依赖。只有这些才允许阻断启动。
_CRITICAL_MODULES = frozenset({"PyQt5", "PIL", "jieba"})


def is_critical(dependency: RuntimeDependency) -> bool:
    """True 表示缺失它就没法把窗口显示出来，必须阻断启动。"""
    return dependency.module in _CRITICAL_MODULES


_BASE_DEPENDENCIES = (
    RuntimeDependency("PyQt5", PYQT_REQUIREMENT, "桌面界面"),
    RuntimeDependency("PIL", PILLOW_REQUIREMENT, "PNG 渲染与截图"),
    RuntimeDependency("requests", REQUESTS_REQUIREMENT, "兼容网络服务"),
    RuntimeDependency("httpx", HTTPX_REQUIREMENT, "异步模型请求"),
    RuntimeDependency("jieba", JIEBA_REQUIREMENT, "中文记忆分词"),
)

_AGENT_DEPENDENCIES = (
    RuntimeDependency(
        "websockets",
        WEBSOCKETS_REQUIREMENT,
        "WebSocket Agent 通信",
    ),
)

_OPENCLAW_DEPENDENCIES = (
    RuntimeDependency(
        "cryptography",
        CRYPTOGRAPHY_REQUIREMENT,
        "OpenClaw 设备身份签名",
    ),
)

_CAPABILITY_DEPENDENCIES = (
    RuntimeDependency(
        "mcp",
        MCP_REQUIREMENT,
        "MeaPet 前端工具 Schema",
    ),
)

_CONTROL_DEPENDENCIES = (
    *_CAPABILITY_DEPENDENCIES,
    RuntimeDependency("uvicorn", UVICORN_REQUIREMENT, "Companion MCP 服务"),
)

# Windows 启动器负责把完整源码运行环境补齐。以下组件具有安全回退路径，
# 所以手动运行 pet.py 时不应仅因它们缺失就阻断 PNG/非翻译场景。
_OPTIONAL_SOURCE_DEPENDENCIES = (
    RuntimeDependency("numpy", "numpy", "本地语音与模型支持"),
    RuntimeDependency("OpenGL", PYOPENGL_REQUIREMENT, "Live2D 渲染"),
    RuntimeDependency(
        "translators",
        TRANSLATORS_REQUIREMENT,
        "语音翻译",
    ),
    # MODIFIED: 显式加入 live2d-py 依赖，以便后续进行真实可用性检查
    RuntimeDependency("live2d", "live2d-py", "Live2D 模型渲染"),
)


def _is_frozen() -> bool:
    """不导入 meapet.paths 也能判断冻结态，避免早期导入失败。"""
    return bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def startup_error_log_path() -> Path | None:
    """启动错误日志路径：冻结态放在 exe 旁边，源码态放在项目根。"""
    try:
        if _is_frozen():
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(__file__).resolve().parents[1]
        return base / "meapet_startup_error.log"
    except Exception:
        return None


def emit_startup_error(message: str, *, stream: TextIO | None = None) -> None:
    """把 GUI 出现之前的失败告知用户。"""
    target = stream if stream is not None else sys.stderr
    if target is not None:
        try:
            print(message, file=target)
        except Exception:
            pass

    log_path = startup_error_log_path()
    if log_path is not None:
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(message.rstrip("\n") + "\n\n")
        except OSError:
            pass

    if stream is None and _is_frozen() and sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None,
                message,
                "MeaPet 启动失败",
                0x10,
            )
        except Exception:
            pass


def _deduplicate(
    dependencies: Iterable[RuntimeDependency],
) -> tuple[RuntimeDependency, ...]:
    result: list[RuntimeDependency] = []
    seen: set[str] = set()
    for dependency in dependencies:
        if dependency.module in seen:
            continue
        seen.add(dependency.module)
        result.append(dependency)
    return tuple(result)


def required_runtime_dependencies(
    config: Mapping[str, object] | None,
) -> tuple[RuntimeDependency, ...]:
    """返回当前配置在导入桌面应用前必须具备的依赖。"""
    config = config if isinstance(config, Mapping) else {}
    llm = config.get("llm")
    llm = llm if isinstance(llm, Mapping) else {}
    agent = llm.get("agent")
    agent = agent if isinstance(agent, Mapping) else {}
    control = config.get("agent_control")
    control = control if isinstance(control, Mapping) else {}

    dependencies: list[RuntimeDependency] = list(_BASE_DEPENDENCIES)
    agent_kind = str(
        agent.get("kind") or llm.get("backend") or "hermes"
    ).strip().lower()
    requested_mode = str(llm.get("mode") or "").strip().lower()
    if requested_mode not in {"direct", "agent"}:
        requested_mode = (
            "agent"
            if agent_kind in {"hermes", "openclaw", "agent_link"}
            and bool(llm.get("backend") or agent.get("kind"))
            else "direct"
        )
    agent_mode = requested_mode == "agent"
    if agent_mode:
        dependencies.extend(_AGENT_DEPENDENCIES)
        if agent_kind == "openclaw":
            dependencies.extend(_OPENCLAW_DEPENDENCIES)
        elif agent_kind == "agent_link":
            dependencies.extend(_CAPABILITY_DEPENDENCIES)
    if (
        agent_mode
        and agent_kind != "agent_link"
        and bool(control.get("enabled", False))
    ):
        dependencies.extend(_CONTROL_DEPENDENCIES)
    return _deduplicate(dependencies)


def all_runtime_dependencies() -> tuple[RuntimeDependency, ...]:
    """返回启动器应安装并检查的完整源码运行环境。"""
    return _deduplicate(
        (
            *_BASE_DEPENDENCIES,
            *_AGENT_DEPENDENCIES,
            *_OPENCLAW_DEPENDENCIES,
            *_CONTROL_DEPENDENCIES,
            *_OPTIONAL_SOURCE_DEPENDENCIES,
        )
    )


# ADDED: 对特定模块进行真实可用性验证（不仅检查模块存在，还尝试导入并调用轻量接口）
def _verify_module_available(module_name: str) -> bool:
    """尝试导入模块并执行一个最小可用性检查，返回 True 表示真正可用。

    当前仅对 'live2d' 模块实施特殊验证（读取版本号），其他模块回退到
    find_spec 的结果。此函数不会抛出异常，所有失败均视为不可用。
    """
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            return False
        # 对 live2d 进行真实调用验证
        if module_name == "live2d":
            mod = importlib.import_module(module_name)
            # 尝试获取版本号（多数 Python 包提供 __version__）
            version = getattr(mod, "__version__", None)
            if version is not None:
                return True
            # 如果没有 __version__，尝试调用一个无害的静态方法
            if hasattr(mod, "version") and callable(mod.version):
                ver = mod.version()
                return ver is not None
            # 最低限度：检查核心类是否存在（例如 LAppModel）
            if hasattr(mod, "LAppModel"):
                return True
            # 以上都失败则视为不可用
            return False
        # 其他模块仅凭 find_spec 即可
        return True
    except Exception:
        return False


def find_missing_dependencies(
    dependencies: Iterable[RuntimeDependency],
    *,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> tuple[RuntimeDependency, ...]:
    """只用模块规格探测依赖，不执行第三方模块代码。

    但对 live2d 模块会额外进行真实可用性检查（通过 _verify_module_available）。
    """
    missing: list[RuntimeDependency] = []
    for dependency in dependencies:
        try:
            # 基础检查：模块规格是否存在
            available = find_spec(dependency.module) is not None
            # 对 live2d 进一步做真实可用性验证
            if available and dependency.module == "live2d":
                available = _verify_module_available(dependency.module)
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        if not available:
            missing.append(dependency)
    return tuple(missing)


def _read_startup_config(
    project_root: Path,
    config_path: Path | None = None,
) -> Mapping[str, object]:
    if config_path is None:
        primary = project_root / "config.json"
        config_path = (
            primary
            if primary.is_file()
            else project_root / "config.example.json"
        )
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def format_missing_dependencies(
    missing: Sequence[RuntimeDependency],
    *,
    project_root: Path,
    executable: Path,
) -> str:
    """生成不依赖 GUI 的、可直接复制命令的错误说明。"""
    lines = [
        "[MeaPet] 启动前依赖检查失败。",
        "",
        "缺少运行依赖：",
    ]
    lines.extend(
        f"  - {dependency.requirement}（{dependency.purpose}）"
        for dependency in missing
    )
    if _is_frozen():
        log_path = startup_error_log_path()
        lines.extend(
            (
                "",
                "这个发行包构建不完整：上述依赖没有被打进程序内部。",
                "请重新下载完整的发行包，或在补齐依赖后重新构建：",
                '  python -m pip install -e ".[all]"',
                "  powershell -ExecutionPolicy Bypass -File scripts/build_windows.ps1",
            )
        )
        if log_path is not None:
            lines.extend(("", f"本次启动日志：{log_path}"))
        return "\n".join(lines)

    requirements = project_root / "linux_requirements.txt"
    lines.extend(
        (
            "",
            f"当前 Python：{executable}",
            "请用当前 Python 补齐项目依赖：",
            f'  "{executable}" -m pip install -r "{requirements}"',
            "",
            "Windows 用户也可以重新双击「启动桌宠.bat」；"
            "启动器会自动修复缺少新依赖的旧 .venv。",
        )
    )
    return "\n".join(lines)


_DEGRADED: tuple[RuntimeDependency, ...] = ()


def degraded_dependencies() -> tuple[RuntimeDependency, ...]:
    """上次依赖检查中缺失、但已降级放行的子系统依赖。"""
    return _DEGRADED


def format_degraded_dependencies(
    degraded: Sequence[RuntimeDependency],
) -> str:
    """生成降级说明，供启动日志与 GUI 非阻塞提示复用。"""
    lines = ["[MeaPet] 以下可选子系统依赖缺失，相关功能已降级："]
    lines.extend(
        f"  - {dependency.requirement}（{dependency.purpose}）"
        for dependency in degraded
    )
    return "\n".join(lines)


def ensure_pet_dependencies(
    project_root: str | Path,
    *,
    config_path: str | Path | None = None,
    stream: TextIO | None = None,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> bool:
    """在导入桌面应用之前检查当前配置真正需要的依赖。

    只有界面本身跑不起来（``PyQt5``/``PIL``/``jieba``）才阻断启动。
    ``websockets``/``mcp`` 之类子系统依赖都有回退路径，缺失时仅记录降级，
    不能让整个桌宠打不开。对 live2d 模块会额外进行真实可用性验证。
    """
    global _DEGRADED

    root = Path(project_root).resolve()
    resolved_config = Path(config_path) if config_path is not None else None
    config = _read_startup_config(root, resolved_config)
    # 使用增强的 find_missing_dependencies（已内置 live2d 真实检查）
    missing = find_missing_dependencies(
        required_runtime_dependencies(config),
        find_spec=find_spec,
    )
    blocking = tuple(d for d in missing if is_critical(d))
    _DEGRADED = tuple(d for d in missing if not is_critical(d))

    if _DEGRADED:
        emit_startup_error(
            format_degraded_dependencies(_DEGRADED),
            stream=stream,
        )
    if not blocking:
        return True
    emit_startup_error(
        format_missing_dependencies(
            blocking,
            project_root=root,
            executable=Path(sys.executable),
        ),
        stream=stream,
    )
    return False


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在导入 MeaPet GUI 前检查运行依赖",
    )
    parser.add_argument(
        "--check",
        choices=("pet", "all"),
        default="pet",
        help="pet=按当前配置检查；all=检查启动器管理的完整环境",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--config", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.project_root.resolve()
    if args.check == "all":
        dependencies = all_runtime_dependencies()
    else:
        config = _read_startup_config(root, args.config)
        dependencies = required_runtime_dependencies(config)

    missing = find_missing_dependencies(dependencies)
    if not missing:
        return 0
    print(
        format_missing_dependencies(
            missing,
            project_root=root,
            executable=Path(sys.executable),
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

