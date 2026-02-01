from __future__ import annotations

from typing import Any, Optional, Set

from langchain_core.callbacks import BaseCallbackHandler

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
