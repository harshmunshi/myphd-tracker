"""Frontmatter schemas and pure helpers for the vault.

Shared by the MCP tools (storage.py), the dashboard renderer, and get_context, so those
three never become competing parsers of the same markdown files — see CLAUDE.md for the
schema this module implements.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Optional, TypeVar

import frontmatter
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison

# --- naming -------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"cannot slugify empty/non-alphanumeric text: {text!r}")
    return slug


def experiment_id(on: date, topic_slug: str, short_title: str) -> str:
    return f"{on.isoformat()}-{slugify(topic_slug)}-{slugify(short_title)}"


# --- atomic I/O -----------------------------------------------------------


def atomic_write(path: Path, content: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


# --- frontmatter models ---------------------------------------------------


class MetricRecord(BaseModel):
    name: str
    value: float
    split: Optional[str] = None
    attempt: int


class CodeRef(BaseModel):
    path: str
    remote: Optional[str] = None
    commit: Optional[str] = None
    dirty: bool = False
    entrypoint: Optional[str] = None


class DataRef(BaseModel):
    path: Optional[str] = None
    url: Optional[str] = None


ResearchStatus = Literal["active", "paused", "abandoned", "published"]
ExperimentStatus = Literal["planned", "running", "blocked", "done", "failed"]
Origin = Literal["live", "backfilled"]


class ResearchTopic(BaseModel):
    # Without this, `model.status = "bogus"` silently succeeds (Pydantic only validates on
    # construction/model_validate by default) and the invalid value gets written to disk —
    # only surfacing as a crash later, in a completely different code path (load_bucket, used
    # by the dashboard, get_context, weekly_progress, and the alias cache).
    model_config = ConfigDict(validate_assignment=True)

    id: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    status: ResearchStatus = "active"
    origin: Origin = "live"
    created: date
    updated: date


class Experiment(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    id: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    status: ExperimentStatus = "planned"
    research_refs: list[str] = Field(default_factory=list)
    resource_refs: list[str] = Field(default_factory=list)
    code_ref: Optional[CodeRef] = None
    data_ref: Optional[DataRef] = None
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None
    origin: Origin = "live"
    verified: bool = True
    tags: list[str] = Field(default_factory=list)
    created: date
    updated: date
    latest_attempt: int = 0


class Resource(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    citekey: str
    title: str
    authors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    origin: Origin = "live"
    path_or_url: Optional[str] = None
    # Which research topic(s) this resource belongs to — a resource found while brainstorming a
    # topic (before any experiment exists) has nowhere else to attach, since Experiment.resource_refs
    # only links resources to experiments. Without this, every resource lands in one undifferentiated
    # global bibliography regardless of which idea it's actually for.
    research_refs: list[str] = Field(default_factory=list)
    created: date


class ProgressReport(BaseModel):
    """A regenerable weekly digest — unlike research/experiment/resource pages, re-running
    weekly_progress for the same week overwrites its report rather than appending to it.
    """

    model_config = ConfigDict(validate_assignment=True)

    id: str
    title: str
    week_start: date
    week_end: date
    created: date


PAGE_MODELS: dict[str, type[BaseModel]] = {
    "research": ResearchTopic,
    "experiments": Experiment,
    "resources": Resource,
    "progress": ProgressReport,
}


def _page_ident(page: BaseModel) -> str:
    return getattr(page, "id", None) or getattr(page, "citekey")


def _page_title(page: BaseModel) -> str:
    return getattr(page, "title")


# --- frontmatter <-> markdown ----------------------------------------------


PageModel = TypeVar("PageModel", bound=BaseModel)


def parse_page(path: Path, model_cls: type[PageModel]) -> tuple[PageModel, str]:
    post = frontmatter.loads(Path(path).read_text())
    model = model_cls.model_validate(post.metadata)
    return model, post.content


def dump_page(model: BaseModel, body: str) -> str:
    metadata = model.model_dump(mode="json", exclude_none=True)
    post = frontmatter.Post(body, **metadata)
    return frontmatter.dumps(post) + "\n"


def load_bucket(bucket_dir: Path, model_cls: type[PageModel]) -> list[tuple[PageModel, str]]:
    bucket_dir = Path(bucket_dir)
    pages = []
    for path in sorted(bucket_dir.glob("*.md")):
        if path.name == "index.md":
            continue
        pages.append(parse_page(path, model_cls))
    return pages


# --- index rendering (pure — no I/O; storage.py owns writing the result) ---


def render_index(
    bucket_title: str,
    pages: list[PageModel],
    sort_key: Callable[[PageModel], SupportsRichComparison],
    group_key: Optional[Callable[[PageModel], str]] = None,
    note: str = "",
) -> str:
    header = "<!-- GENERATED FILE — do not hand-edit. Rewritten in full on every mutation. -->\n\n"
    header += f"# {bucket_title}\n"
    if note:
        header += f"\n{note}\n"

    if not pages:
        return header + "\n_No entries yet._\n"

    sorted_pages = sorted(pages, key=sort_key, reverse=True)

    if group_key is None:
        lines = [f"- [{_page_title(p)}]({_page_ident(p)}.md)" for p in sorted_pages]
        return header + "\n" + "\n".join(lines) + "\n"

    groups: dict[str, list[PageModel]] = {}
    for p in sorted_pages:
        groups.setdefault(group_key(p), []).append(p)

    out = [header.rstrip("\n")]
    for label, group_pages in groups.items():
        out.append(f"\n## {label}\n")
        out.extend(f"- [{_page_title(p)}]({_page_ident(p)}.md)" for p in group_pages)
    return "\n".join(out) + "\n"


# --- body parsing (read-only; light text-splice, not a full markdown AST) ---
#
# Shared by storage.py (get_context, mutation helpers) and dashboard/render.py — both read
# the same body grammar (## sections, ### Attempt N blocks, ### <date> notes, ```metrics
# fences), so the parsing lives here once rather than being reimplemented per consumer.

ATTEMPT_HEADING_RE = re.compile(r"^### (Attempt \d+.*)$", re.MULTILINE)
DATED_NOTE_HEADING_RE = re.compile(r"^### \d{4}-\d{2}-\d{2}$", re.MULTILINE)
METRICS_BLOCK_RE = re.compile(r"```metrics\n(.*?)```", re.DOTALL)


def section_bounds(body: str, heading: str) -> Optional[tuple[list[str], int, int]]:
    lines = body.split("\n")
    heading_line = f"## {heading}"
    try:
        idx = lines.index(heading_line)
    except ValueError:
        return None
    end = len(lines)
    for i in range(idx + 1, len(lines)):
        if lines[i].startswith("## ") or lines[i].startswith("### "):
            end = i
            break
    return lines, idx, end


def extract_section(body: str, heading: str) -> str:
    bounds = section_bounds(body, heading)
    if bounds is None:
        return ""
    lines, idx, end = bounds
    return "\n".join(lines[idx + 1 : end]).strip()


def _extract_blocks(body: str, heading_re: re.Pattern) -> list[str]:
    matches = list(heading_re.finditer(body))
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        blocks.append(body[start:end].strip())
    return blocks


def extract_attempts(body: str) -> list[str]:
    return _extract_blocks(body, ATTEMPT_HEADING_RE)


def extract_dated_notes(body: str) -> list[str]:
    """Notes appended by log_research_note (### YYYY-MM-DD headings) — the running record of
    brainstorming and research findings on a topic. get_context must surface these, not just
    the static Aim/Background sections, or anything logged after topic creation is invisible.
    """
    return _extract_blocks(body, DATED_NOTE_HEADING_RE)


def extract_metrics(body: str) -> list[MetricRecord]:
    """Parse every ```metrics fenced block's records out of an experiment body. Each block's
    content is a YAML flow-style list of mappings (the exact shape update_experiment writes),
    so it round-trips through yaml.safe_load rather than needing a bespoke parser."""
    records: list[MetricRecord] = []
    for block in METRICS_BLOCK_RE.findall(body):
        try:
            parsed = yaml.safe_load(block) or []
        except yaml.YAMLError:
            continue
        for item in parsed:
            try:
                records.append(MetricRecord(**item))
            except (TypeError, ValidationError):
                continue
    return records
