from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import subprocess

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from daily_research_agent.artifacts.paths import RunPaths, build_run_paths, ensure_dirs, slugify
from daily_research_agent.artifacts.writer import write_json, write_text
from daily_research_agent.audit import AuditCallbackHandler, AuditConfig, AuditLogger
from daily_research_agent.config import (
    AgentConfig,
    LoadedPreset,
    PromptRegistryEntry,
    openrouter_settings,
)
from daily_research_agent.domain.models import BookmarkPost
from daily_research_agent.domain.prompts import load_article_template
from daily_research_agent.integrations.mcp_client import MCPResearchClient
from daily_research_agent.integrations.x_bookmarks import (
    XBookmarksClient,
    XBookmarksError,
    load_cached_bookmarks,
)
from daily_research_agent.logging import get_logger
from daily_research_agent.prompting import (
    ArtifactPathGroup,
    _make_namespace,
    render_prompt_with_dotted,
)
from daily_research_agent.tool_limits import ToolLimitCallbackHandler
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


def _build_chat_model(model_id: str, openrouter: Dict[str, Any]) -> ChatOpenAI:
    max_tokens_env = os.getenv("OPENROUTER_MAX_TOKENS")
    max_tokens = int(max_tokens_env) if max_tokens_env else 4096
    kwargs = {
        "model": model_id,
        "api_key": openrouter.get("api_key"),
        "base_url": openrouter.get("base_url"),
        "max_tokens": max_tokens,
    }
    headers = openrouter.get("default_headers")
    if headers:
        kwargs["default_headers"] = headers
    return ChatOpenAI(**kwargs)


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


def _configure_langsmith(config: AgentConfig, run_id: str) -> None:
    if config.observability.langsmith.enabled:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = config.observability.langsmith.project
        os.environ.setdefault("LANGSMITH_RUN_ID", run_id)
    else:
        os.environ["LANGSMITH_TRACING"] = "false"


def _build_audit_logger(config: AgentConfig, run_paths: RunPaths) -> tuple[AuditLogger, AuditConfig]:
    audit_cfg = config.logging.audit
    path = Path(audit_cfg.path.format(run_id=run_paths.run_id))
    logger = AuditLogger(path=path, enabled=audit_cfg.enabled)
    return logger, AuditConfig(
        enabled=audit_cfg.enabled,
        path_template=audit_cfg.path,
        log_llm_events=audit_cfg.log_llm_events,
        log_tool_events=audit_cfg.log_tool_events,
        redaction=audit_cfg.redaction,
    )


def _artifact_virtual_paths() -> ArtifactPathGroup:
    return ArtifactPathGroup(
        inputs=_make_namespace(
            {
                "bookmarks_json": "/artifacts/inputs/bookmarks.json",
                "template_toml": "/artifacts/inputs/template.toml",
                "run_json": "/artifacts/inputs/run.json",
            }
        ),
        research=_make_namespace(
            {
                "findings_json": "/artifacts/research/findings.json",
                "sources_json": "/artifacts/research/sources.json",
                "memo_md": "/artifacts/research/memo.md",
            }
        ),
        draft=_make_namespace(
            {
                "article_md": "/artifacts/draft/article.md",
            }
        ),
        final=_make_namespace(
            {
                "article_md": "/artifacts/final/article.md",
            }
        ),
        run=_make_namespace(
            {
                "diagnostics_md": "/artifacts/run/diagnostics.md",
            }
        ),
    )


def _render_prompt(
    registry: Dict[str, PromptRegistryEntry],
    prompt_id: str,
    context: Dict[str, Any],
) -> str:
    if prompt_id not in registry:
        raise OrchestratorError(f"Prompt id not found in registry: {prompt_id}")
    return render_prompt_with_dotted(registry[prompt_id], context)


def _filter_tools(
    tools: List[Any],
    allow: List[str],
    deny: List[str],
) -> List[Any]:
    if not allow:
        allowed_names: set[str] = set()
    else:
        allowed_names = set()
        for tool in tools:
            for pattern in allow:
                if pattern == "*" or tool.name == pattern:
                    allowed_names.add(tool.name)
    if deny:
        for tool in tools:
            for pattern in deny:
                if pattern == "*" or tool.name == pattern:
                    allowed_names.discard(tool.name)
    return [tool for tool in tools if tool.name in allowed_names]


def _copy_if_exists(source: Path, dest: Path) -> None:
    if source.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _extract_title(markdown: str) -> Optional[str]:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def _validate_article(article_text: str) -> List[str]:
    issues = []
    if "http://" not in article_text and "https://" not in article_text:
        issues.append("No URLs found in article markdown (references may be missing).")
    return issues


async def run_orchestrator(
    config: AgentConfig,
    preset: LoadedPreset,
    article_date: date,
) -> RunPaths:
    load_article_template(preset.template_path)
    run_time = datetime.now(ZoneInfo(config.run.timezone))
    run_paths = build_run_paths(config.run.output_dir, article_date, None, run_time)
    ensure_dirs(run_paths)
    config.run.state_dir.mkdir(parents=True, exist_ok=True)

    logger = get_logger(run_paths.log_file, config.logging)
    audit_logger, audit_config = _build_audit_logger(config, run_paths)
    _configure_langsmith(config, run_paths.run_id)

    audit_logger.event(
        "run_started",
        {
            "run_id": run_paths.run_id,
            "preset": preset.name,
            "date": article_date.isoformat(),
            "git_sha": _git_sha(),
        },
    )

    x_failed = False
    mcp_failed = False

    bookmarks: List[BookmarkPost] = []
    if config.x.enabled:
        audit_logger.event("x_fetch_started", {"run_id": run_paths.run_id})
        try:
            config.x.cache.path.parent.mkdir(parents=True, exist_ok=True)
            token_path = token_file_path(config.run.state_dir)
            cached_tokens = load_token_payload(token_path) or {}

            access_token = (
                os.getenv("X_USER_ACCESS_TOKEN")
                or cached_tokens.get("access_token")
                or ""
            )
            refresh_token = os.getenv("X_REFRESH_TOKEN") or cached_tokens.get(
                "refresh_token"
            )
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
            if not bookmarks and config.x.cache.enabled:
                bookmarks = load_cached_bookmarks(
                    str(config.x.cache.path),
                    config.x.bookmarks_count,
                )
                if bookmarks:
                    logger.info("x_bookmarks_loaded_from_cache", {"count": len(bookmarks)})
        except XBookmarksError as exc:
            x_failed = True
            logger.error("x_bookmarks_failed", {"error": str(exc)})
            if config.x.cache.enabled:
                bookmarks = load_cached_bookmarks(
                    str(config.x.cache.path),
                    config.x.bookmarks_count,
                )
                if bookmarks:
                    logger.info("x_bookmarks_loaded_from_cache", {"count": len(bookmarks)})
        finally:
            audit_logger.event(
                "x_fetch_finished",
                {
                    "run_id": run_paths.run_id,
                    "count": len(bookmarks),
                    "failed": x_failed,
                },
            )
    else:
        logger.info("x_bookmarks_disabled")

    write_json(run_paths.bookmarks_json, _serialize_bookmarks(bookmarks))
    write_json(run_paths.artifacts.inputs.bookmarks_json, _serialize_bookmarks(bookmarks))
    write_text(run_paths.artifacts.inputs.template_toml, preset.template_path.read_text(encoding="utf-8"))

    mcp_tools: List[Any] = []
    tool_names: List[str] = []
    mcp_client = MCPResearchClient(config.mcp.servers, config.mcp.env_allowlist)
    audit_logger.event("mcp_connect_started", {"run_id": run_paths.run_id})
    try:
        if not config.mcp.servers:
            raise OrchestratorError("No MCP servers configured")
        tools_bundle = await mcp_client.connect()
        mcp_tools = tools_bundle.tools
        tool_names = tools_bundle.tool_names
        logger.info("mcp_tools_ready", {"tool_names": tool_names})
    except Exception as exc:  # noqa: BLE001
        mcp_failed = True
        logger.error("mcp_connect_failed", {"error": str(exc)})
    finally:
        audit_logger.event(
            "mcp_connect_finished",
            {
                "run_id": run_paths.run_id,
                "tool_names": tool_names,
                "failed": mcp_failed,
            },
        )

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

    run_input = {
        "run_id": run_paths.run_id,
        "preset": preset.name,
        "date": article_date.isoformat(),
        "timezone": config.run.timezone,
        "max_web_queries": config.run.max_web_queries,
        "x_enabled": config.x.enabled,
        "x_failed": x_failed,
        "mcp_failed": mcp_failed,
    }
    write_json(run_paths.artifacts.inputs.run_json, run_input)

    openrouter = openrouter_settings()
    if not openrouter.get("api_key"):
        logger.warning("openrouter_api_key_missing")

    artifact_paths = _artifact_virtual_paths()
    prompt_context = {
        "date": article_date.isoformat(),
        "timezone": config.run.timezone,
        "preset_name": preset.name,
        "preset_prompt": preset.prompt,
        "max_web_queries": config.run.max_web_queries,
        "language": config.prompts.language,
        "source_priority": config.prompts.source_priority.strip(),
        "x_usage_policy": config.x.usage_policy.strip(),
        "daily_sites": "\n".join(f"- {site}" for site in config.sources.daily_sites),
        "artifact_paths": artifact_paths,
        "run_id": run_paths.run_id,
        "x_failed": x_failed,
        "mcp_failed": mcp_failed,
    }

    supervisor_cfg = config.agents.supervisor
    supervisor_prompt = _render_prompt(
        config.prompts.registry,
        supervisor_cfg.prompt_id,
        prompt_context,
    )
    supervisor_user_prompt = None
    if supervisor_cfg.user_prompt_id:
        supervisor_user_prompt = _render_prompt(
            config.prompts.registry,
            supervisor_cfg.user_prompt_id,
            prompt_context,
        )

    supervisor_model = _build_chat_model(supervisor_cfg.model, openrouter)

    supervisor_tools = _filter_tools(
        mcp_tools,
        supervisor_cfg.tools.allow,
        supervisor_cfg.tools.deny,
    )

    subagents = []
    for name in supervisor_cfg.subagents:
        if name not in config.agents.subagents:
            raise OrchestratorError(f"Subagent not defined: {name}")
        sub_def = config.agents.subagents[name]
        sub_prompt = _render_prompt(
            config.prompts.registry,
            sub_def.prompt_id,
            prompt_context,
        )
        sub_tools = _filter_tools(
            mcp_tools,
            sub_def.tools.allow,
            sub_def.tools.deny,
        )
        subagents.append(
            {
                "name": sub_def.name,
                "description": sub_def.description,
                "system_prompt": sub_prompt,
                "tools": sub_tools,
                "model": _build_chat_model(sub_def.model, openrouter),
            }
        )

    backend = lambda rt: CompositeBackend(
        default=StateBackend(rt),
        routes={
            "/artifacts/": FilesystemBackend(
                root_dir=str(run_paths.run_dir),
                virtual_mode=True,
            )
        },
    )

    checkpointer = None
    if config.deepagents.interrupt_on:
        checkpointer = MemorySaver()

    skills_dirs = []
    skill_files: Dict[str, str] = {}
    skill_ids = set(supervisor_cfg.skills)
    for sub_def in config.agents.subagents.values():
        skill_ids.update(sub_def.skills)
    if skill_ids:
        skills_dirs.append("/skills/")
        for skill_id in sorted(skill_ids):
            entry = config.prompts.registry.get(skill_id)
            if not entry:
                raise OrchestratorError(f"Skill prompt not found: {skill_id}")
            skill_files[f"/skills/{skill_id}/SKILL.md"] = render_prompt_with_dotted(
                entry, prompt_context
            )

    supervisor_agent = create_deep_agent(
        model=supervisor_model,
        tools=supervisor_tools,
        system_prompt=supervisor_prompt,
        subagents=subagents,
        backend=backend,
        skills=skills_dirs or None,
        interrupt_on=config.deepagents.interrupt_on or None,
        checkpointer=checkpointer,
    )

    callbacks = [AuditCallbackHandler(audit_logger, audit_config)]
    if config.run.max_web_queries > 0 and tool_names:
        callbacks.append(
            ToolLimitCallbackHandler(
                max_calls=config.run.max_web_queries,
                tool_names=set(tool_names),
                audit_logger=audit_logger,
            )
        )

    supervisor_input = {
        "messages": [
            HumanMessage(
                content=supervisor_user_prompt
                or "Proceed with the run using the provided artifacts."
            )
        ]
    }
    if skill_files:
        supervisor_input["files"] = skill_files

    diagnostics: List[str] = []
    try:
        audit_logger.event(
            "agent_invoke_started",
            {"run_id": run_paths.run_id, "agent": "supervisor"},
        )
        await supervisor_agent.ainvoke(
            supervisor_input,
            config={
                "tags": ["supervisor", preset.name],
                "metadata": {
                    "run_id": run_paths.run_id,
                    "preset": preset.name,
                    "date": article_date.isoformat(),
                    "tool_names": tool_names,
                },
                "callbacks": callbacks,
                "configurable": {"thread_id": run_paths.run_id},
            },
        )
        audit_logger.event(
            "agent_invoke_finished",
            {"run_id": run_paths.run_id, "agent": "supervisor"},
        )
    except Exception as exc:  # noqa: BLE001
        diagnostics.append(f"Supervisor failed: {exc}")
        audit_logger.event(
            "agent_invoke_failed",
            {"run_id": run_paths.run_id, "agent": "supervisor", "error": str(exc)},
        )

    article_markdown = ""
    if run_paths.artifacts.final.article_md.exists():
        article_markdown = run_paths.artifacts.final.article_md.read_text(encoding="utf-8")
    else:
        diagnostics.append("Final article not found at /artifacts/final/article.md")

    if article_markdown:
        diagnostics.extend(_validate_article(article_markdown))

    if diagnostics:
        write_text(run_paths.artifacts.run.diagnostics_md, "\n".join(diagnostics))

    if not article_markdown:
        audit_logger.event(
            "run_finished",
            {
                "run_id": run_paths.run_id,
                "article_path": None,
                "diagnostics": len(diagnostics),
                "success": False,
            },
        )
        if mcp_client is not None:
            await mcp_client.close()
        raise OrchestratorError("Supervisor did not produce a final article")

    article_title = _extract_title(article_markdown)
    slug = slugify(article_title or "daily-research")
    article_path = run_paths.article_dir / f"{slug}-{run_paths.run_suffix}.md"
    write_text(article_path, article_markdown)

    _copy_if_exists(run_paths.artifacts.research.memo_md, run_paths.research_md)
    _copy_if_exists(run_paths.artifacts.research.sources_json, run_paths.sources_json)

    run_metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    run_metadata["article_path"] = str(article_path)
    run_metadata["x_failed"] = x_failed
    run_metadata["mcp_failed"] = mcp_failed
    write_json(run_paths.run_json, run_metadata)

    if mcp_client is not None:
        await mcp_client.close()

    audit_logger.event(
        "run_finished",
        {
            "run_id": run_paths.run_id,
            "article_path": str(article_path),
            "diagnostics": len(diagnostics),
            "success": True,
        },
    )
    logger.info("run_completed", {"article_path": str(article_path)})
    return run_paths


def _extract_agent_text(response: Any) -> str:
    if isinstance(response, dict):
        messages = response.get("messages")
        if messages:
            return messages[-1].content
    if hasattr(response, "content"):
        return response.content
    return str(response)


async def main_async(config: AgentConfig, preset: LoadedPreset, article_date: date) -> RunPaths:
    return await run_orchestrator(config, preset, article_date)


def main(config: AgentConfig, preset: LoadedPreset, article_date: date) -> RunPaths:
    return asyncio.run(main_async(config, preset, article_date))
