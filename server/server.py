"""MCP entrypoint — wires Vault methods as tools.

Run with `uv run python -m server.server`, or point an MCP client (Claude Desktop/Code) at
this module over stdio. Vault root defaults to the repo root but can be overridden via
MYPHD_TRACKER_ROOT for testing against a scratch vault.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from server.storage import Vault

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
VAULT_ROOT = Path(os.environ.get("MYPHD_TRACKER_ROOT", DEFAULT_ROOT))

mcp = FastMCP("myphd-tracker")
vault = Vault(VAULT_ROOT)


@mcp.tool()
def start_research(topic: str, aim: str, background: str = "") -> dict:
    """Start a new research topic page under research/. Use when brainstorming a new idea
    from scratch, before any code exists."""
    return vault.start_research(topic, aim, background)


@mcp.tool()
def log_brainstorm(topic_ref: str, note: str) -> dict:
    """Append a dated brainstorming note to an existing research topic (by slug, title, or
    alias)."""
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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
