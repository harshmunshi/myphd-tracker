from datetime import date
from pathlib import Path

import pytest

from server.models import (
    Experiment,
    ResearchTopic,
    atomic_write,
    dump_page,
    experiment_id,
    parse_page,
    render_index,
    slugify,
)


def test_slugify_basic():
    assert slugify("Sparse Attention!") == "sparse-attention"
    assert slugify("  multiple   spaces  ") == "multiple-spaces"
    assert slugify("Already-Slugged") == "already-slugged"


def test_slugify_empty_raises():
    with pytest.raises(ValueError):
        slugify("!!!")


def test_experiment_id_format():
    eid = experiment_id(date(2026, 7, 21), "Sparse Attention", "Baseline Run")
    assert eid == "2026-07-21-sparse-attention-baseline-run"


def test_atomic_write_round_trip(tmp_path: Path):
    target = tmp_path / "nested" / "page.md"
    atomic_write(target, "hello world")
    assert target.read_text() == "hello world"
    assert list(tmp_path.rglob("*.tmp")) == []


def test_atomic_write_overwrite_leaves_no_tmp(tmp_path: Path):
    target = tmp_path / "page.md"
    atomic_write(target, "first")
    atomic_write(target, "second")
    assert target.read_text() == "second"
    assert list(tmp_path.glob("*.tmp")) == []


def test_page_round_trip(tmp_path: Path):
    topic = ResearchTopic(
        id="sparse-attention",
        title="Sparse Attention",
        aliases=["research a", "sparse attn"],
        status="active",
        created=date(2026, 7, 21),
        updated=date(2026, 7, 21),
    )
    body = "## Aim\nMake attention sparse.\n"
    text = dump_page(topic, body)

    path = tmp_path / "sparse-attention.md"
    atomic_write(path, text)

    loaded, loaded_body = parse_page(path, ResearchTopic)
    assert loaded == topic
    assert loaded_body.strip() == body.strip()


def test_render_index_empty():
    out = render_index("Research", [], sort_key=lambda p: p.updated)
    assert "_No entries yet._" in out
    assert "do not hand-edit" in out


def test_render_index_grouped_by_status_sorted_by_updated():
    older = Experiment(
        id="2026-07-01-topic-old",
        title="Older experiment",
        status="done",
        created=date(2026, 7, 1),
        updated=date(2026, 7, 1),
    )
    newer = Experiment(
        id="2026-07-20-topic-new",
        title="Newer experiment",
        status="running",
        created=date(2026, 7, 20),
        updated=date(2026, 7, 20),
    )
    out = render_index(
        "Experiments",
        [older, newer],
        sort_key=lambda p: p.updated,
        group_key=lambda p: p.status,
    )
    assert out.index("running") < out.index("done")
    assert "Newer experiment" in out
    assert "Older experiment" in out


def test_render_index_flat_no_group_key():
    a = ResearchTopic(id="a", title="A", created=date(2026, 7, 1), updated=date(2026, 7, 1))
    b = ResearchTopic(id="b", title="B", created=date(2026, 7, 2), updated=date(2026, 7, 2))
    out = render_index("Research", [a, b], sort_key=lambda p: p.updated)
    assert out.index("(b.md)") < out.index("(a.md)")
