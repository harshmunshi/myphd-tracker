import re
import subprocess
from datetime import date
from pathlib import Path

import pytest

from server.models import ExperimentStatus
from server.storage import AlreadyExists, NotFound, Vault, VaultError


def _init_git_repo_with_commit(repo: Path, message: str) -> None:
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "train.py").write_text("print('hi')\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    for bucket in ("research", "experiments", "resources", "progress"):
        (tmp_path / bucket).mkdir()
    (tmp_path / "log.md").write_text("")
    return Vault(tmp_path)


def test_start_research_creates_page_and_index(vault: Vault):
    result = vault.start_research("Sparse Attention", aim="Make attention sub-quadratic.")
    assert result["bucket"] == "research"
    assert result["id"] == "sparse-attention"
    assert "log_research_note" in result["reminder"]

    page = (vault.root / "research" / "sparse-attention.md").read_text()
    assert "Make attention sub-quadratic." in page

    index = (vault.root / "research" / "index.md").read_text()
    assert "Sparse Attention" in index
    assert "do not hand-edit" in index

    log = (vault.root / "log.md").read_text()
    assert "[research:sparse-attention]" in log


def test_duplicate_start_research_raises(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    with pytest.raises(AlreadyExists):
        vault.start_research("Sparse Attention", aim="...")


def test_log_brainstorm_appends_note(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.log_brainstorm("Sparse Attention", "Maybe top-k works?")
    page = (vault.root / "research" / "sparse-attention.md").read_text()
    assert "Maybe top-k works?" in page


def test_start_experiment_links_research_and_disambiguates_collisions(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    e1 = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    e2 = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    assert e1["id"] != e2["id"]
    assert e2["id"].endswith("-2")

    page = (vault.root / "experiments" / f"{e1['id']}.md").read_text()
    assert "research_refs" in page
    assert "sparse-attention" in page


def test_start_experiment_requires_research_ref(vault: Vault):
    with pytest.raises(NotFound):
        vault.start_experiment("nonexistent-topic", title="x", aim="x", setup="x")


def test_update_experiment_append_only_attempt_grammar(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="batch=8")

    vault.update_experiment(
        exp["id"],
        status="failed",
        attempt_notes="OOM at seq_len 4096",
        metrics=[{"name": "val_ppl", "value": 14.2, "attempt": 1}],
    )
    vault.update_experiment(
        exp["id"],
        status="running",
        setup_delta="reduced batch size to 4",
        attempt_notes="converges, still memory bound",
        metrics=[{"name": "val_ppl", "value": 12.1, "attempt": 2}],
    )

    vault.update_experiment(
        exp["id"],
        status="done",
        attempt_notes="memory-bound but stable, good enough",
        metrics=[{"name": "val_ppl", "value": 11.8, "attempt": 3}],
    )

    body = (vault.root / "experiments" / f"{exp['id']}.md").read_text()
    assert "Attempt 1" in body and "Attempt 2" in body and "Attempt 3" in body
    assert "OOM at seq_len 4096" in body
    assert "reduced batch size to 4" in body
    assert body.count("### Attempt") == 3
    assert "val_ppl=11.8" in body
    # Current best must stay the single trailing section, after all three attempt blocks
    assert body.count("## Current best") == 1
    assert body.rindex("### Attempt 3") < body.rindex("## Current best")

    result = vault.update_experiment(exp["id"])
    assert result["latest_attempt"] == 3
    assert result["status"] == "done"


def test_update_experiment_rejects_invalid_status_before_writing(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")

    with pytest.raises(Exception):
        # Deliberately invalid at the type level too — this test exists to prove the runtime
        # guard rejects it; the ignore is for that intentional mismatch, not a real bug.
        bad_status: ExperimentStatus = "in_progress"  # type: ignore[assignment]
        vault.update_experiment(exp["id"], status=bad_status)

    body = (vault.root / "experiments" / f"{exp['id']}.md").read_text()
    assert "in_progress" not in body
    assert "status: planned" in body

    # a bad status must not have crept into the page even partially — load_bucket (used by
    # the dashboard, get_context, and weekly_progress) must still be able to parse this page
    from server.models import Experiment, load_bucket

    pages = load_bucket(vault.root / "experiments", Experiment)
    assert len(pages) == 1


def test_update_experiment_no_attempt_when_no_notes_or_metrics(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(exp["id"], status="blocked")
    body = (vault.root / "experiments" / f"{exp['id']}.md").read_text()
    assert "### Attempt" not in body
    assert "status: blocked" in body


def test_link_code_sets_code_ref(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.link_code(exp["id"], repo_path="/code/topk-attn", commit_sha="a1b2c3d", dirty=True)
    body = (vault.root / "experiments" / f"{exp['id']}.md").read_text()
    assert "a1b2c3d" in body
    assert "dirty: true" in body


def test_add_resource_and_annotate(vault: Vault):
    vault.add_resource(
        "child2019generating",
        title="Generating Long Sequences",
        annotation="Sparse attn origin.",
    )
    vault.annotate_resource("child2019generating", "Re-read section 3.2.")
    body = (vault.root / "resources" / "child2019generating.md").read_text()
    assert "Sparse attn origin." in body
    assert "Re-read section 3.2." in body
    index = (vault.root / "resources" / "index.md").read_text()
    assert "Generating Long Sequences" in index


def test_add_resource_with_research_ref_links_directly_to_topic(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.add_resource(
        "child2019generating",
        title="Generating Long Sequences",
        research_ref="Sparse Attention",
    )
    body = (vault.root / "resources" / "child2019generating.md").read_text()
    assert "research_refs" in body and "sparse-attention" in body

    # shows up in the topic's own context WITHOUT needing an experiment to reference it
    context = vault.get_context("Sparse Attention")
    assert "child2019generating" in context


def test_add_resource_with_invalid_research_ref_raises(vault: Vault):
    with pytest.raises(VaultError):
        vault.add_resource("child2019generating", title="X", research_ref="does not exist")
    assert not (vault.root / "resources" / "child2019generating.md").exists()


def test_link_resource_is_retroactive_and_additive_not_replacing(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.start_research("Graph Embeddings", aim="...")
    vault.add_resource("child2019generating", title="Generating Long Sequences")

    result = vault.link_resource("child2019generating", "Sparse Attention")
    assert result["research_refs"] == ["sparse-attention"]

    result = vault.link_resource("child2019generating", "Graph Embeddings")
    assert result["research_refs"] == ["sparse-attention", "graph-embeddings"]

    # linking the same topic again must not duplicate
    result = vault.link_resource("child2019generating", "Sparse Attention")
    assert result["research_refs"] == ["sparse-attention", "graph-embeddings"]

    assert "child2019generating" in vault.get_context("Sparse Attention")
    assert "child2019generating" in vault.get_context("Graph Embeddings")


def test_resolve_by_alias_and_title_and_bucket_ref(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    assert vault.resolve("sparse attention") == ("research", "sparse-attention")
    assert vault.resolve("research:sparse-attention") == (
        "research",
        "sparse-attention",
    )
    with pytest.raises(NotFound):
        vault.resolve("does not exist")


def test_find_similar_topics_matches_close_names_not_unrelated_ones(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.start_research("Graph Embeddings", aim="...")

    matches = vault.find_similar_topics("sparse attn")
    assert matches
    assert matches[0]["id"] == "sparse-attention"

    assert vault.find_similar_topics("something totally unrelated to either topic") == []


def test_find_similar_topics_empty_vault_returns_empty(vault: Vault):
    assert vault.find_similar_topics("anything") == []


def test_list_experiments_for_topic_returns_only_linked_experiments(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.start_research("Graph Embeddings", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(exp["id"], status="running", attempt_notes="first run")

    results = vault.list_experiments_for_topic("Sparse Attention")
    assert len(results) == 1
    assert results[0]["id"] == exp["id"]
    assert results[0]["status"] == "running"
    assert results[0]["latest_attempt"] == 1

    assert vault.list_experiments_for_topic("Graph Embeddings") == []


def test_list_experiments_for_topic_rejects_non_research_ref(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    with pytest.raises(VaultError):
        vault.list_experiments_for_topic(exp["id"])


def test_session_flags_surfaces_blocked_unverified_and_unlinked(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(exp["id"], status="blocked", attempt_notes="stuck")
    vault.add_resource("child2019generating", title="Generating Long Sequences")  # no research_ref

    flags = vault.session_flags()
    assert flags["blocked"] == [{"id": exp["id"], "title": "Baseline"}]
    assert flags["unlinked_resources"] == [
        {"citekey": "child2019generating", "title": "Generating Long Sequences"}
    ]
    assert flags["unverified_backfilled"] == []


def test_session_flags_empty_vault_returns_empty_lists(vault: Vault):
    assert vault.session_flags() == {
        "blocked": [],
        "unverified_backfilled": [],
        "unlinked_resources": [],
    }


def test_get_context_for_research_includes_experiments_resources_and_flags(
    vault: Vault,
):
    vault.start_research("Sparse Attention", aim="Make attention sub-quadratic.")
    vault.add_resource(
        "child2019generating",
        title="Generating Long Sequences",
        annotation="Origin paper.",
    )
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(
        exp["id"],
        status="blocked",
        attempt_notes="OOM",
        metrics=[{"name": "val_ppl", "value": 14.2, "attempt": 1}],
    )
    # link the resource manually via direct page edit to simulate resource_refs
    from server.models import Experiment, dump_page, parse_page

    path = vault.root / "experiments" / f"{exp['id']}.md"
    model, body = parse_page(path, Experiment)
    model.resource_refs = ["child2019generating"]
    path.write_text(dump_page(model, body))
    vault._rebuild_alias_cache()

    context = vault.get_context("Sparse Attention")
    assert "Make attention sub-quadratic." in context
    assert exp["id"] in context
    assert "BLOCKED" in context
    assert "child2019generating" in context
    assert "Origin paper." in context


def test_get_context_for_research_surfaces_logged_notes_and_findings(vault: Vault):
    vault.start_research("Sparse Attention", aim="Make attention sub-quadratic.")
    vault.log_brainstorm("Sparse Attention", "Maybe top-k works?")
    vault.log_brainstorm(
        "Sparse Attention",
        "Deep-research findings: Linformer achieves O(n) via low-rank projection.",
    )

    context = vault.get_context("Sparse Attention")
    assert "Maybe top-k works?" in context
    assert "Linformer achieves O(n)" in context
    assert "Notes & Findings" in context


def test_get_context_omits_older_notes_beyond_last_three(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    for i in range(5):
        vault.log_brainstorm("Sparse Attention", f"note {i}")

    context = vault.get_context("Sparse Attention")
    assert "note 4" in context and "note 3" in context and "note 2" in context
    assert "note 0" not in context
    assert "earlier note(s) omitted" in context


def test_reindex_groups_experiments_by_status(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    e1 = vault.start_experiment("Sparse Attention", title="Attempt A", aim="...", setup="...")
    e2 = vault.start_experiment("Sparse Attention", title="Attempt B", aim="...", setup="...")
    vault.update_experiment(e1["id"], status="done", attempt_notes="works", metrics=[])
    vault.update_experiment(e2["id"], status="failed", attempt_notes="broken", metrics=[])
    index = (vault.root / "experiments" / "index.md").read_text()
    assert "done" in index and "failed" in index


def test_weekly_progress_includes_tracker_activity_and_persists_report(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(exp["id"], status="done", attempt_notes="works", metrics=[])
    vault.add_resource("child2019generating", title="Generating Long Sequences")

    report = vault.weekly_progress()

    assert "sparse-attention" in report
    assert exp["id"] in report
    assert "child2019generating" in report

    report_path = vault.root / "progress" / f"{date.today().isoformat()}.md"
    assert report_path.exists()
    index = (vault.root / "progress" / "index.md").read_text()
    assert "Week ending" in index


def test_weekly_progress_scans_linked_repo_and_flags_undocumented_commits(vault: Vault):
    repo = vault.root / "external_repo"
    _init_git_repo_with_commit(repo, "initial baseline run")

    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.link_code(exp["id"], repo_path=str(repo))

    report = vault.weekly_progress()

    assert "initial baseline run" in report
    assert exp["id"] in report
    assert "was not called" in report  # no update_experiment attempt logged despite the commit


def test_weekly_progress_does_not_flag_when_attempt_was_logged(vault: Vault):
    repo = vault.root / "external_repo"
    _init_git_repo_with_commit(repo, "converges now")

    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.link_code(exp["id"], repo_path=str(repo))
    vault.update_experiment(
        exp["id"],
        status="done",
        attempt_notes="converges",
        metrics=[{"name": "val_ppl", "value": 10.0, "attempt": 1}],
    )

    report = vault.weekly_progress()
    assert "converges now" in report
    assert "was not called" not in report


def test_weekly_progress_handles_missing_repo_path_gracefully(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.link_code(exp["id"], repo_path=str(vault.root / "does-not-exist"))

    report = vault.weekly_progress()
    assert "repo path not found" in report


def test_weekly_progress_is_structured_not_a_raw_log_dump(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.log_brainstorm("Sparse Attention", "First finding.")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(exp["id"], status="blocked", attempt_notes="stuck on OOM")
    vault.add_resource("child2019generating", title="Generating Long Sequences")

    report = vault.weekly_progress()

    headings = (
        "## Summary",
        "## Research",
        "## Experiments",
        "## Resources reviewed",
        "## Flags for discussion",
        "## Next steps",
    )
    for heading in headings:
        assert heading in report
    # raw log.md lines (timestamp + bracketed tag) must not leak into the rendered report
    assert "[research:" not in report
    assert "[experiments:" not in report
    assert "BLOCKED" in report  # blocked experiment surfaced as a flag, not buried in prose


def test_weekly_progress_demotes_headings_inside_notes_to_nest_under_topic(
    vault: Vault,
):
    vault.start_research("Sparse Attention", aim="...")
    vault.log_brainstorm("Sparse Attention", "## Deep dive\nSome findings.\n### Sub-point\nmore detail")

    report = vault.weekly_progress()

    # the note's own dated heading (### YYYY-MM-DD) demotes to #### so it nests under the topic's ###
    assert re.search(r"^#### \d{4}-\d{2}-\d{2}$", report, re.MULTILINE)
    # headings written *inside* the note content shift down one level too (## -> ###, ### -> ####)
    assert "### Deep dive" in report
    assert "#### Sub-point" in report
