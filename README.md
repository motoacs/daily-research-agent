# daily-research-agent

Local daily research agent that gathers sources via MCP and X bookmarks, then writes a Markdown article.

## Quick start

```bash
uv run daily-research-agent run --preset daily_ai_news --date 2026-02-01
```

Config lives in `configs/agent.toml`. Secrets (OpenRouter, X, LangSmith) go in `.env`.
