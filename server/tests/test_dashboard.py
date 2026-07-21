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
        exp["id"], status="failed", attempt_notes="OOM", metrics=[{"name": "val_ppl", "value": 14.2, "attempt": 1}]
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
