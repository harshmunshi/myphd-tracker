import re
from pathlib import Path

import pytest

from server.dashboard.render import build_dashboard
from server.storage import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    for bucket in ("research", "experiments", "resources", "progress"):
        (tmp_path / bucket).mkdir()
    (tmp_path / "log.md").write_text("")
    return Vault(tmp_path)


def test_build_dashboard_on_empty_vault_does_not_crash(vault: Vault):
    written = build_dashboard(vault.root)
    index = (vault.root / "dashboard" / "index.html").read_text()
    assert "No research topics yet" in index
    assert any(p.name == "bibliography.html" for p in written)


def test_build_dashboard_renders_topic_experiment_and_sparkline(vault: Vault):
    vault.start_research("Sparse Attention", aim="Make attention sub-quadratic.")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(
        exp["id"],
        status="failed",
        attempt_notes="OOM",
        metrics=[{"name": "val_ppl", "value": 14.2, "attempt": 1}],
    )
    vault.update_experiment(
        exp["id"],
        status="done",
        attempt_notes="converges",
        metrics=[{"name": "val_ppl", "value": 11.8, "attempt": 2}],
    )
    vault.add_resource("child2019generating", title="Generating Long Sequences", authors=["Child"])

    build_dashboard(vault.root)

    index = (vault.root / "dashboard" / "index.html").read_text()
    assert "Sparse Attention" in index
    assert "sub-quadratic" in index  # rendered from markdown, not raw "## Aim" heading

    topic_page = (vault.root / "dashboard" / "topics" / "sparse-attention.html").read_text()
    assert exp["id"] in topic_page
    assert "val_ppl" in topic_page
    assert "<svg" in topic_page  # sparkline rendered
    assert "<polyline" in topic_page

    bib = (vault.root / "dashboard" / "bibliography.html").read_text()
    assert "child2019generating" in bib
    assert "Generating Long Sequences" in bib


def test_build_dashboard_has_no_external_network_dependencies():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for bucket in ("research", "experiments", "resources", "progress"):
            (root / bucket).mkdir()
        (root / "log.md").write_text("")
        vault = Vault(root)
        vault.start_research("Sparse Attention", aim="test")

        build_dashboard(vault.root)

        for html_file in (vault.root / "dashboard").rglob("*.html"):
            text = html_file.read_text()
            assert "<script" not in text
            assert "cdn." not in text
            assert "fonts.googleapis" not in text
            # the only allowed "http://" is the SVG xmlns URI, never a fetchable resource
            for line in text.splitlines():
                if "http://" in line or "https://" in line:
                    assert "www.w3.org/2000/svg" in line


def test_build_dashboard_removes_stale_topic_pages_on_rebuild(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    build_dashboard(vault.root)
    stale_page = vault.root / "dashboard" / "topics" / "sparse-attention.html"
    assert stale_page.exists()

    # simulate the topic being renamed: old page file removed, new one created
    (vault.root / "research" / "sparse-attention.md").unlink()
    vault.start_research("Block Sparse Attention", aim="...")

    build_dashboard(vault.root)
    assert not stale_page.exists()
    assert (vault.root / "dashboard" / "topics" / "block-sparse-attention.html").exists()


def test_build_dashboard_is_idempotent_full_rebuild(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    first = build_dashboard(vault.root)
    second = build_dashboard(vault.root)
    assert {p.name for p in first} == {p.name for p in second}


def test_topic_page_renders_background_and_dated_notes(vault: Vault):
    vault.start_research(
        "Sparse Attention",
        aim="Make attention sub-quadratic.",
        background="Quadratic scaling hurts.",
    )
    vault.log_brainstorm("Sparse Attention", "Looked into Longformer and Reformer today.")

    build_dashboard(vault.root)

    topic_page = (vault.root / "dashboard" / "topics" / "sparse-attention.html").read_text()
    assert "Quadratic scaling hurts" in topic_page
    assert "Longformer and Reformer" in topic_page


def test_topic_page_renders_full_attempt_history(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")
    vault.update_experiment(exp["id"], status="failed", attempt_notes="ran out of memory on attempt one")
    vault.update_experiment(exp["id"], status="running", attempt_notes="fixed batch size for attempt two")

    build_dashboard(vault.root)

    topic_page = (vault.root / "dashboard" / "topics" / "sparse-attention.html").read_text()
    assert "ran out of memory on attempt one" in topic_page
    assert "fixed batch size for attempt two" in topic_page


def test_topic_page_sidebar_toc_links_to_sections_on_same_page(vault: Vault):
    vault.start_research("Sparse Attention", aim="Make attention sub-quadratic.", background="...")
    vault.log_brainstorm("Sparse Attention", "First finding.")
    exp = vault.start_experiment("Sparse Attention", title="Baseline", aim="...", setup="...")

    build_dashboard(vault.root)

    topic_page = (vault.root / "dashboard" / "topics" / "sparse-attention.html").read_text()
    assert 'href="#aim"' in topic_page and 'id="aim"' in topic_page
    assert 'href="#background"' in topic_page and 'id="background"' in topic_page
    assert 'href="#experiments"' in topic_page and 'id="experiments"' in topic_page
    assert f'href="#exp-{exp["id"]}"' in topic_page and f'id="exp-{exp["id"]}"' in topic_page


def test_topic_page_toc_anchors_unique_for_same_day_notes(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    for i in range(3):
        vault.log_brainstorm("Sparse Attention", f"finding number {i}")

    build_dashboard(vault.root)

    topic_page = (vault.root / "dashboard" / "topics" / "sparse-attention.html").read_text()
    ids = re.findall(r'id="(note-[^"]+)"', topic_page)
    assert len(ids) == 3
    assert len(set(ids)) == 3  # same-day notes must not collide on one anchor


def test_resource_gets_its_own_page_linked_from_bibliography(vault: Vault):
    vault.add_resource(
        "child2019generating",
        title="Generating Long Sequences",
        authors=["Child"],
        annotation="Cited for the sparse-factorization trick in section 3.",
    )

    build_dashboard(vault.root)

    bib = (vault.root / "dashboard" / "bibliography.html").read_text()
    assert 'href="resources/child2019generating.html"' in bib

    resource_page = (vault.root / "dashboard" / "resources" / "child2019generating.html").read_text()
    assert "sparse-factorization trick" in resource_page


def test_bibliography_is_grouped_per_topic_not_one_flat_list(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.start_research("Graph Embeddings", aim="...")
    vault.add_resource(
        "child2019generating",
        title="Generating Long Sequences",
        research_ref="Sparse Attention",
    )
    vault.add_resource("sun2019rotate", title="RotatE", research_ref="Graph Embeddings")
    vault.add_resource("orphan2020paper", title="Orphan Paper")  # no research_ref

    build_dashboard(vault.root)

    bib = (vault.root / "dashboard" / "bibliography.html").read_text()
    # each topic gets its own heading/group, plus an Unlinked group for the orphan — search for
    # the group heading markup specifically, since the sidebar nav also lists both topic titles.
    # Don't assume which of the two topic groups renders first (both created same day, so file
    # glob order decides it) — just check each resource sits within its own group's span.
    sparse_idx = bib.index('<a href="topics/sparse-attention.html">Sparse Attention</a></h2>')
    graph_idx = bib.index('<a href="topics/graph-embeddings.html">Graph Embeddings</a></h2>')
    orphan_group_idx = bib.index("<h2>Unlinked</h2>")
    child_idx = bib.index("child2019generating")
    rotate_idx = bib.index("sun2019rotate")
    orphan_res_idx = bib.index("orphan2020paper")

    groups = sorted(
        [("sparse", sparse_idx, child_idx), ("graph", graph_idx, rotate_idx)],
        key=lambda g: g[1],
    )
    (_, first_heading, first_resource), (_, second_heading, second_resource) = groups
    assert first_heading < first_resource < second_heading  # first group's resource before next heading
    assert second_heading < second_resource < orphan_group_idx  # second group's resource before Unlinked
    assert orphan_group_idx < orphan_res_idx  # unlinked resource falls into its own group

    # and the topic page itself carries a Resources section for its own linked resource(s)
    topic_page = (vault.root / "dashboard" / "topics" / "sparse-attention.html").read_text()
    assert 'href="#resources"' in topic_page
    assert 'id="resources"' in topic_page
    assert "child2019generating" in topic_page
    assert "sun2019rotate" not in topic_page  # doesn't leak into the wrong topic's page


def test_progress_report_gets_its_own_page_linked_from_index(vault: Vault):
    vault.start_research("Sparse Attention", aim="...")
    vault.weekly_progress()

    build_dashboard(vault.root)

    index = (vault.root / "dashboard" / "index.html").read_text()
    assert 'href="progress/' in index

    report_pages = list((vault.root / "dashboard" / "progress").glob("*.html"))
    assert len(report_pages) == 1
    assert "Weekly Progress" in report_pages[0].read_text()
