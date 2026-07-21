"""Deterministic static-site generator for the vault.

Reads the same page models as storage.py's get_context (via server.models) — never LLM-
authored, no incremental patching, just a full rebuild from current vault state every time.
Zero non-Python dependencies at render time: no CDN scripts, no client-side charting library
(see server/dashboard/svg.py), so the output is fully openable via file:// with no server
running. See CLAUDE.md.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown_it import MarkdownIt

from server.dashboard.svg import sparkline_svg
from server.models import (
    Experiment,
    ProgressReport,
    Resource,
    ResearchTopic,
    atomic_write,
    extract_attempts,
    extract_dated_notes,
    extract_metrics,
    extract_section,
    load_bucket,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)
_md = MarkdownIt("commonmark").enable(["table", "strikethrough"])


def _render_md(text: str) -> str:
    return _md.render(text) if text else ""


def _experiment_view(exp: Experiment, body: str) -> dict:
    by_name: dict[str, list[tuple[int, float]]] = {}
    for m in extract_metrics(body):
        by_name.setdefault(m.name, []).append((m.attempt, m.value))
    sparklines = {
        name: sparkline_svg([v for _, v in sorted(points)]) for name, points in by_name.items()
    }
    current_best = extract_section(body, "Current best")
    attempts_html = [_render_md(a) for a in reversed(extract_attempts(body))]
    return {
        "id": exp.id,
        "title": exp.title,
        "status": exp.status,
        "updated": exp.updated.isoformat(),
        "latest_attempt": exp.latest_attempt,
        "current_best": current_best,
        "current_best_html": _render_md(current_best),
        "attempts_html": attempts_html,
        "sparklines": sparklines,
        "origin": exp.origin,
        "verified": exp.verified,
    }


def _build_pages(vault_root: Path, output_dir: Path) -> list[Path]:
    topics = load_bucket(vault_root / "research", ResearchTopic)
    experiments = load_bucket(vault_root / "experiments", Experiment)
    resources = load_bucket(vault_root / "resources", Resource)
    reports = load_bucket(vault_root / "progress", ProgressReport)

    experiments_by_topic: dict[str, list[dict]] = {}
    for exp, body in experiments:
        view = _experiment_view(exp, body)
        for ref in exp.research_refs:
            experiments_by_topic.setdefault(ref, []).append(view)

    topic_views = []
    for topic, topic_body in topics:
        exps = sorted(
            experiments_by_topic.get(topic.id, []), key=lambda e: e["updated"], reverse=True
        )
        aim = extract_section(topic_body, "Aim")
        background = extract_section(topic_body, "Background")
        notes_html = [_render_md(n) for n in reversed(extract_dated_notes(topic_body))]
        topic_views.append(
            {
                "id": topic.id,
                "title": topic.title,
                "status": topic.status,
                "updated": topic.updated.isoformat(),
                "aim": aim,
                "aim_html": _render_md(aim),
                "background_html": _render_md(background),
                "notes_html": notes_html,
                "experiment_count": len(exps),
                "experiments": exps,
            }
        )
    topic_views.sort(key=lambda t: t["updated"], reverse=True)

    resource_views = [
        {
            "citekey": r.citekey,
            "title": r.title,
            "authors": r.authors,
            "tags": r.tags,
            "path_or_url": r.path_or_url,
            "annotation_html": _render_md(body),
        }
        for r, body in sorted(resources, key=lambda t: t[0].created, reverse=True)
    ]

    report_views = [
        {
            "id": r.id,
            "title": r.title,
            "week_start": r.week_start.isoformat(),
            "week_end": r.week_end.isoformat(),
            "body_html": _render_md(body),
        }
        for r, body in sorted(reports, key=lambda t: t[0].week_end, reverse=True)
    ]

    written: list[Path] = []

    index_html = _env.get_template("index.html").render(
        root_prefix="",
        topics=topic_views,
        resource_count=len(resource_views),
        reports=report_views,
    )
    index_path = output_dir / "index.html"
    atomic_write(index_path, index_html)
    written.append(index_path)

    topics_dir = output_dir / "topics"
    existing_topic_pages = set(topics_dir.glob("*.html")) if topics_dir.exists() else set()
    current_topic_pages = set()
    for topic in topic_views:
        html = _env.get_template("topic.html").render(root_prefix="../", topic=topic)
        path = topics_dir / f"{topic['id']}.html"
        atomic_write(path, html)
        written.append(path)
        current_topic_pages.add(path)
    for stale in existing_topic_pages - current_topic_pages:
        stale.unlink()

    bib_html = _env.get_template("bibliography.html").render(root_prefix="", resources=resource_views)
    bib_path = output_dir / "bibliography.html"
    atomic_write(bib_path, bib_html)
    written.append(bib_path)

    resources_dir = output_dir / "resources"
    existing_resource_pages = set(resources_dir.glob("*.html")) if resources_dir.exists() else set()
    current_resource_pages = set()
    for resource in resource_views:
        html = _env.get_template("resource.html").render(root_prefix="../", resource=resource)
        path = resources_dir / f"{resource['citekey']}.html"
        atomic_write(path, html)
        written.append(path)
        current_resource_pages.add(path)
    for stale in existing_resource_pages - current_resource_pages:
        stale.unlink()

    progress_dir = output_dir / "progress"
    existing_report_pages = set(progress_dir.glob("*.html")) if progress_dir.exists() else set()
    current_report_pages = set()
    for report in report_views:
        html = _env.get_template("progress.html").render(root_prefix="../", report=report)
        path = progress_dir / f"{report['id']}.html"
        atomic_write(path, html)
        written.append(path)
        current_report_pages.add(path)
    for stale in existing_report_pages - current_report_pages:
        stale.unlink()

    return written


def build_dashboard(vault_root: Path) -> list[Path]:
    """Regenerate the entire static dashboard under `<vault_root>/dashboard/`. Returns the
    list of files written. Safe to call repeatedly — it's a full rebuild, not an incremental
    patch, so stale pages for deleted/renamed topics never linger."""
    vault_root = Path(vault_root)
    return _build_pages(vault_root, vault_root / "dashboard")
