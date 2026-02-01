from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
import uuid


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    run_suffix: str
    run_dir: Path
    articles_dir: Path
    article_dir: Path
    artifacts_dir: Path
    artifacts: "ArtifactPaths"
    run_json: Path
    research_md: Path
    sources_json: Path
    bookmarks_json: Path
    log_file: Path


@dataclass(frozen=True)
class ArtifactInputs:
    dir: Path
    bookmarks_json: Path
    template_toml: Path
    run_json: Path


@dataclass(frozen=True)
class ArtifactResearch:
    dir: Path
    findings_json: Path
    sources_json: Path
    memo_md: Path


@dataclass(frozen=True)
class ArtifactDraft:
    dir: Path
    article_md: Path


@dataclass(frozen=True)
class ArtifactFinal:
    dir: Path
    article_md: Path


@dataclass(frozen=True)
class ArtifactRun:
    dir: Path
    diagnostics_md: Path


@dataclass(frozen=True)
class ArtifactPaths:
    root: Path
    inputs: ArtifactInputs
    research: ArtifactResearch
    draft: ArtifactDraft
    final: ArtifactFinal
    run: ArtifactRun


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    slug = value.strip().lower()
    slug = _SLUG_RE.sub("-", slug).strip("-")
    return slug or "article"


def build_run_paths(
    output_dir: Path,
    article_date: date,
    title: str | None,
    run_time: datetime | None = None,
) -> RunPaths:
    run_time = run_time or datetime.now()
    run_stamp = run_time.strftime("%H%M%S")
    short_id = uuid.uuid4().hex[:8]
    run_suffix = f"{run_stamp}-{short_id}"
    run_id = f"{article_date.isoformat()}-{run_suffix}"
    run_dir = output_dir / "runs" / run_id
    articles_dir = output_dir / "articles"
    article_dir = articles_dir / article_date.isoformat()
    slug = slugify(title or "daily-research")
    artifacts_dir = run_dir / "artifacts"

    inputs_dir = artifacts_dir / "inputs"
    research_dir = artifacts_dir / "research"
    draft_dir = artifacts_dir / "draft"
    final_dir = artifacts_dir / "final"
    run_artifacts_dir = artifacts_dir / "run"

    artifacts = ArtifactPaths(
        root=artifacts_dir,
        inputs=ArtifactInputs(
            dir=inputs_dir,
            bookmarks_json=inputs_dir / "bookmarks.json",
            template_toml=inputs_dir / "template.toml",
            run_json=inputs_dir / "run.json",
        ),
        research=ArtifactResearch(
            dir=research_dir,
            findings_json=research_dir / "findings.json",
            sources_json=research_dir / "sources.json",
            memo_md=research_dir / "memo.md",
        ),
        draft=ArtifactDraft(
            dir=draft_dir,
            article_md=draft_dir / "article.md",
        ),
        final=ArtifactFinal(
            dir=final_dir,
            article_md=final_dir / "article.md",
        ),
        run=ArtifactRun(
            dir=run_artifacts_dir,
            diagnostics_md=run_artifacts_dir / "diagnostics.md",
        ),
    )

    return RunPaths(
        run_id=run_id,
        run_suffix=run_suffix,
        run_dir=run_dir,
        articles_dir=articles_dir,
        article_dir=article_dir,
        artifacts_dir=artifacts_dir,
        artifacts=artifacts,
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
    paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts.inputs.dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts.research.dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts.draft.dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts.final.dir.mkdir(parents=True, exist_ok=True)
    paths.artifacts.run.dir.mkdir(parents=True, exist_ok=True)
