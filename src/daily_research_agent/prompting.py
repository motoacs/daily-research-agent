from __future__ import annotations

import string
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, Iterable

from daily_research_agent.config import ConfigError, PromptRegistryEntry


@dataclass(frozen=True)
class ArtifactPathGroup:
    inputs: Any
    research: Any
    draft: Any
    final: Any
    run: Any


def _make_namespace(mapping: Dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(**mapping)


def build_prompt_context(values: Dict[str, Any]) -> Dict[str, Any]:
    return dict(values)


def render_prompt(entry: PromptRegistryEntry, context: Dict[str, Any]) -> str:
    formatter = string.Formatter()
    missing: set[str] = set()
    for _, field_name, _, _ in formatter.parse(entry.text):
        if not field_name:
            continue
        if field_name not in context:
            missing.add(field_name)
    if missing:
        raise ConfigError(f"Missing prompt variables: {', '.join(sorted(missing))}")
    return entry.text.format_map(context)


def render_prompt_with_dotted(entry: PromptRegistryEntry, context: Dict[str, Any]) -> str:
    formatter = string.Formatter()
    missing: set[str] = set()
    for _, field_name, _, _ in formatter.parse(entry.text):
        if not field_name:
            continue
        root = field_name.split(".")[0]
        if root not in context:
            missing.add(root)
    if missing:
        raise ConfigError(f"Missing prompt variables: {', '.join(sorted(missing))}")
    return entry.text.format(**context)
