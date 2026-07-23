import asyncio
from pathlib import Path

import pytest


@pytest.fixture
def mcp_env(tmp_path: Path, monkeypatch):
    for bucket in ("research", "experiments", "resources", "progress"):
        (tmp_path / bucket).mkdir()
    (tmp_path / "log.md").write_text("")
    monkeypatch.setenv("MYPHD_TRACKER_ROOT", str(tmp_path))

    import importlib

    import server.app as app_module
    import server.prompts as prompts_module
    import server.resources as resources_module
    import server.server as server_module
    import server.tools as tools_module

    # Reload in dependency order: app.py first (fresh mcp/vault bound to the new tmp_path),
    # then the modules that register @mcp.tool()/@mcp.prompt()/@mcp.resource() against it,
    # then server.py last. Python doesn't cascade-reload already-imported submodules, so
    # reloading server_module alone would leave tools/prompts/resources registered against
    # the STALE mcp/vault from a previous test — this order is what gives test isolation.
    for module in (
        app_module,
        tools_module,
        prompts_module,
        resources_module,
        server_module,
    ):
        importlib.reload(module)
    return server_module, tmp_path


def test_expected_tools_are_registered(mcp_env):
    server_module, _ = mcp_env
    tools = asyncio.run(server_module.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {
        "track_research_topic",
        "log_research_note",
        "start_experiment",
        "update_experiment",
        "link_code",
        "add_resource",
        "link_resource",
        "annotate_resource",
        "get_context",
        "weekly_progress",
        "build_dashboard",
    }


def test_track_research_topic_disambiguates_from_literature_search(mcp_env):
    server_module, _ = mcp_env
    tools = asyncio.run(server_module.mcp.list_tools())
    tool = next(t for t in tools if t.name == "track_research_topic")
    assert "NOT a literature search" in tool.description
    assert server_module.mcp.instructions is not None
    assert "not a research assistant" in server_module.mcp.instructions


def test_track_research_topic_call_creates_page(mcp_env):
    server_module, root = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "sparse attention", "aim": "sub-quadratic attention"},
        )
    )
    assert (root / "research" / "sparse-attention.md").exists()


def test_instructions_require_logging_findings_after_research(mcp_env):
    server_module, _ = mcp_env
    assert "log_research_note" in server_module.mcp.instructions
    assert "MUST summarize the key findings" in server_module.mcp.instructions


def test_log_research_note_call_persists_and_shows_in_context(mcp_env):
    server_module, root = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "sparse attention", "aim": "sub-quadratic attention"},
        )
    )
    asyncio.run(
        server_module.mcp.call_tool(
            "log_research_note",
            {
                "topic_ref": "sparse attention",
                "note": "Linformer achieves O(n) via low-rank projection.",
            },
        )
    )
    body = (root / "research" / "sparse-attention.md").read_text()
    assert "Linformer achieves O(n)" in body

    result = asyncio.run(server_module.mcp.call_tool("get_context", {"ref": "sparse attention"}))
    content_blocks, _structured = result
    assert "Linformer achieves O(n)" in content_blocks[0].text


def test_track_research_topic_result_reminds_to_log_findings(mcp_env):
    server_module, _ = mcp_env
    result = asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "sparse attention", "aim": "sub-quadratic attention"},
        )
    )
    assert "log_research_note" in result[0].text


def test_get_context_for_research_reminds_to_log_findings(mcp_env):
    server_module, _ = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "sparse attention", "aim": "sub-quadratic attention"},
        )
    )
    result = asyncio.run(server_module.mcp.call_tool("get_context", {"ref": "sparse attention"}))
    content_blocks, _structured = result
    assert "log_research_note" in content_blocks[0].text


def test_mutating_tool_calls_rebuild_dashboard_automatically(mcp_env):
    server_module, root = mcp_env
    assert not (root / "dashboard" / "index.html").exists()

    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "sparse attention", "aim": "sub-quadratic attention"},
        )
    )
    assert (root / "dashboard" / "index.html").exists()
    assert (root / "dashboard" / "topics" / "sparse-attention.html").exists()

    asyncio.run(
        server_module.mcp.call_tool(
            "add_resource",
            {"citekey": "child2019generating", "title": "Generating Long Sequences"},
        )
    )
    assert (root / "dashboard" / "resources" / "child2019generating.html").exists()


def test_weekly_progress_call_accepts_date_strings_and_persists(mcp_env):
    server_module, root = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "sparse attention", "aim": "sub-quadratic attention"},
        )
    )
    result = asyncio.run(
        server_module.mcp.call_tool("weekly_progress", {"since": "2020-01-01", "until": "2030-01-01"})
    )
    content_blocks, _structured = result
    text = content_blocks[0].text
    assert "sparse-attention" in text
    assert (root / "progress" / "2030-01-01.md").exists()


def test_expected_prompts_are_registered(mcp_env):
    server_module, _ = mcp_env
    prompts = asyncio.run(server_module.mcp.list_prompts())
    names = {p.name for p in prompts}
    assert names == {"start_or_resume_research", "log_code_run", "wrap_up_session"}


def test_start_or_resume_research_prompt_finds_no_match_on_empty_vault(mcp_env):
    server_module, _ = mcp_env
    result = asyncio.run(
        server_module.mcp.get_prompt("start_or_resume_research", {"topic": "sparse attention"})
    )
    text = result.messages[0].content.text
    assert "track_research_topic" in text
    assert "No existing research topic matches" in text


def test_start_or_resume_research_prompt_surfaces_existing_match(mcp_env):
    server_module, _ = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "Sparse Attention", "aim": "sub-quadratic attention"},
        )
    )
    result = asyncio.run(server_module.mcp.get_prompt("start_or_resume_research", {"topic": "sparse attn"}))
    text = result.messages[0].content.text
    assert "sparse-attention" in text
    assert "get_context" in text


def test_log_code_run_prompt_distinguishes_new_vs_existing_experiment(mcp_env):
    server_module, _ = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "Sparse Attention", "aim": "sub-quadratic attention"},
        )
    )
    no_exp_result = asyncio.run(
        server_module.mcp.get_prompt("log_code_run", {"research_ref": "sparse attention"})
    )
    no_exp_text = no_exp_result.messages[0].content.text
    assert "start_experiment" in no_exp_text
    assert "log_research_note" in no_exp_text  # explicitly told NOT to use it

    asyncio.run(
        server_module.mcp.call_tool(
            "start_experiment",
            {
                "research_ref": "sparse attention",
                "title": "Baseline",
                "aim": "...",
                "setup": "...",
            },
        )
    )
    has_exp_result = asyncio.run(
        server_module.mcp.get_prompt("log_code_run", {"research_ref": "sparse attention"})
    )
    has_exp_text = has_exp_result.messages[0].content.text
    assert "Baseline" in has_exp_text
    assert "update_experiment" in has_exp_text


def test_wrap_up_session_prompt_reports_clean_state_and_flags(mcp_env):
    server_module, _ = mcp_env
    clean_result = asyncio.run(server_module.mcp.get_prompt("wrap_up_session", {}))
    assert "Nothing outstanding" in clean_result.messages[0].content.text

    asyncio.run(
        server_module.mcp.call_tool("track_research_topic", {"topic": "Sparse Attention", "aim": "..."})
    )
    asyncio.run(
        server_module.mcp.call_tool(
            "start_experiment",
            {
                "research_ref": "sparse attention",
                "title": "Baseline",
                "aim": "...",
                "setup": "...",
            },
        )
    )
    asyncio.run(
        server_module.mcp.call_tool(
            "update_experiment",
            {
                "experiment_ref": "Baseline",
                "status": "blocked",
                "attempt_notes": "stuck",
            },
        )
    )

    flagged_result = asyncio.run(server_module.mcp.get_prompt("wrap_up_session", {}))
    flagged_text = flagged_result.messages[0].content.text
    assert "Blocked experiments" in flagged_text
    assert "Baseline" in flagged_text


def test_expected_resources_are_registered(mcp_env):
    server_module, _ = mcp_env
    resources = asyncio.run(server_module.mcp.list_resources())
    templates = asyncio.run(server_module.mcp.list_resource_templates())
    assert {str(r.uri) for r in resources} == {"myphd://topics"}
    assert {t.uriTemplate for t in templates} == {"myphd://topics/{topic_id}"}


def test_topics_resource_lists_tracked_topics(mcp_env):
    server_module, _ = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "Sparse Attention", "aim": "sub-quadratic attention"},
        )
    )
    contents = asyncio.run(server_module.mcp.read_resource("myphd://topics"))
    assert "sparse-attention" in contents[0].content


def test_topic_context_resource_matches_get_context_tool(mcp_env):
    server_module, _ = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic",
            {"topic": "Sparse Attention", "aim": "sub-quadratic attention"},
        )
    )
    tool_content_blocks, _structured = asyncio.run(
        server_module.mcp.call_tool("get_context", {"ref": "sparse attention"})
    )
    tool_text = tool_content_blocks[0].text

    resource_contents = asyncio.run(server_module.mcp.read_resource("myphd://topics/sparse-attention"))
    assert resource_contents[0].content == tool_text
