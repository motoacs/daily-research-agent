from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass(frozen=True)
class BookmarkPost:
    id: str
    url: str
    text: str
    author_username: str
    author_name: str
    created_at: str
    referenced_posts: List["BookmarkPost"] = field(default_factory=list)


@dataclass(frozen=True)
class Source:
    url: str
    title: str
    publisher: Optional[str] = None
    published_at: Optional[str] = None
    snippet: Optional[str] = None


@dataclass(frozen=True)
class Finding:
    claim: str
    evidence: str
    sources: List[Source]
    confidence: str


@dataclass(frozen=True)
class ArticleSection:
    heading: str
    body: str


@dataclass(frozen=True)
class Article:
    title: str
    dek: Optional[str]
    sections: List[ArticleSection]
    references: List[Source]


@dataclass(frozen=True)
class RunMetadata:
    run_id: str
    preset: str
    date: str
    started_at: datetime
    finished_at: Optional[datetime] = None
