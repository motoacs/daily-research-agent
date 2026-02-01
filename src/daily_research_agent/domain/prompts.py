from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import tomllib
from typing import Dict, List


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


def build_research_prompt(
    language: str,
    source_priority: str,
    preset_prompt: str,
    daily_sites: List[str],
    x_usage_policy: str,
    max_web_queries: int,
    date_value: date,
) -> str:
    daily_list = "\n".join(f"- {url}" for url in daily_sites)
    return "\n".join(
        [
            f"Language: {language}",
            "You are the Researcher agent.",
            "Goal: collect reliable findings with citations and note uncertainties.",
            f"Date: {date_value.isoformat()}",
            "Source priorities:",
            source_priority.strip(),
            "Daily sites to check:",
            daily_list.strip() or "(none)",
            "X usage policy:",
            x_usage_policy.strip() or "(X disabled)",
            f"Max web queries: {max_web_queries}",
            "Use the available web tools to gather sources.",
            "When evidence is weak, mark confidence as low.",
            "Return only JSON with keys: findings, sources, memo_markdown, missing_info.",
            "findings: list of {claim, evidence, confidence, sources:[url]}",
            "sources: list of {url, title, publisher, published_at, snippet}",
            "memo_markdown: markdown research notes with accepted/rejected reasoning.",
            "missing_info: list of unanswered questions.",
            "Preset prompt:",
            preset_prompt.strip(),
        ]
    )


def build_writer_prompt(
    language: str,
    source_priority: str,
    preset_prompt: str,
    template: ArticleTemplate,
    date_value: date,
    x_usage_policy: str,
    x_failed: bool,
    mcp_failed: bool,
) -> str:
    sections = []
    for section in template.sections:
        sections.append(
            f"- {section.heading} (required={section.required}): {section.intent} | {section.guidance}"
        )
    sections_text = "\n".join(sections)

    disclaimers = []
    if x_failed:
        disclaimers.append(
            "Note: X bookmarks could not be retrieved in this run; mention this in the article."
        )
    if mcp_failed:
        disclaimers.append(
            "Note: Some web research failed; mention uncertainty where needed."
        )

    return "\n".join(
        [
            f"Language: {language}",
            "You are the Writer agent.",
            f"Date: {date_value.isoformat()}",
            "Source priorities:",
            source_priority.strip(),
            "X usage policy:",
            x_usage_policy.strip() or "(X disabled)",
            "Template sections:",
            sections_text.strip(),
            *disclaimers,
            "Write a Markdown article that follows the template.",
            "Include a references section with URLs.",
            "Return only Markdown.",
            "Preset prompt:",
            preset_prompt.strip(),
        ]
    )
