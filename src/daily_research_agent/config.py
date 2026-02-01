from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import tomllib


@dataclass(frozen=True)
class RunSettings:
    output_dir: Path
    timezone: str
    max_web_queries: int
    include_run_artifacts: bool
    state_dir: Path


@dataclass(frozen=True)
class ModelsConfig:
    main: str
    writer: str
    researcher: Optional[str] = None
    verifier: Optional[str] = None




@dataclass(frozen=True)
class PresetConfig:
    template: Path
    prompt_id: str


@dataclass(frozen=True)
class SourcesConfig:
    daily_sites: List[str]


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    format: str
    to_stdout: bool
    to_file: bool
    audit: "AuditLoggingConfig"


@dataclass(frozen=True)
class AuditLoggingConfig:
    enabled: bool
    path: str
    log_llm_events: bool
    log_tool_events: bool
    redaction: str


@dataclass(frozen=True)
class XCacheConfig:
    enabled: bool
    path: Path
    stop_on_seen_streak: int
    max_cached_posts: int


@dataclass(frozen=True)
class XQuoteConfig:
    resolve_depth: int


@dataclass(frozen=True)
class XConfig:
    enabled: bool
    bookmarks_count: int
    usage_policy: str
    cache: XCacheConfig
    quote: XQuoteConfig


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class MCPConfig:
    servers: List[MCPServerConfig]
    env_allowlist: List[str]


@dataclass(frozen=True)
class LangSmithConfig:
    enabled: bool
    project: str


@dataclass(frozen=True)
class ObservabilityConfig:
    langsmith: LangSmithConfig


@dataclass(frozen=True)
class AgentConfig:
    run: RunSettings
    models: ModelsConfig
    prompts: PromptsConfig
    presets: Dict[str, PresetConfig]
    sources: SourcesConfig
    logging: LoggingConfig
    x: XConfig
    mcp: MCPConfig
    observability: ObservabilityConfig
    deepagents: "DeepAgentsConfig"
    agents: "AgentsConfig"


@dataclass(frozen=True)
class PromptRegistryEntry:
    text: str


@dataclass(frozen=True)
class PromptsConfig:
    language: str
    source_priority: str
    presets: Dict[str, Dict[str, str]]
    registry: Dict[str, PromptRegistryEntry]


@dataclass(frozen=True)
class LoadedPreset:
    name: str
    prompt: str
    template_path: Path


@dataclass(frozen=True)
class AgentToolPolicy:
    allow: List[str]
    deny: List[str]


@dataclass(frozen=True)
class AgentDefinition:
    model: str
    prompt_id: str
    description: Optional[str] = None
    tools: AgentToolPolicy = field(default_factory=lambda: AgentToolPolicy(allow=[], deny=[]))
    skills: List[str] = field(default_factory=list)
    subagents: List[str] = field(default_factory=list)
    user_prompt_id: Optional[str] = None


@dataclass(frozen=True)
class SubAgentDefinition:
    name: str
    description: str
    model: str
    prompt_id: str
    tools: AgentToolPolicy
    skills: List[str]


@dataclass(frozen=True)
class AgentsConfig:
    supervisor: AgentDefinition
    subagents: Dict[str, SubAgentDefinition]


@dataclass(frozen=True)
class DeepAgentsConfig:
    tool_token_limit_before_evict: Optional[int]
    interrupt_on: Dict[str, bool]


class ConfigError(RuntimeError):
    pass


def _require(value: Any, path: str) -> Any:
    if value is None:
        raise ConfigError(f"Missing required config value: {path}")
    return value


def _to_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


def _resolve_path(path: Path, base_dir: Path) -> Path:
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _parse_mcp_servers(raw: List[Dict[str, Any]]) -> List[MCPServerConfig]:
    servers: List[MCPServerConfig] = []
    for item in raw:
        servers.append(
            MCPServerConfig(
                name=_require(item.get("name"), "mcp.servers[].name"),
                transport=_require(item.get("transport"), "mcp.servers[].transport"),
                url=item.get("url"),
                command=item.get("command"),
                args=item.get("args"),
                env=item.get("env"),
            )
        )
    return servers


def _parse_tool_policy(raw: Dict[str, Any]) -> AgentToolPolicy:
    return AgentToolPolicy(
        allow=list(raw.get("allow", [])),
        deny=list(raw.get("deny", [])),
    )


def _parse_agent_definition(raw: Dict[str, Any], path: str) -> AgentDefinition:
    tools = _parse_tool_policy(raw.get("tools", {}))
    return AgentDefinition(
        model=_require(raw.get("model"), f"{path}.model"),
        prompt_id=_require(raw.get("prompt_id"), f"{path}.prompt_id"),
        description=raw.get("description"),
        tools=tools,
        skills=list(raw.get("skills", [])),
        subagents=list(raw.get("subagents", [])),
        user_prompt_id=raw.get("user_prompt_id"),
    )


def _parse_subagents(raw: Dict[str, Any]) -> Dict[str, SubAgentDefinition]:
    subagents: Dict[str, SubAgentDefinition] = {}
    for name, payload in raw.items():
        tools = _parse_tool_policy(payload.get("tools", {}))
        subagents[name] = SubAgentDefinition(
            name=name,
            description=_require(payload.get("description"), f"agents.subagents.{name}.description"),
            model=_require(payload.get("model"), f"agents.subagents.{name}.model"),
            prompt_id=_require(payload.get("prompt_id"), f"agents.subagents.{name}.prompt_id"),
            tools=tools,
            skills=list(payload.get("skills", [])),
        )
    return subagents


def load_config(path: str | Path) -> AgentConfig:
    config_path = _to_path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
    base_dir = config_path.parent

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    run = data.get("run", {})
    run_settings = RunSettings(
        output_dir=_resolve_path(_to_path(run.get("output_dir", "./outputs")), base_dir),
        timezone=run.get("timezone", "UTC"),
        max_web_queries=int(run.get("max_web_queries", 20)),
        include_run_artifacts=bool(run.get("include_run_artifacts", True)),
        state_dir=_resolve_path(_to_path(run.get("state_dir", "./state")), base_dir),
    )

    models = data.get("models", {})
    models_config = ModelsConfig(
        main=_require(models.get("main"), "models.main"),
        writer=_require(models.get("writer"), "models.writer"),
        researcher=models.get("researcher"),
        verifier=models.get("verifier"),
    )

    prompts = data.get("prompts", {})
    presets_prompts = prompts.get("presets", {})
    raw_registry = prompts.get("registry", {})
    registry: Dict[str, PromptRegistryEntry] = {}
    for key, value in raw_registry.items():
        if isinstance(value, dict):
            text = value.get("text")
        else:
            text = value
        registry[key] = PromptRegistryEntry(
            text=_require(text, f"prompts.registry.{key}.text")
        )
    prompts_config = PromptsConfig(
        language=prompts.get("language", "en"),
        source_priority=prompts.get("source_priority", ""),
        presets=presets_prompts,
        registry=registry,
    )

    raw_presets = data.get("presets", {})
    presets_config: Dict[str, PresetConfig] = {}
    for name, preset in raw_presets.items():
        presets_config[name] = PresetConfig(
            template=_resolve_path(
                _to_path(_require(preset.get("template"), f"presets.{name}.template")),
                base_dir,
            ),
            prompt_id=_require(preset.get("prompt_id"), f"presets.{name}.prompt_id"),
        )

    sources = data.get("sources", {})
    sources_config = SourcesConfig(daily_sites=list(sources.get("daily_sites", [])))

    logging_cfg = data.get("logging", {})
    audit_cfg = logging_cfg.get("audit", {})
    logging_config = LoggingConfig(
        level=logging_cfg.get("level", "INFO"),
        format=logging_cfg.get("format", "json"),
        to_stdout=bool(logging_cfg.get("to_stdout", True)),
        to_file=bool(logging_cfg.get("to_file", True)),
        audit=AuditLoggingConfig(
            enabled=bool(audit_cfg.get("enabled", False)),
            path=audit_cfg.get("path", "../outputs/runs/{run_id}/audit.jsonl"),
            log_llm_events=bool(audit_cfg.get("log_llm_events", True)),
            log_tool_events=bool(audit_cfg.get("log_tool_events", True)),
            redaction=audit_cfg.get("redaction", "strict"),
        ),
    )

    x_cfg = data.get("x", {})
    x_cache_cfg = x_cfg.get("cache", {})
    x_quote_cfg = x_cfg.get("quote", {})
    x_config = XConfig(
        enabled=bool(x_cfg.get("enabled", False)),
        bookmarks_count=int(x_cfg.get("bookmarks_count", 0)),
        usage_policy=x_cfg.get("usage_policy", ""),
        cache=XCacheConfig(
            enabled=bool(x_cache_cfg.get("enabled", True)),
            path=_resolve_path(
                _to_path(x_cache_cfg.get("path", "./state/x_bookmarks_cache.sqlite")),
                base_dir,
            ),
            stop_on_seen_streak=int(x_cache_cfg.get("stop_on_seen_streak", 20)),
            max_cached_posts=int(x_cache_cfg.get("max_cached_posts", 20000)),
        ),
        quote=XQuoteConfig(resolve_depth=int(x_quote_cfg.get("resolve_depth", 0))),
    )

    mcp_cfg = data.get("mcp", {})
    mcp_config = MCPConfig(
        servers=_parse_mcp_servers(mcp_cfg.get("servers", [])),
        env_allowlist=list(mcp_cfg.get("env_allowlist", [])),
    )

    langsmith_cfg = data.get("observability", {}).get("langsmith", {})
    observability_config = ObservabilityConfig(
        langsmith=LangSmithConfig(
            enabled=bool(langsmith_cfg.get("enabled", False)),
            project=langsmith_cfg.get("project", "daily-research-agent"),
        )
    )

    deep_cfg = data.get("deepagents", {})
    deepagents_config = DeepAgentsConfig(
        tool_token_limit_before_evict=deep_cfg.get("tool_token_limit_before_evict"),
        interrupt_on=dict(deep_cfg.get("interrupt_on", {})),
    )

    agents_cfg = data.get("agents", {})
    supervisor_cfg = agents_cfg.get("supervisor", {})
    if not supervisor_cfg:
        raise ConfigError("Missing required config: agents.supervisor")
    subagents_cfg = _parse_subagents(agents_cfg.get("subagents", {}))
    agents_config = AgentsConfig(
        supervisor=_parse_agent_definition(supervisor_cfg, "agents.supervisor"),
        subagents=subagents_cfg,
    )

    return AgentConfig(
        run=run_settings,
        models=models_config,
        prompts=prompts_config,
        presets=presets_config,
        sources=sources_config,
        logging=logging_config,
        x=x_config,
        mcp=mcp_config,
        observability=observability_config,
        deepagents=deepagents_config,
        agents=agents_config,
    )


def resolve_preset(config: AgentConfig, preset_name: str, today: date) -> LoadedPreset:
    if preset_name not in config.presets:
        raise ConfigError(f"Preset not found: {preset_name}")

    preset = config.presets[preset_name]
    prompt_block = config.prompts.presets.get(preset.prompt_id)
    if not prompt_block:
        raise ConfigError(f"Prompt id not found: {preset.prompt_id}")

    prompt = prompt_block.get("prompt")
    if not prompt:
        raise ConfigError(f"Prompt text missing for: {preset.prompt_id}")

    prompt = prompt.format(date=today.isoformat())

    return LoadedPreset(name=preset_name, prompt=prompt, template_path=preset.template)


def openrouter_settings() -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    headers = {}
    referer = os.getenv("OPENROUTER_HTTP_REFERER")
    title = os.getenv("OPENROUTER_X_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return {
        "api_key": api_key,
        "base_url": base_url,
        "default_headers": headers or None,
    }
