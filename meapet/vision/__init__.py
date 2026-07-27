"""Vision routing and multimodal coordination."""

from importlib import import_module

from meapet.vision.policy import VisionRoute, resolve_vision_route

__all__ = [
    "VisionCoordinator",
    "VisionReply",
    "VisionRoute",
    "resolve_vision_route",
]


def __getattr__(name: str):
    """仅在调用方需要协调器时加载其 Agent 协议依赖。"""

    if name not in {"VisionCoordinator", "VisionReply"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    coordinator = import_module(".coordinator", __name__)
    globals()["VisionCoordinator"] = coordinator.VisionCoordinator
    globals()["VisionReply"] = coordinator.VisionReply
    return globals()[name]


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
