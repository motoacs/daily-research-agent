from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import subprocess

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

from daily_research_agent.artifacts.paths import RunPaths, build_run_paths, ensure_dirs, slugify
from daily_research_agent.artifacts.writer import write_json, write_text
from daily_research_agent.config import AgentConfig, LoadedPreset, openrouter_settings
from daily_research_agent.domain.models import BookmarkPost, Source
from daily_research_agent.domain.prompts import (
    build_research_prompt,
    build_writer_prompt,
    load_article_template,
)
from daily_research_agent.integrations.mcp_client import MCPResearchClient
from daily_research_agent.integrations.x_bookmarks import XBookmarksClient, XBookmarksError
from daily_research_agent.logging import get_logger
from daily_research_agent.tools.x_oauth import (
    load_token_payload,
    refresh_access_token,
    save_token_payload,
    token_file_path,
)


class OrchestratorError(RuntimeError):
    pass


def _git_sha() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    raw = text.strip()
    if raw.startswith("{") and raw.endswith("}"):
        return _safe_json_loads(raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return _safe_json_loads(raw[start : end + 1])


def _build_chat_model(model_id: str, openrouter: Dict[str, Any]) -> ChatOpenAI:
    kwargs = {
        "model": model_id,
        "api_key": openrouter.get("api_key"),
        "base_url": openrouter.get("base_url"),
    }
    headers = openrouter.get("default_headers")
    if headers:
        kwargs["default_headers"] = headers
    return ChatOpenAI(**kwargs)


def _normalize_sources(raw_sources: List[Dict[str, Any]]) -> List[Source]:
    sources = []
    for item in raw_sources:
        if not item.get("url"):
            continue
        sources.append(
            Source(
                url=item.get("url", ""),
                title=item.get("title", ""),
                publisher=item.get("publisher"),
                published_at=item.get("published_at"),
                snippet=item.get("snippet"),
            )
        )
    return sources


def _serialize_bookmarks(bookmarks: List[BookmarkPost]) -> List[Dict[str, Any]]:
    return [asdict(post) for post in bookmarks]


def _build_run_metadata(
    config: AgentConfig,
    preset: LoadedPreset,
    run_paths: RunPaths,
    article_date: date,
    x_enabled: bool,
    bookmarks_count: int,
    mcp_servers: List[str],
) -> Dict[str, Any]:
    return {
        "run_id": run_paths.run_id,
        "preset": preset.name,
        "date": article_date.isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(config.run.output_dir),
        "state_dir": str(config.run.state_dir),
        "max_web_queries": config.run.max_web_queries,
        "x_enabled": x_enabled,
        "bookmarks_count": bookmarks_count,
        "mcp_servers": mcp_servers,
        "langsmith_project": config.observability.langsmith.project,
        "git_sha": _git_sha(),
    }


async def run_orchestrator(
    config: AgentConfig,
    preset: LoadedPreset,
    article_date: date,
) -> RunPaths:
    template = load_article_template(preset.template_path)
    run_paths = build_run_paths(config.run.output_dir, article_date, None)
    ensure_dirs(run_paths)
    config.run.state_dir.mkdir(parents=True, exist_ok=True)

    logger = get_logger(run_paths.log_file, config.logging)

    x_failed = False
    mcp_failed = False

    bookmarks: List[BookmarkPost] = []
    if config.x.enabled:
        try:
            config.x.cache.path.parent.mkdir(parents=True, exist_ok=True)
            token_path = token_file_path(config.run.state_dir)
            cached_tokens = load_token_payload(token_path) or {}

            access_token = os.getenv("X_USER_ACCESS_TOKEN") or cached_tokens.get("access_token") or ""
            refresh_token = os.getenv("X_REFRESH_TOKEN") or cached_tokens.get("refresh_token")
            client_id = os.getenv("X_CLIENT_ID")
            client_secret = os.getenv("X_CLIENT_SECRET")

            def _fetch_with_token(token: str) -> List[BookmarkPost]:
                x_client = XBookmarksClient(
                    base_url=os.getenv("X_API_BASE_URL", "https://api.x.com"),
                    access_token=token,
                    cache_path=str(config.x.cache.path),
                )
                return x_client.fetch_bookmarks(
                    max_results=config.x.bookmarks_count,
                    stop_on_seen_streak=config.x.cache.stop_on_seen_streak,
                    resolve_depth=config.x.quote.resolve_depth,
                    max_cached_posts=config.x.cache.max_cached_posts,
                    enabled_cache=config.x.cache.enabled,
                )

            try:
                bookmarks = _fetch_with_token(access_token)
            except XBookmarksError as exc:
                # Try one refresh cycle when auth fails.
                if exc.status_code == 401 and refresh_token and client_id:
                    logger.warning("x_access_token_expired_try_refresh")
                    new_payload = refresh_access_token(
                        client_id=client_id,
                        refresh_token=refresh_token,
                        client_secret=client_secret,
                    )
                    save_token_payload(token_path, new_payload)
                    new_access_token = new_payload.get("access_token") or ""
                    bookmarks = _fetch_with_token(new_access_token)
                else:
                    raise
        except XBookmarksError as exc:
            x_failed = True
            logger.error("x_bookmarks_failed", error=str(exc))
    else:
        logger.info("x_bookmarks_disabled")

    write_json(run_paths.bookmarks_json, _serialize_bookmarks(bookmarks))

    mcp_tools = []
    tool_names: List[str] = []
    mcp_client = MCPResearchClient(config.mcp.servers)
    try:
        if not config.mcp.servers:
            raise OrchestratorError("No MCP servers configured")
        tools_bundle = await mcp_client.connect()
        mcp_tools = tools_bundle.tools
        tool_names = tools_bundle.tool_names
        logger.info("mcp_tools_ready", tool_names=tool_names)
    except Exception as exc:  # noqa: BLE001 - capture MCP failures
        mcp_failed = True
        logger.error("mcp_connect_failed", error=str(exc))

    run_metadata = _build_run_metadata(
        config,
        preset,
        run_paths,
        article_date,
        config.x.enabled,
        len(bookmarks),
        tool_names,
    )
    write_json(run_paths.run_json, run_metadata)

    research_prompt = build_research_prompt(
        language=config.prompts.language,
        source_priority=config.prompts.source_priority,
        preset_prompt=preset.prompt,
        daily_sites=config.sources.daily_sites,
        x_usage_policy=config.x.usage_policy,
        max_web_queries=config.run.max_web_queries,
        date_value=article_date,
    )

    openrouter = openrouter_settings()
    if not openrouter.get("api_key"):
        logger.warning("openrouter_api_key_missing")

    researcher_model = _build_chat_model(
        config.models.researcher or config.models.main, openrouter
    )

    backend = FilesystemBackend(root_dir=str(config.run.output_dir))
    researcher_agent = create_deep_agent(
        model=researcher_model,
        tools=mcp_tools,
        system_prompt=research_prompt,
        workspace=backend,
    )

    research_input = {
        "messages": [
            HumanMessage(
                content=(
                    "Use the available tools to gather sources. "
                    "Output JSON only.\n\n"
                    f"Bookmarks JSON:\n{json.dumps(_serialize_bookmarks(bookmarks), ensure_ascii=False, indent=2)}"
                )
            )
        ]
    }

    research_response = None
    if not mcp_failed:
        try:
            research_response = await researcher_agent.ainvoke(
                research_input,
                config={
                    "tags": ["research", preset.name],
                    "metadata": {
                        "run_id": run_paths.run_id,
                        "preset": preset.name,
                        "date": article_date.isoformat(),
                        "tool_names": tool_names,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            mcp_failed = True
            logger.error("research_agent_failed", error=str(exc))

    research_text = ""
    parsed = None
    if research_response:
        research_text = _extract_agent_text(research_response)
        parsed = _extract_json(research_text)
    if parsed is None:
        logger.warning("research_json_parse_failed")
        parsed = {
            "findings": [],
            "sources": [],
            "memo_markdown": research_text or "Research failed or returned no JSON.",
            "missing_info": [],
        }

    sources = _normalize_sources(parsed.get("sources", []))
    write_json(run_paths.sources_json, [asdict(source) for source in sources])
    write_text(run_paths.research_md, parsed.get("memo_markdown", ""))

    writer_prompt = build_writer_prompt(
        language=config.prompts.language,
        source_priority=config.prompts.source_priority,
        preset_prompt=preset.prompt,
        template=template,
        date_value=article_date,
        x_usage_policy=config.x.usage_policy,
        x_failed=x_failed,
        mcp_failed=mcp_failed,
    )

    writer_model = _build_chat_model(config.models.writer, openrouter)
    writer_agent = create_deep_agent(
        model=writer_model,
        tools=[],
        system_prompt=writer_prompt,
        workspace=backend,
    )

    writer_input = {
        "messages": [
            HumanMessage(
                content=(
                    "Use the research findings below to write the article.\n\n"
                    f"Findings JSON:\n{json.dumps(parsed, ensure_ascii=False, indent=2)}\n\n"
                    f"Bookmarks JSON:\n{json.dumps(_serialize_bookmarks(bookmarks), ensure_ascii=False, indent=2)}\n"
                )
            )
        ]
    }

    article_markdown = ""
    try:
        writer_response = await writer_agent.ainvoke(
            writer_input,
            config={
                "tags": ["writer", preset.name],
                "metadata": {
                    "run_id": run_paths.run_id,
                    "preset": preset.name,
                    "date": article_date.isoformat(),
                },
            },
        )
        article_markdown = _extract_agent_text(writer_response)
    except Exception as exc:  # noqa: BLE001
        logger.error("writer_agent_failed", error=str(exc))
        raise OrchestratorError("Writer agent failed") from exc

    article_title = _extract_title(article_markdown)
    slug = slugify(article_title or "daily-research")
    article_path = run_paths.article_dir / f"{slug}.md"
    write_text(article_path, article_markdown)

    run_metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    run_metadata["article_path"] = str(article_path)
    run_metadata["x_failed"] = x_failed
    run_metadata["mcp_failed"] = mcp_failed
    write_json(run_paths.run_json, run_metadata)

    if mcp_client is not None:
        await mcp_client.close()

    logger.info("run_completed", article_path=str(article_path))
    return run_paths


def _extract_agent_text(response: Any) -> str:
    if isinstance(response, dict):
        messages = response.get("messages")
        if messages:
            return messages[-1].content
    if hasattr(response, "content"):
        return response.content
    return str(response)


def _extract_title(markdown: str) -> Optional[str]:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None
