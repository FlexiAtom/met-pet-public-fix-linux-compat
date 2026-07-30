"""标准 MCP Streamable HTTP 工具表面（网络安全封装在 runner）。"""

from __future__ import annotations

from meapet.dependencies import MCP_REQUIREMENT

from .broker import CompanionControlBroker
from .capabilities import CapabilityRegistry, build_companion_capabilities


def build_companion_mcp(
    broker: CompanionControlBroker,
    *,
    transport_security=None,
    registry: CapabilityRegistry | None = None,
):
    """从统一能力注册表构造 FastMCP 服务器。"""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - 由可选依赖环境触发
        raise RuntimeError(
            f"Companion MCP 需要安装 {MCP_REQUIREMENT}"
        ) from exc

    server = FastMCP(
        "MeaPet Companion",
        instructions=(
            "受限桌宠前端控制；仅用于说话、表情、只读状态和逐次确认截图。"
        ),
        stateless_http=True,
        json_response=True,
        transport_security=transport_security,
    )

    capabilities = registry or build_companion_capabilities(broker)
    for tool in capabilities.tools():
        server.add_tool(
            tool.handler,
            name=tool.name,
            description=tool.description,
            structured_output=tool.structured_output,
        )

    return server
