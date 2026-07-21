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
from typing import Callable, Literal, Optional

import frontmatter
from pydantic import BaseModel, Field

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
    id: str
    title: str
    aliases: list[str] = Field(default_factory=list)
    status: ResearchStatus = "active"
    origin: Origin = "live"
    created: date
    updated: date


class Experiment(BaseModel):
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
    citekey: str
    title: str
    authors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    origin: Origin = "live"
    path_or_url: Optional[str] = None
    created: date


PAGE_MODELS: dict[str, type[BaseModel]] = {
    "research": ResearchTopic,
    "experiments": Experiment,
    "resources": Resource,
}


def _page_ident(page: BaseModel) -> str:
    return getattr(page, "id", None) or getattr(page, "citekey")


# --- frontmatter <-> markdown ----------------------------------------------


def parse_page(path: Path, model_cls: type[BaseModel]) -> tuple[BaseModel, str]:
    post = frontmatter.loads(Path(path).read_text())
    model = model_cls.model_validate(post.metadata)
    return model, post.content


def dump_page(model: BaseModel, body: str) -> str:
    metadata = model.model_dump(mode="json", exclude_none=True)
    post = frontmatter.Post(body, **metadata)
    return frontmatter.dumps(post) + "\n"


def load_bucket(bucket_dir: Path, model_cls: type[BaseModel]) -> list[tuple[BaseModel, str]]:
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
    pages: list[BaseModel],
    sort_key: Callable[[BaseModel], object],
    group_key: Optional[Callable[[BaseModel], str]] = None,
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
        lines = [f"- [{p.title}]({_page_ident(p)}.md)" for p in sorted_pages]
        return header + "\n" + "\n".join(lines) + "\n"

    groups: dict[str, list[BaseModel]] = {}
    for p in sorted_pages:
        groups.setdefault(group_key(p), []).append(p)

    out = [header.rstrip("\n")]
    for label, group_pages in groups.items():
        out.append(f"\n## {label}\n")
        out.extend(f"- [{p.title}]({_page_ident(p)}.md)" for p in group_pages)
    return "\n".join(out) + "\n"
