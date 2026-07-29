"""MeaPet 本机能力的统一注册表。

同一份注册同时供 Companion MCP 和 Agent Link 使用。能力实现只依赖
``CompanionControlBroker``，网络协议不得直接触碰 Qt 对象。
"""

from __future__ import annotations

import copy
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping


ToolHandler = Callable[..., Awaitable[Any]]
RegistryListener = Callable[[int], None]


class CapabilityError(RuntimeError):
    """能力注册或调用失败。"""


class CapabilityNotFoundError(CapabilityError):
    """请求了当前设备没有暴露的能力。"""


class CapabilityArgumentsError(CapabilityError):
    """Tool 参数不符合注册时生成的 JSON Schema。"""


@dataclass(frozen=True)
class CapabilityTool:
    """一个可同时投影为 MCP Tool 和 Agent Link Tool 的能力。"""

    name: str
    description: str
    handler: ToolHandler = field(repr=False, compare=False)
    structured_output: bool = True
    result_modalities: tuple[str, ...] = ()
    _mcp_tool: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        description = str(self.description or "").strip()
        if not name or len(name) > 128:
            raise ValueError("能力名称不能为空且不能超过 128 个字符")
        if not description:
            raise ValueError(f"能力 {name} 必须提供用途和限制说明")

        # FastMCP 的 Tool.from_function 是当前项目生成、校验 JSON Schema 的
        # 唯一事实源，避免 Agent Link 和 HTTP MCP 各维护一份参数定义。
        from mcp.server.fastmcp.tools import Tool

        mcp_tool = Tool.from_function(
            self.handler,
            name=name,
            description=description,
            structured_output=self.structured_output,
        )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(
            self,
            "result_modalities",
            tuple(
                dict.fromkeys(
                    str(item or "").strip().lower()
                    for item in self.result_modalities
                    if str(item or "").strip()
                )
            ),
        )
        object.__setattr__(self, "_mcp_tool", mcp_tool)

    @property
    def input_schema(self) -> dict[str, Any]:
        """返回不要求模型填写协议级 request_id 的输入 Schema。"""
        schema = copy.deepcopy(getattr(self._mcp_tool, "parameters", {}) or {})
        properties = schema.get("properties")
        if isinstance(properties, dict):
            properties.pop("request_id", None)
        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [
                name for name in required if name != "request_id"
            ]
        return schema

    @property
    def output_schema(self) -> dict[str, Any] | None:
        metadata = getattr(self._mcp_tool, "fn_metadata", None)
        schema = getattr(metadata, "output_schema", None)
        return copy.deepcopy(schema) if isinstance(schema, dict) else None

    def protocol_definition(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
        output_schema = self.output_schema
        if output_schema is not None:
            result["output_schema"] = output_schema
        if self.result_modalities:
            result["result_modalities"] = list(self.result_modalities)
        return result

    async def call(
        self,
        arguments: Mapping[str, object] | None,
        *,
        request_id: str,
    ) -> Any:
        values = dict(arguments or {})
        parameters = getattr(self._mcp_tool, "parameters", {}) or {}
        properties = parameters.get("properties")
        if isinstance(properties, dict) and "request_id" in properties:
            # request_id 属于传输幂等键，不让模型覆盖。
            values["request_id"] = request_id
        try:
            result = await self._mcp_tool.run(values)
        except Exception as exc:
            # FastMCP 会在这里完成 Pydantic 参数校验。上层只需要稳定错误类别，
            # 不向 Agent 泄露 Python 堆栈。
            cause = exc
            while cause.__cause__ is not None and cause.__cause__ is not cause:
                cause = cause.__cause__
            if type(cause).__name__ in {"ValidationError", "InvalidSignature"}:
                raise CapabilityArgumentsError(
                    f"能力 {self.name} 的参数无效"
                ) from exc
            raise
        try:
            json.dumps(result, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise CapabilityError(
                f"能力 {self.name} 返回了无法序列化的结果"
            ) from exc
        return result


class CapabilityRegistry:
    """线程安全、带修订号的能力集合。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tools: dict[str, CapabilityTool] = {}
        self._revision = 0
        self._listeners: list[RegistryListener] = []

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def add_tool(
        self,
        handler: ToolHandler,
        *,
        name: str,
        description: str,
        structured_output: bool = True,
        result_modalities: tuple[str, ...] = (),
    ) -> CapabilityTool:
        tool = CapabilityTool(
            name=name,
            description=description,
            handler=handler,
            structured_output=structured_output,
            result_modalities=result_modalities,
        )
        with self._lock:
            if tool.name in self._tools:
                raise ValueError(f"能力名称重复：{tool.name}")
            self._tools[tool.name] = tool
            self._revision += 1
            revision = self._revision
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(revision)
        return tool

    def remove_tool(self, name: str) -> bool:
        key = str(name or "").strip()
        with self._lock:
            if key not in self._tools:
                return False
            self._tools.pop(key)
            self._revision += 1
            revision = self._revision
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(revision)
        return True

    def subscribe(self, listener: RegistryListener) -> Callable[[], None]:
        with self._lock:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._listeners.remove(listener)
                except ValueError:
                    pass

        return unsubscribe

    def tools(self) -> tuple[CapabilityTool, ...]:
        with self._lock:
            return tuple(self._tools.values())

    def protocol_snapshot(self) -> dict[str, Any]:
        with self._lock:
            revision = self._revision
            tools = tuple(self._tools.values())
        return {
            "revision": revision,
            "tools": [tool.protocol_definition() for tool in tools],
        }

    async def call(
        self,
        name: str,
        arguments: Mapping[str, object] | None,
        *,
        request_id: str,
    ) -> Any:
        key = str(name or "").strip()
        with self._lock:
            tool = self._tools.get(key)
        if tool is None:
            raise CapabilityNotFoundError(f"未知能力：{key}")
        return await tool.call(arguments, request_id=request_id)


def build_companion_capabilities(broker) -> CapabilityRegistry:
    """用现有 Broker 构造内置桌宠能力。"""
    registry = CapabilityRegistry()

    async def meapet_say(
        segments: list[dict[str, Any]],
        request_id: str = "",
    ) -> dict[str, Any]:
        """排队一到多个完整回复分段；不会抢占用户正在等待的回复。"""
        return await broker.say(segments, request_id=request_id)

    async def meapet_express(
        mood: str = "",
        motion: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        """请求当前前端明确支持的情绪或动作，不做值回退。"""
        return await broker.express(
            mood=mood,
            motion=motion,
            request_id=request_id,
        )

    async def meapet_get_state() -> dict[str, Any]:
        """读取不含路径、密钥、记忆和全文的前端能力与状态摘要。"""
        return await broker.get_state()

    async def meapet_capture_screen(
        scope: str = "full_screen",
        region: dict[str, int] | None = None,
        application: str = "",
        request_id: str = "",
    ) -> dict[str, Any]:
        """请求一次本机确认后的截图；授权不复用，截图不落盘。"""
        return await broker.capture_screen(
            scope=scope,
            region=region,
            application=application,
            request_id=request_id,
        )

    registry.add_tool(
        meapet_say,
        name="meapet.say",
        description=meapet_say.__doc__ or "",
    )
    registry.add_tool(
        meapet_express,
        name="meapet.express",
        description=meapet_express.__doc__ or "",
    )
    registry.add_tool(
        meapet_get_state,
        name="meapet.get_state",
        description=meapet_get_state.__doc__ or "",
    )
    registry.add_tool(
        meapet_capture_screen,
        name="meapet.capture_screen",
        description=meapet_capture_screen.__doc__ or "",
        result_modalities=("image",),
    )
    return registry
