"""Shared MCP server state: the FastMCP instance, the Vault, and the vault root path.

Deliberately holds no @mcp.tool()/@mcp.prompt()/@mcp.resource() definitions itself — those
live in tools.py/prompts.py/resources.py respectively, each importing `mcp`/`vault` from here
and registering against them. Keeping the shared state in its own module (rather than in
server.py, which then imports the registration modules) avoids a circular import between
server.py and the registration modules.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from server.dashboard.render import build_dashboard as render_dashboard
from server.storage import Vault

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
VAULT_ROOT = Path(os.environ.get("MYPHD_TRACKER_ROOT", DEFAULT_ROOT))

mcp = FastMCP(
    "myphd-tracker",
    instructions=(
        "This server is a lab-journal/tracker, not a research assistant — it never searches "
        "the web or reads papers itself. Whenever the user starts talking about a new research "
        "idea or topic (even phrased as 'I want to research X' or 'let's look into X'), call "
        "the start_or_resume_research PROMPT first — it checks whether this already matches an "
        "existing tracked topic (by reading the vault directly, so it works regardless of what "
        "chat session you're in) and tells you whether to call track_research_topic or resume "
        "via get_context instead of creating a duplicate. Logging the topic and actually "
        "researching it are two different, non-exclusive actions: do both if the user wants "
        "both, but always resolve/log first. CRITICALLY: whenever you finish actually "
        "investigating a tracked topic — a deep-research pass, web search, reading a paper, or "
        "any other research work — you MUST summarize the key findings and call log_research_note "
        "on that topic before you finish responding. Do not let findings live only in the chat "
        "transcript. A tracker that only records 'I started thinking about X' and never what was "
        "learned has no value — the findings are the entire point. ADDITIONALLY: any specific "
        "paper/dataset/resource surfaced during that investigation must also be recorded via "
        "add_resource with research_ref set to that topic — log_research_note alone only "
        "captures a prose summary, not a structured, citable bibliography entry. Passing "
        "research_ref is what keeps each topic's bibliography separate instead of every resource "
        "landing in one undifferentiated global list; if a resource turns out relevant to a topic "
        "after the fact, use link_resource to attach it retroactively. Likewise, once code is "
        "being written or run for a tracked idea, call the log_code_run PROMPT first — code "
        "activity defaults to an experiment (start_experiment/update_experiment), never a "
        "log_research_note prose entry, and the prompt tells you whether to start a new "
        "experiment or continue an existing one. When the user wants to resume prior work "
        "('let's work on research A'), call get_context first. When the user asks for a weekly "
        "summary/status update, or what they got done recently, call weekly_progress — it also "
        "inspects git history in any linked code repo, so it can surface real coding activity "
        "even if the researcher forgot to log an update_experiment call for it. When the user "
        "wants to visually browse their research rather than read it in chat, call build_dashboard "
        "and point them at dashboard/index.html — it's a fully offline static site, no server "
        "needed. Before ending a longer session, consider the wrap_up_session PROMPT to check for "
        "anything left blocked, unverified, or undocumented."
    ),
)
vault = Vault(VAULT_ROOT)


def _rebuild_dashboard() -> list[Path]:
    return render_dashboard(VAULT_ROOT)
