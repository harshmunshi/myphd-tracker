"""Read-only, client-pulled MCP resources — the third primitive alongside tools (actions) and
prompts (composition).

Namespaced under `myphd://` to avoid confusion with the vault's own "Resource" model (the
bibliography/citation concept in models.py) — these are the unrelated *MCP protocol* resource
primitive. `get_context` stays available as a tool (reliably callable by an LLM in an agentic
loop regardless of client) — `myphd://topics/{topic_id}` is purely additive for clients that
support pulling resources directly, not a replacement.
"""

from __future__ import annotations

from server.app import mcp, vault
from server.models import ResearchTopic, load_bucket


@mcp.resource("myphd://topics")
def list_topics() -> list[dict]:
    """Every tracked research topic — id, title, status, aliases. Read this (or the
    start_or_resume_research prompt) before creating a new topic, to check whether it already
    exists regardless of what's been discussed in the current chat session."""
    topics = load_bucket(vault.root / "research", ResearchTopic)
    return [{"id": t.id, "title": t.title, "status": t.status, "aliases": t.aliases} for t, _ in topics]


@mcp.resource("myphd://topics/{topic_id}")
def topic_context(topic_id: str) -> str:
    """Compiled context bundle for one research topic — the same content as the get_context
    tool, exposed as a directly readable resource for clients that support pulling resources.
    """
    return vault.get_context(topic_id)
