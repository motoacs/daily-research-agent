from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Dict, List, Optional

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_core.tools import BaseTool

from daily_research_agent.config import MCPServerConfig


@dataclass
class MCPTools:
    tools: List[BaseTool]
    tool_names: List[str]


def _server_to_config(server: MCPServerConfig) -> dict:
    if server.transport == "http":
        return {
            "transport": "http",
            "url": server.url,
        }
    if server.transport == "stdio":
        config = {
            "transport": "stdio",
            "command": server.command,
            "args": server.args or [],
        }
        base_env = dict(os.environ)
        if server.env:
            base_env.update(server.env)
        config["env"] = base_env
        return config
    raise ValueError(f"Unsupported MCP transport: {server.transport}")


class MCPResearchClient:
    def __init__(self, servers: List[MCPServerConfig]) -> None:
        self._servers = servers
        self._client: Optional[MultiServerMCPClient] = None
        self._tools: List[BaseTool] = []

    async def connect(self) -> MCPTools:
        server_configs = {s.name: _server_to_config(s) for s in self._servers}
        self._client = MultiServerMCPClient(server_configs)
        self._tools = await self._client.get_tools()
        tool_names = [tool.name for tool in self._tools]
        return MCPTools(tools=self._tools, tool_names=tool_names)

    async def close(self) -> None:
        if self._client is not None:
            close_fn = getattr(self._client, "close", None)
            if callable(close_fn):
                await close_fn()
            self._client = None
            self._tools = []
