from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3
from typing import Dict, Iterable, List, Optional, Tuple

import httpx

from daily_research_agent.domain.models import BookmarkPost


class XBookmarksError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bookmarks (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            text TEXT NOT NULL,
            author_username TEXT NOT NULL,
            author_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            referenced_posts TEXT,
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bookmarks_fetched_at ON bookmarks(fetched_at)"
    )
    conn.commit()


def _get_cached_ids(conn: sqlite3.Connection, ids: Iterable[str]) -> set[str]:
    id_list = list(ids)
    if not id_list:
        return set()
    placeholders = ",".join("?" for _ in id_list)
    rows = conn.execute(
        f"SELECT id FROM bookmarks WHERE id IN ({placeholders})", id_list
    ).fetchall()
    return {row[0] for row in rows}


def _insert_bookmark(conn: sqlite3.Connection, post: BookmarkPost) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO bookmarks (
            id, url, text, author_username, author_name, created_at, referenced_posts, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            post.id,
            post.url,
            post.text,
            post.author_username,
            post.author_name,
            post.created_at,
            json.dumps([asdict(p) for p in post.referenced_posts], ensure_ascii=False),
            _utc_now_iso(),
        ),
    )


def _cleanup_cache(conn: sqlite3.Connection, max_cached_posts: int) -> None:
    if max_cached_posts <= 0:
        return
    count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
    if count <= max_cached_posts:
        return
    to_delete = count - max_cached_posts
    conn.execute(
        """
        DELETE FROM bookmarks WHERE id IN (
            SELECT id FROM bookmarks ORDER BY fetched_at ASC LIMIT ?
        )
        """,
        (to_delete,),
    )
    conn.commit()


def _build_post_url(username: str, post_id: str) -> str:
    return f"https://x.com/{username}/status/{post_id}"


def _index_includes(payload: Dict) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    includes = payload.get("includes", {})
    users = {user["id"]: user for user in includes.get("users", [])}
    tweets = {tweet["id"]: tweet for tweet in includes.get("tweets", [])}
    return users, tweets


def _parse_post(tweet: Dict, users: Dict[str, Dict], referenced_posts: List[BookmarkPost]) -> BookmarkPost:
    author = users.get(tweet.get("author_id"), {})
    username = author.get("username", "unknown")
    name = author.get("name", "unknown")
    return BookmarkPost(
        id=tweet["id"],
        url=_build_post_url(username, tweet["id"]),
        text=tweet.get("text", ""),
        author_username=username,
        author_name=name,
        created_at=tweet.get("created_at", ""),
        referenced_posts=referenced_posts,
    )


def _collect_referenced(
    tweet: Dict, tweets: Dict[str, Dict], users: Dict[str, Dict], resolve_depth: int
) -> List[BookmarkPost]:
    if resolve_depth <= 0:
        return []
    referenced = []
    for ref in tweet.get("referenced_tweets", []) or []:
        if ref.get("type") != "quoted":
            continue
        ref_tweet = tweets.get(ref.get("id"))
        if not ref_tweet:
            continue
        referenced.append(_parse_post(ref_tweet, users, []))
    return referenced


class XBookmarksClient:
    def __init__(self, base_url: str, access_token: str, cache_path: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._cache_path = cache_path

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base_url, headers=self._headers(), timeout=30.0)

    def fetch_bookmarks(
        self,
        max_results: int,
        stop_on_seen_streak: int,
        resolve_depth: int,
        max_cached_posts: int,
        enabled_cache: bool,
    ) -> List[BookmarkPost]:
        if not self._access_token:
            raise XBookmarksError("X_USER_ACCESS_TOKEN is not set")

        conn = sqlite3.connect(self._cache_path)
        _init_db(conn)

        new_posts: List[BookmarkPost] = []
        seen_streak = 0

        with self._client() as client:
            user_id = self._get_user_id(client)
            next_token: Optional[str] = None

            while len(new_posts) < max_results:
                payload = self._get_bookmarks_page(client, user_id, max_results, next_token)
                data = payload.get("data", [])
                if not data:
                    break
                users, tweets = _index_includes(payload)
                ids = [tweet["id"] for tweet in data]
                cached_ids = _get_cached_ids(conn, ids) if enabled_cache else set()

                for tweet in data:
                    if tweet["id"] in cached_ids:
                        seen_streak += 1
                        if seen_streak >= stop_on_seen_streak:
                            break
                        continue

                    referenced = _collect_referenced(tweet, tweets, users, resolve_depth)
                    post = _parse_post(tweet, users, referenced)
                    new_posts.append(post)
                    seen_streak = 0
                    if enabled_cache:
                        _insert_bookmark(conn, post)
                    if len(new_posts) >= max_results:
                        break

                if seen_streak >= stop_on_seen_streak:
                    break

                next_token = payload.get("meta", {}).get("next_token")
                if not next_token:
                    break

        if enabled_cache:
            _cleanup_cache(conn, max_cached_posts)
        conn.commit()
        conn.close()
        return new_posts

    def _get_user_id(self, client: httpx.Client) -> str:
        resp = client.get("/2/users/me")
        if resp.status_code >= 400:
            raise XBookmarksError(
                f"X API /2/users/me failed: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
            )
        payload = resp.json()
        return payload.get("data", {}).get("id")

    def _get_bookmarks_page(
        self, client: httpx.Client, user_id: str, max_results: int, pagination_token: Optional[str]
    ) -> Dict:
        params = {
            "max_results": min(max_results, 100),
            "expansions": "author_id,referenced_tweets.id,referenced_tweets.id.author_id",
            "tweet.fields": "created_at,referenced_tweets,author_id",
            "user.fields": "name,username",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        resp = client.get(f"/2/users/{user_id}/bookmarks", params=params)
        if resp.status_code >= 400:
            raise XBookmarksError(
                f"X API /2/users/{user_id}/bookmarks failed: {resp.status_code} {resp.text}",
                status_code=resp.status_code,
            )
        return resp.json()
