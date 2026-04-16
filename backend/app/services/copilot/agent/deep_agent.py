"""Deep Agent - Main Agent + Sub Agent multi-agent architecture.

Uses deepagents create_deep_agent to create the main Agent.
Main Agent: intent recognition, planning, delegation, reflection, dynamic adjustment.
Sub Agents: search/collect (researcher), analysis/reports (analyst), system management (manager).

Design doc: docs/MULTI_AGENT_REDESIGN_V2.md
"""

import logging
from dataclasses import dataclass

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware.permissions import FilesystemPermission
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore

logger = logging.getLogger(__name__)

# --- Context Schema (user isolation) ---


@dataclass
class Context:
    user_id: str = "default"
    session_id: str = ""


def _user_namespace(runtime) -> tuple[str, ...]:
    """StoreBackend namespace factory - isolate by user_id."""
    try:
        user_id = runtime.context.user_id
    except Exception:
        user_id = "default"
    return (user_id, "memories")


# --- System Prompt ---

MAIN_AGENT_SYSTEM_PROMPT = """You are a ResearchPilot intelligent research assistant. Users will ask you research questions, analysis requests, or system operations.

## Core workflow

### Intent recognition
- Understand what the user really wants
- Judge task type: search research / knowledge base analysis / system management / mixed
- Judge complexity: simple (1 step) / medium (2-3 steps) / complex (4+ steps)
- If intent is unclear, ask the user directly, do not guess

### Planning
- Simple tasks (<=2 steps): delegate directly to Sub Agent, no write_todos needed
- Medium/complex tasks: use write_todos to create todo list first
- Planning principles:
  * Don't split if 1 Sub Agent can handle it
  * Parallelize independent tasks (call multiple task in one output)
  * Serialize dependent tasks
  * Plans can be adjusted anytime, don't stick to one path

### Delegation
- Use task tool to delegate to Sub Agents
- Call multiple task in one output for parallel execution
- Give Sub Agents clear descriptions: task goal + context + expected output format
- For large context, write_file first then reference the path in description

### Reflection
- After receiving Sub Agent results, think:
  * Is the information sufficient? Can you answer the user's question?
  * Do you need supplementary search or direction adjustment?
  * What should be the next step?
- Don't blindly continue the original plan, adjust based on actual situation

### Dynamic adjustment
- Insufficient info -> add todo + delegate supplementary search
- Wrong direction -> modify subsequent todos + change approach
- Sufficient info -> mark complete + synthesize results for user

### Synthesize results
- Single Sub Agent result: summarize directly
- Multiple Sub Agent results: comprehensive summary
- Report-type results: inform user that report has been generated

## Sub Agent descriptions
- researcher: Internet search and collection. Multi-round search, returns compressed summary or file reference.
- analyst: Local knowledge base analysis and report generation.
- manager: System CRUD operations, fast execution.

## Long-term memory
- If you need to recall user preferences or historical research conclusions, use recall_memory tool
- If you find information worth preserving long-term (user preferences, important conclusions), use save_memory tool

## Important principles
- Simple questions: answer directly, don't over-plan
- Complex questions: plan first then execute, don't skip planning
- Each round: only do what's most needed right now
- Sub Agent intermediate processes: you don't need to care, just look at final results
- Don't repeat searches for content already searched

You must respond in Chinese (Simplified). Be concise and professional. Never introduce yourself.
"""


# --- Sub Agent Prompts ---

RESEARCHER_PROMPT = """You are a professional internet researcher.

Workflow:
1. After receiving a search task, search for key information first
2. Evaluate search results: do they sufficiently answer the task? Are sources reliable?
3. If not enough, search with different keywords (max 3 rounds)
4. If you need to save materials, use ingest_url to collect into a topic

Output rules:
- Small results (<2000 chars): output summary directly in final reply
- Large results (>=2000 chars): use write_file to save to /results/ directory, mention file path and key findings in reply
- Always cite information sources
- Don't output raw web noise, only keep key findings and data

Rules:
1. Never introduce yourself, execute tasks directly
2. Only do search and collection, no analysis
3. When collecting articles to topics, first use list_topics to find topic ID, then use collect_topic
4. If search returns no results, state clearly
5. Reply in Chinese, concise and professional"""

ANALYST_PROMPT = """You are a professional data analyst and report writer.

Workflow:
1. After receiving an analysis task, first use rag_query to query the local knowledge base
2. Combine context information from task description for analysis
3. If you need to generate a report, use generate_report

Output rules:
- Analysis results: output directly with clear viewpoints and supporting evidence
- Reports: use generate_report to generate, mention in reply that report has been generated
- If there are file references in context, use read_file first then analyze

Rules:
1. Never introduce yourself, execute tasks directly
2. First use rag_query to query knowledge base, request supplementary info if not enough
3. Provide key findings when generating reports
4. Return analysis conclusions, don't repeat raw data
5. Reply in Chinese, concise and professional"""

MANAGER_PROMPT = """You are a system administrator. Efficiently execute user-requested CRUD operations. Complete in single execution, no multi-round reflection needed. Return operation results after completion.

Rules:
1. Never introduce yourself, execute tasks directly
2. Execute batch operations step by step
3. Return operation results
4. Reply in Chinese, concise and professional"""


# --- Tool Loading ---


def _get_researcher_tools():
    """Get researcher's tool set."""
    from app.services.copilot.tools.search import TOOLS as search_tools
    from app.services.copilot.tools.topic import TOOLS as topic_tools
    tools = search_tools + [t for t in topic_tools if t.name == "list_topics"]
    return tools


def _get_analyst_tools():
    """Get analyst's tool set."""
    from app.services.copilot.tools.rag import TOOLS as rag_tools
    from app.services.copilot.tools.report import TOOLS as report_tools
    return rag_tools + report_tools


def _get_manager_tools():
    """Get manager's tool set."""
    from app.services.copilot.tools.topic import TOOLS as topic_tools
    from app.services.copilot.tools.article import TOOLS as article_tools
    from app.services.copilot.tools.knowledge_base import TOOLS as kb_tools
    from app.services.copilot.tools.system import TOOLS as system_tools
    from app.services.copilot.tools.context import TOOLS as context_tools
    return topic_tools + article_tools + kb_tools + system_tools + context_tools


# --- Sub Agent Specs ---


def _build_subagents():
    """Build Sub Agent configuration list."""
    return [
        {
            "name": "researcher",
            "description": (
                "Deep internet search and collection expert. Responsible for searching internet info, "
                "collecting web pages, saving to topics. "
                "Use for: internet research, latest news search, web content collection. "
                "Not for: local knowledge base queries, report generation, system management."
            ),
            "system_prompt": RESEARCHER_PROMPT,
            "tools": _get_researcher_tools(),
        },
        {
            "name": "analyst",
            "description": (
                "Knowledge base analysis and report generation expert. Responsible for querying local "
                "knowledge base, analyzing data, generating research reports. "
                "Use for: local knowledge base queries, data analysis, structured report generation. "
                "Not for: internet search, system management."
            ),
            "system_prompt": ANALYST_PROMPT,
            "tools": _get_analyst_tools(),
        },
        {
            "name": "manager",
            "description": (
                "System management and resource operation expert. Responsible for topic CRUD, article CRUD, "
                "knowledge base management, system status queries, document uploads. "
                "Use for: system resource management, CRUD operations, status queries. "
                "Not for: internet search, data analysis, report generation."
            ),
            "system_prompt": MANAGER_PROMPT,
            "tools": _get_manager_tools(),
        },
    ]


# --- Persistent Infrastructure ---

_checkpointer = MemorySaver()
_store = InMemoryStore()

# --- Deep Agent Singleton ---

_deep_agent = None


def get_deep_agent():
    """Get Deep Agent instance (singleton)."""
    global _deep_agent
    if _deep_agent is not None:
        return _deep_agent

    from app.services.copilot.llm import get_chat_llm
    from app.services.copilot.agent.memory_tools import recall_memory, save_memory
    from app.services.copilot.agent.middleware import (
        SubAgentResilienceMiddleware,
        SessionArchiveMiddleware,
    )

    # create_deep_agent needs ChatOpenAI instance or model identifier string
    # get_chat_llm() returns RunnableWithFallbacks, extract .runnable for primary
    llm_chain = get_chat_llm()
    if hasattr(llm_chain, "runnable"):
        llm = llm_chain.runnable  # ChatOpenAI primary
    else:
        llm = llm_chain  # Direct ChatOpenAI

    try:
        _deep_agent = create_deep_agent(
            model=llm,
            tools=[recall_memory, save_memory],
            system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
            subagents=_build_subagents(),
            # Virtual filesystem: ephemeral + persistent
            backend=CompositeBackend(
                default=StateBackend(),
                routes={
                    "/memories/": StoreBackend(namespace=_user_namespace),
                },
            ),
            store=_store,
            # Key memory (loaded every conversation)
            memory=["/memories/AGENTS.md"],
            # Human approval: sensitive operations need confirmation
            interrupt_on={
                "generate_report": True,
                "delete_topic": True,
            },
            # File permissions
            permissions=[
                FilesystemPermission(
                    operations=["write"],
                    paths=["/system/**"],
                    mode="deny",
                ),
            ],
            checkpointer=_checkpointer,
            # Custom middleware
            middleware=[
                SubAgentResilienceMiddleware(),
                SessionArchiveMiddleware(),
            ],
            # Runtime context (user isolation)
            context_schema=Context,
        )

        logger.info("Deep Agent created with %d subagents", 3)
    except Exception as e:
        logger.error("Failed to create Deep Agent: %s", e, exc_info=True)
        raise

    return _deep_agent


def reset_deep_agent():
    """Reset Deep Agent singleton."""
    global _deep_agent
    _deep_agent = None
