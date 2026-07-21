"""MCP entrypoint — wires Vault methods as tools.

Run with `uv run python -m server.server`, or point an MCP client (Claude Desktop/Code) at
this module over stdio. Vault root defaults to the repo root but can be overridden via
MYPHD_TRACKER_ROOT for testing against a scratch vault.
"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Optional

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
        "track_research_topic to create the durable journal entry BEFORE doing anything else — "
        "including before running any separate literature-search/deep-research pass. Logging the "
        "topic here and actually researching it are two different, non-exclusive actions: do "
        "both if the user wants both, but always log first. CRITICALLY: whenever you finish "
        "actually investigating a tracked topic — a deep-research pass, web search, reading a "
        "paper, or any other research work — you MUST summarize the key findings and call "
        "log_research_note on that topic before you finish responding. Do not let findings live "
        "only in the chat transcript. A tracker that only records 'I started thinking about X' "
        "and never what was learned has no value — the findings are the entire point. Likewise, "
        "once code is being written for an idea, call start_experiment, and log every subsequent "
        "run via update_experiment "
        "whether it succeeds or fails. When the user wants to resume prior work ('let's work on "
        "research A'), call get_context first. When the user asks for a weekly summary/status "
        "update, or what they got done recently, call weekly_progress — it also inspects git "
        "history in any linked code repo, so it can surface real coding activity even if the "
        "researcher forgot to log an update_experiment call for it. When the user wants to "
        "visually browse their research rather than read it in chat, call build_dashboard and "
        "point them at dashboard/index.html — it's a fully offline static site, no server needed."
    ),
)
vault = Vault(VAULT_ROOT)


@mcp.tool(name="track_research_topic")
def start_research(topic: str, aim: str, background: str = "") -> dict:
    """Create a tracked journal entry for a new research idea/topic (NOT a literature search —
    this records that you're starting to think about a topic so it can be resumed later; it
    performs no research itself). Call this whenever the user begins, brainstorms, or wants to
    track a new research topic, even if their phrasing sounds like a request to go research it
    ('I want to do research on X', 'let's look into X') — that phrasing usually means both track
    it AND (optionally, separately) investigate it. Always call this one first regardless. If you
    do go on to investigate, call log_research_note afterward with a summary of what you found —
    otherwise those findings are lost the moment the chat ends."""
    return vault.start_research(topic, aim, background)


@mcp.tool(name="log_research_note")
def log_brainstorm(topic_ref: str, note: str) -> dict:
    """Append a dated note to an existing research topic (by slug, title, or alias) — this is
    THE mechanism for persisting anything learned about a topic: brainstormed ideas, AND
    (critically) findings/summaries from any research or investigation you just performed
    (deep-research pass, web search, reading a paper). Call this every time you finish
    investigating a tracked topic, summarizing what you found — otherwise the findings only
    exist in the chat transcript and the tracker has recorded nothing of value. get_context
    surfaces the most recent notes logged here when resuming work on a topic."""
    return vault.log_brainstorm(topic_ref, note)


@mcp.tool()
def start_experiment(research_ref: str, title: str, aim: str, setup: str) -> dict:
    """Start a new experiment page under experiments/, linked to a research topic. Use when
    the user moves from brainstorming to actually writing/running code."""
    return vault.start_experiment(research_ref, title, aim, setup)


@mcp.tool()
def update_experiment(
    experiment_ref: str,
    status: Optional[str] = None,
    setup_delta: Optional[str] = None,
    attempt_notes: Optional[str] = None,
    metrics: Optional[list[dict]] = None,
) -> dict:
    """Record a new attempt on an existing experiment. Append-only: never edits a prior
    attempt. Pass attempt_notes and/or metrics (list of {name, value, split?, attempt}) to log
    a new attempt; pass status alone to change state without logging an attempt (e.g. marking
    it blocked). Call this every time code is run, whether it succeeds or fails — most
    experiments fail before they work, and that history is the point."""
    return vault.update_experiment(experiment_ref, status, setup_delta, attempt_notes, metrics)


@mcp.tool()
def link_code(
    experiment_ref: str,
    repo_path: str,
    commit_sha: Optional[str] = None,
    remote: Optional[str] = None,
    entrypoint: Optional[str] = None,
    dirty: bool = False,
) -> dict:
    """Point an experiment at the external code repo that produced it. Code is never copied
    into the vault, only referenced by path/commit."""
    return vault.link_code(experiment_ref, repo_path, commit_sha, remote, entrypoint, dirty)


@mcp.tool()
def add_resource(
    citekey: str,
    title: str,
    authors: Optional[list[str]] = None,
    path_or_url: Optional[str] = None,
    tags: Optional[list[str]] = None,
    annotation: str = "",
) -> dict:
    """Add a paper/resource to the bibliography under resources/."""
    return vault.add_resource(citekey, title, authors, path_or_url, tags, annotation)


@mcp.tool()
def annotate_resource(resource_ref: str, note: str) -> dict:
    """Append a dated annotation to an existing resource (by citekey, title, or alias)."""
    return vault.annotate_resource(resource_ref, note)


@mcp.tool()
def get_context(ref: str) -> str:
    """Resolve a research topic, experiment, or resource by slug/title/alias (e.g. 'research
    A') and return a compiled markdown context bundle: aim/background, linked experiments with
    status and current best, linked resources, and recent activity. Call this first whenever
    the user asks to resume or continue existing work."""
    return vault.get_context(ref)


@mcp.tool()
def weekly_progress(since: Optional[str] = None, until: Optional[str] = None) -> str:
    """Generate a weekly progress digest and persist it under progress/. Defaults to the last
    7 days ending today; pass since/until as ISO dates ('YYYY-MM-DD') for a different range.
    Pulls together everything that happened in the tracker (research brainstorming, experiment
    attempts, resource additions) AND raw git commit activity in any repo linked via link_code —
    so coding work shows up here even if the researcher never called update_experiment for every
    run. Flags any linked repo with commits this week that weren't reflected in a logged attempt,
    so worked-on-but-undocumented progress doesn't get silently lost. Call this whenever the user
    asks for a weekly summary/status update, or wants to see what they got done."""
    since_date = dt.date.fromisoformat(since) if since else None
    until_date = dt.date.fromisoformat(until) if until else None
    return vault.weekly_progress(since_date, until_date)


@mcp.tool()
def build_dashboard() -> dict:
    """Regenerate the static HTML dashboard under dashboard/ from the current vault state — an
    overview of all research topics with status, a per-topic experiment timeline (status,
    attempt count, current best, a metric-trend sparkline), and a bibliography page. Fully
    offline: no server or network needed, just open dashboard/index.html in a browser. This is
    a deterministic rebuild from the vault's markdown/frontmatter, never LLM-authored — call it
    whenever the user wants to visually browse their research/experiments rather than read
    get_context in chat."""
    written = render_dashboard(VAULT_ROOT)
    return {"files_written": [str(p.relative_to(VAULT_ROOT)) for p in written]}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
