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
        "Hermes/OpenClaw Agent",
    ),
)

_OPENCLAW_DEPENDENCIES = (
    RuntimeDependency(
        "cryptography",
        CRYPTOGRAPHY_REQUIREMENT,
        "OpenClaw 设备身份签名",
    ),
)

_CONTROL_DEPENDENCIES = (
    RuntimeDependency("mcp", MCP_REQUIREMENT, "Companion MCP 服务"),
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
)


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
    """返回当前配置在导入桌面应用前必须具备的依赖。

    直连模式不会加载 WebSocket Agent 或 Companion MCP 栈。Agent 模式和
    Companion 服务只有在配置明确启用时才加入对应依赖。
    """

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
            if agent_kind in {"hermes", "openclaw"}
            and bool(llm.get("backend") or agent.get("kind"))
            else "direct"
        )
    agent_mode = requested_mode == "agent"
    if agent_mode:
        dependencies.extend(_AGENT_DEPENDENCIES)
        if agent_kind == "openclaw":
            dependencies.extend(_OPENCLAW_DEPENDENCIES)
    if agent_mode and bool(control.get("enabled", False)):
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


def find_missing_dependencies(
    dependencies: Iterable[RuntimeDependency],
    *,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> tuple[RuntimeDependency, ...]:
    """只用模块规格探测依赖，不执行第三方模块代码。"""

    missing: list[RuntimeDependency] = []
    for dependency in dependencies:
        try:
            available = find_spec(dependency.module) is not None
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


def ensure_pet_dependencies(
    project_root: str | Path,
    *,
    config_path: str | Path | None = None,
    stream: TextIO | None = None,
    find_spec: Callable[[str], object | None] = importlib.util.find_spec,
) -> bool:
    """在导入桌面应用之前检查当前配置真正需要的依赖。"""

    root = Path(project_root).resolve()
    resolved_config = Path(config_path) if config_path is not None else None
    config = _read_startup_config(root, resolved_config)
    missing = find_missing_dependencies(
        required_runtime_dependencies(config),
        find_spec=find_spec,
    )
    if not missing:
        return True
    output = stream if stream is not None else sys.stderr
    print(
        format_missing_dependencies(
            missing,
            project_root=root,
            executable=Path(sys.executable),
        ),
        file=output,
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
