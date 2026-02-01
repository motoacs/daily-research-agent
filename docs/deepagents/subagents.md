> ## Documentation Index
> Fetch the complete documentation index at: https://docs.langchain.com/llms.txt
> Use this file to discover all available pages before exploring further.

# Subagents

> Learn how to use subagents to delegate work and keep context clean

Deep agents can create subagents to delegate work. You can specify custom subagents in the `subagents` parameter. Subagents are useful for [context quarantine](https://www.dbreunig.com/2025/06/26/how-to-fix-your-context.html#context-quarantine) (keeping the main agent's context clean) and for providing specialized instructions.

```mermaid  theme={null}
graph TB
    Main[Main Agent] --> |task tool| Sub[Subagent]

    Sub --> Research[Research]
    Sub --> Code[Code]
    Sub --> General[General]

    Research --> |isolated work| Result[Final Result]
    Code --> |isolated work| Result
    General --> |isolated work| Result

    Result --> Main
```

## Why use subagents?

Subagents solve the **context bloat problem**. When agents use tools with large outputs (web search, file reads, database queries), the context window fills up quickly with intermediate results. Subagents isolate this detailed work—the main agent receives only the final result, not the dozens of tool calls that produced it.

**When to use subagents:**

* ✅ Multi-step tasks that would clutter the main agent's context
* ✅ Specialized domains that need custom instructions or tools
* ✅ Tasks requiring different model capabilities
* ✅ When you want to keep the main agent focused on high-level coordination

**When NOT to use subagents:**

* ❌ Simple, single-step tasks
* ❌ When you need to maintain intermediate context
* ❌ When the overhead outweighs benefits

## Configuration

`subagents` should be a list of dictionaries or `CompiledSubAgent` objects. There are two types:

### SubAgent (Dictionary-based)

For most use cases, define subagents as dictionaries:

**Required fields:**

<ParamField body="name" type="str" required>
  Unique identifier for the subagent.
  The main agent uses this name when calling the `task()` tool.
  The subagent name becomes metadata for `AIMessage`s and for streaming, which helps to differentiate between agents.
</ParamField>

<ParamField body="description" type="str" required>
  What this subagent does. Be specific and action-oriented. The main agent uses this to decide when to delegate.
</ParamField>

<ParamField body="system_prompt" type="str" required>
  Instructions for the subagent. Include tool usage guidance and output format requirements.
</ParamField>

<ParamField body="tools" type="list[Callable]" required>
  Tools the subagent can use. Keep this minimal and include only what's needed.
</ParamField>

**Optional fields:**

<ParamField body="model" type="str | BaseChatModel">
  Override the main agent's model. Use the format `'provider:model-name'` (for example, `'openai:gpt-4.1'`).
</ParamField>

<ParamField body="middleware" type="list[Middleware]">
  Additional middleware for custom behavior, logging, or rate limiting.
</ParamField>

<ParamField body="interrupt_on" type="dict[str, bool]">
  Configure human-in-the-loop for specific tools. Requires a checkpointer.
</ParamField>

### CompiledSubAgent

For complex workflows, use a pre-built LangGraph graph:

<ParamField body="name" type="str" required>
  Unique identifier for the subagent.
  The subagent name becomes metadata for `AIMessage`s and for streaming, which helps to differentiate between agents.
</ParamField>

<ParamField body="description" type="str" required>
  What this subagent does.
</ParamField>

<ParamField body="runnable" type="Runnable" required>
  A compiled LangGraph graph (must call `.compile()` first).
</ParamField>

## Using SubAgent

```python  theme={null}
import os
from typing import Literal
from tavily import TavilyClient
from deepagents import create_deep_agent

tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search"""
    return tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )

research_subagent = {
    "name": "research-agent",
    "description": "Used to research more in depth questions",
    "system_prompt": "You are a great researcher",
    "tools": [internet_search],
    "model": "openai:gpt-4.1",  # Optional override, defaults to main agent model
}
subagents = [research_subagent]

agent = create_deep_agent(
    model="claude-sonnet-4-5-20250929",
    subagents=subagents
)
```

## Using CompiledSubAgent

For more complex use cases, you can provide your custom subagents.
You can create a custom subagent using LangChain's `create_agent` or by making a custom LangGraph graph using the [graph API](https://github.com/langchain-ai/docs/pull/).

If you're creating a custom LangGraph graph, make sure that the graph has a [state key called `"messages"`](/oss/python/langgraph/quickstart#2-define-state):

```python  theme={null}
from deepagents import create_deep_agent, CompiledSubAgent
from langchain.agents import create_agent

# Create a custom agent graph
custom_graph = create_agent(
    model=your_model,
    tools=specialized_tools,
    prompt="You are a specialized agent for data analysis..."
)

# Use it as a custom subagent
custom_subagent = CompiledSubAgent(
    name="data-analyzer",
    description="Specialized agent for complex data analysis tasks",
    runnable=custom_graph
)

subagents = [custom_subagent]

agent = create_deep_agent(
    model="claude-sonnet-4-5-20250929",
    tools=[internet_search],
    system_prompt=research_instructions,
    subagents=subagents
)
```

## Streaming

When streaming tracing information agents' names are available as `lc_agent_name` in metadata.
When reviewing tracing information, you can use this metadata to differentiate which agent the data came from.

The following example creates a deep agent with the name `main-agent` and a subagent with the name `research-agent`:

```python  theme={null}
import os
from typing import Literal
from tavily import TavilyClient
from deepagents import create_deep_agent

tavily_client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

def internet_search(
    query: str,
    max_results: int = 5,
    topic: Literal["general", "news", "finance"] = "general",
    include_raw_content: bool = False,
):
    """Run a web search"""
    return tavily_client.search(
        query,
        max_results=max_results,
        include_raw_content=include_raw_content,
        topic=topic,
    )

research_subagent = {
    "name": "research-agent",
    "description": "Used to research more in depth questions",
    "system_prompt": "You are a great researcher",
    "tools": [internet_search],
    "model": "claude-sonnet-4-5-20250929",  # Optional override, defaults to main agent model
}
subagents = [research_subagent]

agent = create_deep_agent(
    model="claude-sonnet-4-5-20250929",
    subagents=subagents,
    name="main-agent"
)
```

As you prompt your deepagents, all agent runs executed by a subagent or deep agent will have the agent name in their metadata.
In this case the subagent with the name `"research-agent"`, will have `{'lc_agent_name': 'research-agent'}` in any associated agent run metadata:

<img src="https://mintcdn.com/langchain-5e9cc07a/IlqYrcANJ39avG84/oss/images/deepagents/deepagents-langsmith.png?fit=max&auto=format&n=IlqYrcANJ39avG84&q=85&s=4c3a1512fb27abc30da37751aee19afd" alt="LangSmith Example trace showing the metadata" data-og-width="907" width="907" data-og-height="866" height="866" data-path="oss/images/deepagents/deepagents-langsmith.png" data-optimize="true" data-opv="3" srcset="https://mintcdn.com/langchain-5e9cc07a/IlqYrcANJ39avG84/oss/images/deepagents/deepagents-langsmith.png?w=280&fit=max&auto=format&n=IlqYrcANJ39avG84&q=85&s=5cf23d1d7aae4d3343e37b643a0ecd2d 280w, https://mintcdn.com/langchain-5e9cc07a/IlqYrcANJ39avG84/oss/images/deepagents/deepagents-langsmith.png?w=560&fit=max&auto=format&n=IlqYrcANJ39avG84&q=85&s=1fda9f540ca84404a42a0c5a88f58de8 560w, https://mintcdn.com/langchain-5e9cc07a/IlqYrcANJ39avG84/oss/images/deepagents/deepagents-langsmith.png?w=840&fit=max&auto=format&n=IlqYrcANJ39avG84&q=85&s=3cb2fe556586a7117ce22a264f2c9e2d 840w, https://mintcdn.com/langchain-5e9cc07a/IlqYrcANJ39avG84/oss/images/deepagents/deepagents-langsmith.png?w=1100&fit=max&auto=format&n=IlqYrcANJ39avG84&q=85&s=9f5dbbdf170d099d214027c4adb60aa3 1100w, https://mintcdn.com/langchain-5e9cc07a/IlqYrcANJ39avG84/oss/images/deepagents/deepagents-langsmith.png?w=1650&fit=max&auto=format&n=IlqYrcANJ39avG84&q=85&s=f52f916b003fa21997bd12dadb974a39 1650w, https://mintcdn.com/langchain-5e9cc07a/IlqYrcANJ39avG84/oss/images/deepagents/deepagents-langsmith.png?w=2500&fit=max&auto=format&n=IlqYrcANJ39avG84&q=85&s=d665dc4bfedc5abb01bafd2ac0b729c4 2500w" />

## The general-purpose subagent

In addition to any user-defined subagents, deep agents have access to a `general-purpose` subagent at all times. This subagent:

* Has the same system prompt as the main agent
* Has access to all the same tools
* Uses the same model (unless overridden)

### When to use it

The general-purpose subagent is ideal for context isolation without specialized behavior. The main agent can delegate a complex multi-step task to this subagent and get a concise result back without bloat from intermediate tool calls.

<Card title="Example">
  Instead of the main agent making 10 web searches and filling its context with results, it delegates to the general-purpose subagent: `task(name="general-purpose", task="Research quantum computing trends")`. The subagent performs all the searches internally and returns only a summary.
</Card>

## Best practices

### Write clear descriptions

The main agent uses descriptions to decide which subagent to call. Be specific:

✅ **Good:** `"Analyzes financial data and generates investment insights with confidence scores"`

❌ **Bad:** `"Does finance stuff"`

### Keep system prompts detailed

Include specific guidance on how to use tools and format outputs:

```python  theme={null}
research_subagent = {
    "name": "research-agent",
    "description": "Conducts in-depth research using web search and synthesizes findings",
    "system_prompt": """You are a thorough researcher. Your job is to:

    1. Break down the research question into searchable queries
    2. Use internet_search to find relevant information
    3. Synthesize findings into a comprehensive but concise summary
    4. Cite sources when making claims

    Output format:
    - Summary (2-3 paragraphs)
    - Key findings (bullet points)
    - Sources (with URLs)

    Keep your response under 500 words to maintain clean context.""",
    "tools": [internet_search],
}
```

### Minimize tool sets

Only give subagents the tools they need. This improves focus and security:

```python  theme={null}
# ✅ Good: Focused tool set
email_agent = {
    "name": "email-sender",
    "tools": [send_email, validate_email],  # Only email-related
}

# ❌ Bad: Too many tools
email_agent = {
    "name": "email-sender",
    "tools": [send_email, web_search, database_query, file_upload],  # Unfocused
}
```

### Choose models by task

Different models excel at different tasks:

```python  theme={null}
subagents = [
    {
        "name": "contract-reviewer",
        "description": "Reviews legal documents and contracts",
        "system_prompt": "You are an expert legal reviewer...",
        "tools": [read_document, analyze_contract],
        "model": "claude-sonnet-4-5-20250929",  # Large context for long documents
    },
    {
        "name": "financial-analyst",
        "description": "Analyzes financial data and market trends",
        "system_prompt": "You are an expert financial analyst...",
        "tools": [get_stock_price, analyze_fundamentals],
        "model": "openai:gpt-5",  # Better for numerical analysis
    },
]
```

### Return concise results

Instruct subagents to return summaries, not raw data:

```python  theme={null}
data_analyst = {
    "system_prompt": """Analyze the data and return:
    1. Key insights (3-5 bullet points)
    2. Overall confidence score
    3. Recommended next actions

    Do NOT include:
    - Raw data
    - Intermediate calculations
    - Detailed tool outputs

    Keep response under 300 words."""
}
```

## Common patterns

### Multiple specialized subagents

Create specialized subagents for different domains:

```python  theme={null}
from deepagents import create_deep_agent

subagents = [
    {
        "name": "data-collector",
        "description": "Gathers raw data from various sources",
        "system_prompt": "Collect comprehensive data on the topic",
        "tools": [web_search, api_call, database_query],
    },
    {
        "name": "data-analyzer",
        "description": "Analyzes collected data for insights",
        "system_prompt": "Analyze data and extract key insights",
        "tools": [statistical_analysis],
    },
    {
        "name": "report-writer",
        "description": "Writes polished reports from analysis",
        "system_prompt": "Create professional reports from insights",
        "tools": [format_document],
    },
]

agent = create_deep_agent(
    model="claude-sonnet-4-5-20250929",
    system_prompt="You coordinate data analysis and reporting. Use subagents for specialized tasks.",
    subagents=subagents
)
```

**Workflow:**

1. Main agent creates high-level plan
2. Delegates data collection to data-collector
3. Passes results to data-analyzer
4. Sends insights to report-writer
5. Compiles final output

Each subagent works with clean context focused only on its task.

## Troubleshooting

### Subagent not being called

**Problem**: Main agent tries to do work itself instead of delegating.

**Solutions**:

1. **Make descriptions more specific:**

   ```python  theme={null}
   # ✅ Good
   {"name": "research-specialist", "description": "Conducts in-depth research on specific topics using web search. Use when you need detailed information that requires multiple searches."}

   # ❌ Bad
   {"name": "helper", "description": "helps with stuff"}
   ```

2. **Instruct main agent to delegate:**

   ```python  theme={null}
   agent = create_deep_agent(
       system_prompt="""...your instructions...

       IMPORTANT: For complex tasks, delegate to your subagents using the task() tool.
       This keeps your context clean and improves results.""",
       subagents=[...]
   )
   ```

### Context still getting bloated

**Problem**: Context fills up despite using subagents.

**Solutions**:

1. **Instruct subagent to return concise results:**

   ```python  theme={null}
   system_prompt="""...

   IMPORTANT: Return only the essential summary.
   Do NOT include raw data, intermediate search results, or detailed tool outputs.
   Your response should be under 500 words."""
   ```

2. **Use filesystem for large data:**

   ```python  theme={null}
   system_prompt="""When you gather large amounts of data:
   1. Save raw data to /data/raw_results.txt
   2. Process and analyze the data
   3. Return only the analysis summary

   This keeps context clean."""
   ```

### Wrong subagent being selected

**Problem**: Main agent calls inappropriate subagent for the task.

**Solution**: Differentiate subagents clearly in descriptions:

```python  theme={null}
subagents = [
    {
        "name": "quick-researcher",
        "description": "For simple, quick research questions that need 1-2 searches. Use when you need basic facts or definitions.",
    },
    {
        "name": "deep-researcher",
        "description": "For complex, in-depth research requiring multiple searches, synthesis, and analysis. Use for comprehensive reports.",
    }
]
```

***

<Callout icon="pen-to-square" iconType="regular">
  [Edit this page on GitHub](https://github.com/langchain-ai/docs/edit/main/src/oss/deepagents/subagents.mdx) or [file an issue](https://github.com/langchain-ai/docs/issues/new/choose).
</Callout>

<Tip icon="terminal" iconType="regular">
  [Connect these docs](/use-these-docs) to Claude, VSCode, and more via MCP for real-time answers.
</Tip>
