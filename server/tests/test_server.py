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
    import server.server as server_module

    importlib.reload(server_module)
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
    result = asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic", {"topic": "sparse attention", "aim": "sub-quadratic attention"}
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
            "track_research_topic", {"topic": "sparse attention", "aim": "sub-quadratic attention"}
        )
    )
    asyncio.run(
        server_module.mcp.call_tool(
            "log_research_note",
            {"topic_ref": "sparse attention", "note": "Linformer achieves O(n) via low-rank projection."},
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
            "track_research_topic", {"topic": "sparse attention", "aim": "sub-quadratic attention"}
        )
    )
    assert "log_research_note" in result[0].text


def test_get_context_for_research_reminds_to_log_findings(mcp_env):
    server_module, _ = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic", {"topic": "sparse attention", "aim": "sub-quadratic attention"}
        )
    )
    result = asyncio.run(server_module.mcp.call_tool("get_context", {"ref": "sparse attention"}))
    content_blocks, _structured = result
    assert "log_research_note" in content_blocks[0].text


def test_weekly_progress_call_accepts_date_strings_and_persists(mcp_env):
    server_module, root = mcp_env
    asyncio.run(
        server_module.mcp.call_tool(
            "track_research_topic", {"topic": "sparse attention", "aim": "sub-quadratic attention"}
        )
    )
    result = asyncio.run(
        server_module.mcp.call_tool("weekly_progress", {"since": "2020-01-01", "until": "2030-01-01"})
    )
    content_blocks, _structured = result
    text = content_blocks[0].text
    assert "sparse-attention" in text
    assert (root / "progress" / "2030-01-01.md").exists()
