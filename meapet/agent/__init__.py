"""外部 Agent 运行时的原生 WebSocket 适配器。"""

from importlib import import_module

from .base import (
    AgentTurnRequest,
    FormatRepairRequired,
    ToolStatus,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from .presentation import AgentTurnPresentation

__all__ = [
    "AgentTurnRequest",
    "AgentTurnPresentation",
    "FormatRepairRequired",
    "HermesAdapter",
    "HermesCapabilities",
    "HermesConfig",
    "OpenClawAdapter",
    "OpenClawCapabilities",
    "OpenClawConfig",
    "ToolStatus",
    "TurnCancelled",
    "TurnCompleted",
    "TurnFailed",
    "create_agent_adapter_from_config",
]

_LAZY_EXPORTS = {
    "HermesAdapter": (".hermes", "HermesAdapter"),
    "HermesCapabilities": (".hermes", "HermesCapabilities"),
    "HermesConfig": (".hermes", "HermesConfig"),
    "OpenClawAdapter": (".openclaw", "OpenClawAdapter"),
    "OpenClawCapabilities": (".openclaw", "OpenClawCapabilities"),
    "OpenClawConfig": (".openclaw", "OpenClawConfig"),
    "create_agent_adapter_from_config": (
        ".factory",
        "create_agent_adapter_from_config",
    ),
}


def __getattr__(name: str):
    """按需加载 WebSocket/密码学依赖，避免阻塞直连模式启动。"""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
