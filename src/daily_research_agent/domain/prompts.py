from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import List


@dataclass(frozen=True)
class ArticleSectionTemplate:
    id: str
    heading: str
    required: bool
    intent: str
    guidance: str


@dataclass(frozen=True)
class ArticleTemplate:
    name: str
    version: int
    title_guidance: str
    sections: List[ArticleSectionTemplate]


class TemplateError(RuntimeError):
    pass


def load_article_template(path: Path) -> ArticleTemplate:
    if not path.exists():
        raise TemplateError(f"Template not found: {path}")
    with path.open("rb") as f:
        data = tomllib.load(f)

    title = data.get("title", {})
    sections = []
    for section in data.get("sections", []):
        sections.append(
            ArticleSectionTemplate(
                id=section.get("id", ""),
                heading=section.get("heading", ""),
                required=bool(section.get("required", False)),
                intent=section.get("intent", ""),
                guidance=section.get("guidance", ""),
            )
        )

    return ArticleTemplate(
        name=data.get("name", "article"),
        version=int(data.get("version", 1)),
        title_guidance=title.get("guidance", ""),
        sections=sections,
    )
