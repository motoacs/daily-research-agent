from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from langchain_core.callbacks import BaseCallbackHandler


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool
    path_template: str
    log_llm_events: bool
    log_tool_events: bool
    redaction: str


class AuditLogger:
    def __init__(self, path: Path, enabled: bool) -> None:
        self._path = path
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def event(self, name: str, payload: Dict[str, Any]) -> None:
        if not self._enabled:
            return
        payload = dict(payload)
        payload["event"] = name
        payload["time"] = datetime.now(timezone.utc).isoformat()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _summarize_tool_input(input_payload: Any) -> Dict[str, Any]:
    if isinstance(input_payload, dict):
        return {
            "type": "dict",
            "keys": list(input_payload.keys())[:50],
            "size": len(input_payload),
        }
    if isinstance(input_payload, list):
        return {"type": "list", "size": len(input_payload)}
    if isinstance(input_payload, str):
        return {"type": "str", "length": len(input_payload)}
    return {"type": type(input_payload).__name__}


def _estimate_output_size(output: Any) -> Dict[str, Any]:
    if isinstance(output, dict):
        return {"type": "dict", "size": len(output)}
    if isinstance(output, list):
        return {"type": "list", "size": len(output)}
    if isinstance(output, str):
        return {"type": "str", "length": len(output)}
    return {"type": type(output).__name__}


def _extract_model_name(serialized: Any) -> Optional[str]:
    if isinstance(serialized, dict):
        if "id" in serialized and serialized["id"]:
            return str(serialized["id"])
        if "name" in serialized and serialized["name"]:
            return str(serialized["name"])
        kwargs = serialized.get("kwargs")
        if isinstance(kwargs, dict) and kwargs.get("model"):
            return str(kwargs["model"])
    return None


class AuditCallbackHandler(BaseCallbackHandler):
    def __init__(self, logger: AuditLogger, config: AuditConfig) -> None:
        self._logger = logger
        self._config = config
        self._tool_starts: Dict[str, float] = {}
        self._tool_names: Dict[str, str] = {}
        self._llm_starts: Dict[str, float] = {}
        self._llm_models: Dict[str, Optional[str]] = {}

    def on_tool_start(self, serialized: Any, input_str: Any, **kwargs: Any) -> None:
        if not self._config.log_tool_events or not self._logger.enabled:
            return
        run_id = str(kwargs.get("run_id", ""))
        name = kwargs.get("name") or (serialized.get("name") if isinstance(serialized, dict) else None)
        if run_id:
            self._tool_starts[run_id] = time.monotonic()
            if name:
                self._tool_names[run_id] = str(name)
        self._logger.event(
            "tool_started",
            {
                "run_id": run_id,
                "tool_name": str(name) if name else None,
                "input": _summarize_tool_input(input_str),
                "parent_run_id": str(kwargs.get("parent_run_id", "")),
            },
        )

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if not self._config.log_tool_events or not self._logger.enabled:
            return
        run_id = str(kwargs.get("run_id", ""))
        start = self._tool_starts.pop(run_id, None)
        duration_ms = int((time.monotonic() - start) * 1000) if start else None
        tool_name = self._tool_names.pop(run_id, None)
        self._logger.event(
            "tool_finished",
            {
                "run_id": run_id,
                "tool_name": tool_name,
                "duration_ms": duration_ms,
                "output": _estimate_output_size(output),
                "parent_run_id": str(kwargs.get("parent_run_id", "")),
            },
        )

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        if not self._config.log_tool_events or not self._logger.enabled:
            return
        run_id = str(kwargs.get("run_id", ""))
        tool_name = self._tool_names.pop(run_id, None)
        self._logger.event(
            "tool_failed",
            {
                "run_id": run_id,
                "tool_name": tool_name,
                "error": str(error),
                "parent_run_id": str(kwargs.get("parent_run_id", "")),
            },
        )

    def on_llm_start(self, serialized: Any, prompts: Any, **kwargs: Any) -> None:
        if not self._config.log_llm_events or not self._logger.enabled:
            return
        run_id = str(kwargs.get("run_id", ""))
        self._llm_starts[run_id] = time.monotonic()
        model_name = _extract_model_name(serialized)
        self._llm_models[run_id] = model_name
        self._logger.event(
            "llm_started",
            {
                "run_id": run_id,
                "model": model_name,
                "parent_run_id": str(kwargs.get("parent_run_id", "")),
                "prompt_count": len(prompts) if isinstance(prompts, list) else None,
            },
        )

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if not self._config.log_llm_events or not self._logger.enabled:
            return
        run_id = str(kwargs.get("run_id", ""))
        start = self._llm_starts.pop(run_id, None)
        duration_ms = int((time.monotonic() - start) * 1000) if start else None
        model_name = self._llm_models.pop(run_id, None)
        usage = None
        llm_output = getattr(response, "llm_output", None)
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage")
        self._logger.event(
            "llm_finished",
            {
                "run_id": run_id,
                "model": model_name,
                "duration_ms": duration_ms,
                "usage": usage,
                "parent_run_id": str(kwargs.get("parent_run_id", "")),
            },
        )

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        if not self._config.log_llm_events or not self._logger.enabled:
            return
        run_id = str(kwargs.get("run_id", ""))
        model_name = self._llm_models.pop(run_id, None)
        self._logger.event(
            "llm_failed",
            {
                "run_id": run_id,
                "model": model_name,
                "error": str(error),
                "parent_run_id": str(kwargs.get("parent_run_id", "")),
            },
        )
