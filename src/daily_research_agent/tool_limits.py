from __future__ import annotations

from typing import Any, Iterable, Optional, Set, List

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from daily_research_agent.audit import AuditLogger


class ToolLimitCallbackHandler(BaseCallbackHandler):
    def __init__(
        self,
        max_calls: int,
        tool_names: Set[str],
        audit_logger: Optional[AuditLogger] = None,
    ) -> None:
        self._max_calls = max_calls
        self._tool_names = tool_names
        self._audit = audit_logger
        self._count = 0

    def _extract_app_run_id(self, kwargs: Any) -> str:
        metadata = None
        if isinstance(kwargs, dict):
            metadata = kwargs.get("metadata")
        if isinstance(metadata, dict) and metadata.get("run_id"):
            return str(metadata["run_id"])
        return ""

    def on_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        name = kwargs.get("name")
        if name is None and isinstance(serialized, dict):
            name = serialized.get("name")
        if not name or name not in self._tool_names:
            return
        self._count += 1
        if self._count > self._max_calls:
            if self._audit and self._audit.enabled:
                self._audit.event(
                    "tool_limit_exceeded",
                    {
                        "app_run_id": self._extract_app_run_id(kwargs),
                        "tool_name": str(name),
                        "max_calls": self._max_calls,
                        "count": self._count,
                        "run_id": str(kwargs.get("run_id", "")),
                    },
                )
            raise RuntimeError(
                f"max_web_queries exceeded: {self._count} > {self._max_calls}"
            )


class ToolCallLimiter:
    def __init__(
        self,
        max_calls: int,
        tool_names: Set[str],
        audit_logger: Optional[AuditLogger] = None,
        run_id: str = "",
    ) -> None:
        self._max_calls = max_calls
        self._tool_names = tool_names
        self._audit = audit_logger
        self._run_id = run_id
        self._count = 0

    def _emit_limit_event(self, tool_name: str) -> None:
        if self._audit and self._audit.enabled:
            self._audit.event(
                "tool_limit_exceeded",
                {
                    "app_run_id": self._run_id,
                    "tool_name": tool_name,
                    "max_calls": self._max_calls,
                    "count": self._count,
                    "run_id": self._run_id,
                },
            )

    def check(self, tool_name: str) -> None:
        if tool_name not in self._tool_names:
            return
        self._count += 1
        if self._count > self._max_calls:
            self._emit_limit_event(tool_name)
            raise RuntimeError(
                f"max_web_queries exceeded: {self._count} > {self._max_calls}"
            )

    def wrap_tools(self, tools: Iterable[BaseTool]) -> List[BaseTool]:
        wrapped = []
        for tool in tools:
            if tool.name in self._tool_names:
                wrapped.append(_LimitedTool(tool, self))
            else:
                wrapped.append(tool)
        return wrapped


def _build_tool_input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    if kwargs:
        return kwargs
    if len(args) == 1:
        return args[0]
    if args:
        return args
    return None


class _LimitedTool(BaseTool):
    _tool: BaseTool = PrivateAttr()
    _limiter: ToolCallLimiter = PrivateAttr()

    def __init__(self, tool: BaseTool, limiter: ToolCallLimiter) -> None:
        super().__init__(
            name=tool.name,
            description=tool.description,
            args_schema=getattr(tool, "args_schema", None),
            return_direct=getattr(tool, "return_direct", False),
        )
        self._tool = tool
        self._limiter = limiter

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        self._limiter.check(self.name)
        return self._tool.invoke(_build_tool_input(args, kwargs))

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        self._limiter.check(self.name)
        return await self._tool.ainvoke(_build_tool_input(args, kwargs))
