from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
import json


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def asdict_safe(obj: Any) -> Any:
    try:
        return asdict(obj)
    except TypeError:
        return obj
