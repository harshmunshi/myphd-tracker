"""Composed, multi-step workflows exposed as MCP prompts.

A prompt is NOT a way to execute tool calls server-side — per the installed `mcp` SDK, a
prompt function just returns text (or messages) that FastMCP inserts into the conversation for
the calling LLM to then act on by choosing which tools to call. Every prompt below reads the
vault directly (via `vault`, read-only) to ground its instructions in actual current state
rather than conversation history — that's what lets "does this already exist" work correctly
regardless of which chat session raised the question. Contrast with tools.py: tools are the
atomic actions a prompt's instructions tell the LLM to call.
"""

from __future__ import annotations

from server.app import mcp, vault


@mcp.prompt()
def start_or_resume_research(topic: str) -> str:
    """Before creating a new research topic, check whether one already exists (by title/alias/
    fuzzy match) so a duplicate never gets created just because this is a fresh chat session.
    """
    candidates = vault.find_similar_topics(topic)
    if not candidates:
        return (
            f"No existing research topic matches '{topic}'. Call track_research_topic to "
            f"create a new one, with an aim summarizing what to investigate."
        )
    lines = [f"'{topic}' may already be tracked. Candidate existing topic(s), best match first:"]
    for c in candidates:
        lines.append(f"- {c['title']} (`{c['id']}`, status={c['status']}, match={c['score']})")
    lines.append(
        "If one of these is clearly the same idea, call get_context on it to resume instead of "
        "creating a duplicate with track_research_topic. Only call track_research_topic if this "
        "is genuinely a different idea from all of the above."
    )
    return "\n".join(lines)


@mcp.prompt()
def log_code_run(research_ref: str) -> str:
    """Use this whenever code was just written or run for a tracked research topic — code
    activity defaults to an experiment attempt, never a log_research_note prose entry.
    """
    experiments = vault.list_experiments_for_topic(research_ref)
    if not experiments:
        return (
            f"No experiment exists yet for '{research_ref}'. Since code was just written/run, "
            f"call start_experiment(research_ref='{research_ref}', ...) and then update_experiment "
            f"with this run's status/notes/metrics. Code activity belongs under experiments/, "
            f"never logged via log_research_note — that's for prose findings, not code runs."
        )
    lines = [f"Existing experiment(s) for '{research_ref}':"]
    for e in experiments:
        lines.append(
            f"- {e['title']} (`{e['id']}`, status={e['status']}, latest_attempt={e['latest_attempt']})"
        )
    lines.append(
        "Since code was just run, call update_experiment on whichever of these it continues, "
        "with this run's status/notes/metrics. Only call start_experiment if this is genuinely a "
        "new experiment line, not a continuation of one above. Do not log this as a "
        "log_research_note — that's for prose findings, not code runs."
    )
    return "\n".join(lines)


@mcp.prompt()
def wrap_up_session() -> str:
    """Use at the end of a working session to check for anything left blocked, unverified, or
    undocumented before ending — a checklist, not an automatic fix."""
    flags = vault.session_flags()
    if not any(flags.values()):
        return "Nothing outstanding: no blocked experiments, no unverified backfills, no unlinked resources."

    lines = ["Before ending this session, double check the following:"]
    if flags["blocked"]:
        lines.append("\nBlocked experiments (resolve or note why they're stuck):")
        lines.extend(f"- {e['title']} (`{e['id']}`)" for e in flags["blocked"])
    if flags["unverified_backfilled"]:
        lines.append("\nBackfilled experiments still unverified (confirm against a real artifact):")
        lines.extend(f"- {e['title']} (`{e['id']}`)" for e in flags["unverified_backfilled"])
    if flags["unlinked_resources"]:
        lines.append("\nResources not linked to any research topic (call link_resource if they belong):")
        lines.extend(f"- {r['title']} (`{r['citekey']}`)" for r in flags["unlinked_resources"])
    return "\n".join(lines)
