from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import uuid


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    run_dir: Path
    articles_dir: Path
    article_dir: Path
    run_json: Path
    research_md: Path
    sources_json: Path
    bookmarks_json: Path
    log_file: Path


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = _SLUG_RE.sub("-", slug).strip("-")
    return slug or "article"


def build_run_paths(output_dir: Path, article_date: date, title: str | None) -> RunPaths:
    run_id = f"{article_date.isoformat()}-{uuid.uuid4().hex[:8]}"
    run_dir = output_dir / "runs" / run_id
    articles_dir = output_dir / "articles"
    article_dir = articles_dir / article_date.isoformat()
    slug = slugify(title or "daily-research")

    return RunPaths(
        run_id=run_id,
        run_dir=run_dir,
        articles_dir=articles_dir,
        article_dir=article_dir,
        run_json=run_dir / "run.json",
        research_md=run_dir / "research.md",
        sources_json=run_dir / "sources.json",
        bookmarks_json=run_dir / "bookmarks.json",
        log_file=run_dir / "app.log",
    )


def ensure_dirs(paths: RunPaths) -> None:
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.articles_dir.mkdir(parents=True, exist_ok=True)
    paths.article_dir.mkdir(parents=True, exist_ok=True)
