"""Vault orchestration: locking, reindex-on-mutation, alias resolution, and get_context.

Built on top of models.py's pure parse/dump/render helpers — this module owns all the I/O
and side effects; models.py stays a pure schema/rendering layer. See CLAUDE.md for the
conventions this implements.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Optional

from filelock import FileLock

from server.models import (
    CodeRef,
    Experiment,
    MetricRecord,
    PAGE_MODELS,
    Resource,
    ResearchTopic,
    atomic_write,
    dump_page,
    experiment_id,
    load_bucket,
    parse_page,
    render_index,
    slugify,
)

BUCKET_TITLES = {"research": "Research", "experiments": "Experiments", "resources": "Resources"}


class VaultError(Exception):
    pass


class NotFound(VaultError):
    pass


class AlreadyExists(VaultError):
    pass


# --- body section helpers (light text-splice, not a full markdown AST) ------

_ATTEMPT_HEADING_RE = re.compile(r"^### (Attempt \d+.*)$", re.MULTILINE)


def _section_bounds(body: str, heading: str) -> Optional[tuple[list[str], int, int]]:
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


def _append_under_heading(body: str, heading: str, text: str) -> str:
    bounds = _section_bounds(body, heading)
    if bounds is None:
        return body.rstrip("\n") + f"\n\n## {heading}\n{text}\n"
    lines, idx, end = bounds
    new_lines = lines[:end] + [text.rstrip("\n"), ""] + lines[end:]
    return "\n".join(new_lines)


def _insert_before_heading_or_end(body: str, heading: str, text: str) -> str:
    """Insert `text` as a new block right before `## heading` if it exists, else at EOF.
    Used to keep newly-appended attempt blocks ahead of the trailing Current-best section."""
    lines = body.split("\n")
    heading_line = f"## {heading}"
    try:
        idx = lines.index(heading_line)
    except ValueError:
        return body.rstrip("\n") + f"\n\n{text.rstrip(chr(10))}\n"
    new_lines = lines[:idx] + [text.rstrip("\n"), ""] + lines[idx:]
    return "\n".join(new_lines)


def _replace_section(body: str, heading: str, text: str) -> str:
    bounds = _section_bounds(body, heading)
    if bounds is None:
        return body.rstrip("\n") + f"\n\n## {heading}\n{text}\n"
    lines, idx, end = bounds
    new_lines = lines[: idx + 1] + [text.rstrip("\n"), ""] + lines[end:]
    return "\n".join(new_lines)


def _extract_section(body: str, heading: str) -> str:
    bounds = _section_bounds(body, heading)
    if bounds is None:
        return ""
    lines, idx, end = bounds
    return "\n".join(lines[idx + 1 : end]).strip()


def _extract_attempts(body: str) -> list[str]:
    matches = list(_ATTEMPT_HEADING_RE.finditer(body))
    blocks = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        blocks.append(body[start:end].strip())
    return blocks


def _metrics_block(metrics: list[MetricRecord]) -> str:
    if not metrics:
        return ""
    records = "\n".join(
        f"- {{name: {m.name}, value: {m.value}, split: {m.split or 'null'}, attempt: {m.attempt}}}"
        for m in metrics
    )
    return f"```metrics\n{records}\n```\n"


class Vault:
    """One instance per running server process, rooted at a vault directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = FileLock(str(self.root / ".vault.lock"))
        self._alias_cache: dict[str, str] = {}
        self._rebuild_alias_cache()

    # --- paths ---------------------------------------------------------

    def _bucket_dir(self, bucket: str) -> Path:
        return self.root / bucket

    def _page_path(self, bucket: str, ident: str) -> Path:
        return self._bucket_dir(bucket) / f"{ident}.md"

    # --- alias resolution -----------------------------------------------

    def _rebuild_alias_cache(self) -> None:
        cache: dict[str, str] = {}
        for bucket, model_cls in PAGE_MODELS.items():
            for page, _ in load_bucket(self._bucket_dir(bucket), model_cls):
                ident = getattr(page, "id", None) or getattr(page, "citekey")
                ref = f"{bucket}:{ident}"
                cache[ident.lower()] = ref
                title = getattr(page, "title", None)
                if title:
                    cache.setdefault(title.lower(), ref)
                for alias in getattr(page, "aliases", []) or []:
                    cache[alias.lower()] = ref
        self._alias_cache = cache

    def resolve(self, ref: str) -> tuple[str, str]:
        if ":" in ref:
            bucket, ident = ref.split(":", 1)
            if bucket in PAGE_MODELS and self._page_path(bucket, ident).exists():
                return bucket, ident
        hit = self._alias_cache.get(ref.strip().lower())
        if hit is None:
            raise NotFound(f"no page matches {ref!r}")
        bucket, ident = hit.split(":", 1)
        return bucket, ident

    # --- reindex / log ----------------------------------------------------

    def _reindex(self, bucket: str) -> None:
        model_cls = PAGE_MODELS[bucket]
        pages = [p for p, _ in load_bucket(self._bucket_dir(bucket), model_cls)]
        if bucket == "resources":
            content = render_index(BUCKET_TITLES[bucket], pages, sort_key=lambda p: p.created)
        else:
            content = render_index(
                BUCKET_TITLES[bucket],
                pages,
                sort_key=lambda p: p.updated,
                group_key=lambda p: p.status,
            )
        atomic_write(self._bucket_dir(bucket) / "index.md", content)

    def _append_log(self, bucket: str, ident: str, message: str) -> None:
        ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        line = f"{ts} [{bucket}:{ident}] {message}\n"
        with open(self.root / "log.md", "a") as f:
            f.write(line)

    def _mutate(self, bucket: str, ident: str, log_message: str) -> None:
        self._reindex(bucket)
        self._append_log(bucket, ident, log_message)
        self._rebuild_alias_cache()

    # --- research ----------------------------------------------------------

    def start_research(self, topic: str, aim: str, background: str = "") -> dict:
        with self._lock:
            slug = slugify(topic)
            path = self._page_path("research", slug)
            if path.exists():
                raise AlreadyExists(f"research topic {slug!r} already exists")
            today = dt.date.today()
            model = ResearchTopic(
                id=slug, title=topic, status="active", origin="live", created=today, updated=today
            )
            body = f"## Aim\n{aim}\n"
            if background:
                body += f"\n## Background\n{background}\n"
            atomic_write(path, dump_page(model, body))
            self._mutate("research", slug, "created — status active")
            return {"bucket": "research", "id": slug}

    def log_brainstorm(self, topic_ref: str, note: str) -> dict:
        with self._lock:
            bucket, ident = self.resolve(topic_ref)
            if bucket != "research":
                raise VaultError(f"{topic_ref!r} is not a research topic")
            path = self._page_path(bucket, ident)
            model, body = parse_page(path, ResearchTopic)
            today = dt.date.today()
            body = body.rstrip("\n") + f"\n\n### {today.isoformat()}\n{note}\n"
            model.updated = today
            atomic_write(path, dump_page(model, body))
            self._mutate(bucket, ident, "brainstorm note appended")
            return {"bucket": bucket, "id": ident}

    # --- experiments ---------------------------------------------------------

    def start_experiment(self, research_ref: str, title: str, aim: str, setup: str) -> dict:
        with self._lock:
            r_bucket, r_ident = self.resolve(research_ref)
            if r_bucket != "research":
                raise VaultError(f"{research_ref!r} is not a research topic")
            today = dt.date.today()
            eid = experiment_id(today, r_ident, title)
            path = self._page_path("experiments", eid)
            base_eid, n = eid, 2
            while path.exists():
                eid = f"{base_eid}-{n}"
                path = self._page_path("experiments", eid)
                n += 1
            model = Experiment(
                id=eid,
                title=title,
                status="planned",
                research_refs=[r_ident],
                created=today,
                updated=today,
                latest_attempt=0,
            )
            body = f"## Aim\n{aim}\n\n## Setup (current)\n{setup}\n"
            atomic_write(path, dump_page(model, body))
            self._mutate("experiments", eid, "created — status planned")
            return {"bucket": "experiments", "id": eid}

    def update_experiment(
        self,
        ref: str,
        status: Optional[str] = None,
        setup_delta: Optional[str] = None,
        attempt_notes: Optional[str] = None,
        metrics: Optional[list[dict]] = None,
    ) -> dict:
        with self._lock:
            bucket, ident = self.resolve(ref)
            if bucket != "experiments":
                raise VaultError(f"{ref!r} is not an experiment")
            path = self._page_path(bucket, ident)
            model, body = parse_page(path, Experiment)
            today = dt.date.today()

            if setup_delta:
                body = _append_under_heading(body, "Setup (current)", setup_delta)

            metric_records = [MetricRecord(**m) for m in (metrics or [])]
            logged_attempt = False
            if attempt_notes is not None or metric_records:
                new_attempt = model.latest_attempt + 1
                attempt_status = status or "running"
                section = (
                    f"### Attempt {new_attempt} — {today.isoformat()} ({attempt_status})\n"
                    f"{_metrics_block(metric_records)}Notes: {attempt_notes or ''}\n"
                )
                body = _insert_before_heading_or_end(body, "Current best", section)
                model.latest_attempt = new_attempt
                logged_attempt = True
                if metric_records:
                    summary = ", ".join(f"{m.name}={m.value}" for m in metric_records)
                    body = _replace_section(body, "Current best", f"Attempt {new_attempt} — {summary}")

            if status:
                model.status = status

            model.updated = today
            atomic_write(path, dump_page(model, body))
            msg = f"status {model.status}"
            if logged_attempt:
                msg = f"attempt {model.latest_attempt} logged — {msg}"
            self._mutate(bucket, ident, msg)
            return {"bucket": bucket, "id": ident, "status": model.status, "latest_attempt": model.latest_attempt}

    def link_code(
        self,
        ref: str,
        repo_path: str,
        commit_sha: Optional[str] = None,
        remote: Optional[str] = None,
        entrypoint: Optional[str] = None,
        dirty: bool = False,
    ) -> dict:
        with self._lock:
            bucket, ident = self.resolve(ref)
            if bucket != "experiments":
                raise VaultError(f"{ref!r} is not an experiment")
            path = self._page_path(bucket, ident)
            model, body = parse_page(path, Experiment)
            model.code_ref = CodeRef(
                path=repo_path, remote=remote, commit=commit_sha, entrypoint=entrypoint, dirty=dirty
            )
            model.updated = dt.date.today()
            atomic_write(path, dump_page(model, body))
            self._mutate(bucket, ident, f"code linked — {repo_path}")
            return {"bucket": bucket, "id": ident}

    # --- resources -----------------------------------------------------------

    def add_resource(
        self,
        citekey: str,
        title: str,
        authors: Optional[list[str]] = None,
        path_or_url: Optional[str] = None,
        tags: Optional[list[str]] = None,
        annotation: str = "",
    ) -> dict:
        with self._lock:
            slug = slugify(citekey)
            path = self._page_path("resources", slug)
            if path.exists():
                raise AlreadyExists(f"resource {slug!r} already exists")
            model = Resource(
                citekey=slug,
                title=title,
                authors=authors or [],
                tags=tags or [],
                origin="live",
                path_or_url=path_or_url,
                created=dt.date.today(),
            )
            atomic_write(path, dump_page(model, annotation))
            self._mutate("resources", slug, "added")
            return {"bucket": "resources", "id": slug}

    def annotate_resource(self, ref: str, note: str) -> dict:
        with self._lock:
            bucket, ident = self.resolve(ref)
            if bucket != "resources":
                raise VaultError(f"{ref!r} is not a resource")
            path = self._page_path(bucket, ident)
            model, body = parse_page(path, Resource)
            body = body.rstrip("\n") + f"\n\n### {dt.date.today().isoformat()}\n{note}\n"
            atomic_write(path, dump_page(model, body))
            self._mutate(bucket, ident, "annotation appended")
            return {"bucket": bucket, "id": ident}

    # --- get_context ---------------------------------------------------------

    def _recent_log_lines(self, refs: set[str], limit: int = 10) -> list[str]:
        log_path = self.root / "log.md"
        if not log_path.exists():
            return []
        tags = {f"[{b}:{r}]" for b in PAGE_MODELS for r in refs}
        lines = [
            line.rstrip("\n")
            for line in log_path.read_text().splitlines()
            if line.strip() and not line.startswith("<!--")
        ]
        filtered = [line for line in lines if any(tag in line for tag in tags)]
        return filtered[-limit:]

    def get_context(self, ref: str) -> str:
        bucket, ident = self.resolve(ref)
        if bucket == "research":
            return self._context_for_research(ident)
        if bucket == "experiments":
            return self._context_for_experiment(ident)
        return self._context_for_resource(ident)

    def _context_for_research(self, ident: str) -> str:
        topic, topic_body = parse_page(self._page_path("research", ident), ResearchTopic)
        experiments = [
            (p, b)
            for p, b in load_bucket(self._bucket_dir("experiments"), Experiment)
            if ident in p.research_refs
        ]
        resource_idents = {r for exp, _ in experiments for r in exp.resource_refs}
        resources = [
            (p, b) for p, b in load_bucket(self._bucket_dir("resources"), Resource) if p.citekey in resource_idents
        ]
        refs = {ident} | {exp.id for exp, _ in experiments}
        recent_log = self._recent_log_lines(refs)

        lines = [f"# {topic.title}", f"_status: {topic.status}_", ""]
        aim = _extract_section(topic_body, "Aim")
        if aim:
            lines += ["## Aim", aim, ""]
        background = _extract_section(topic_body, "Background")
        if background:
            lines += ["## Background", background, ""]

        flags = [
            f"- BLOCKED: {exp.id}" for exp, _ in experiments if exp.status == "blocked"
        ] + [
            f"- UNVERIFIED (backfilled): {exp.id}"
            for exp, _ in experiments
            if exp.origin == "backfilled" and not exp.verified
        ]
        if flags:
            lines += ["## Flags", *flags, ""]

        lines.append("## Experiments")
        if not experiments:
            lines.append("_none yet_")
        for exp, body in sorted(experiments, key=lambda t: t[0].updated, reverse=True):
            current_best = _extract_section(body, "Current best")
            attempts = _extract_attempts(body)
            lines.append(f"\n### {exp.id} ({exp.status}, updated {exp.updated})")
            if current_best:
                lines.append(f"Current best: {current_best}")
            shown = attempts[-2:]
            omitted = len(attempts) - len(shown)
            if omitted > 0:
                lines.append(f"_{omitted} earlier attempt(s) omitted — ask for full history of {exp.id} if needed._")
            lines.extend(shown)

        lines.append("\n## Resources")
        if not resources:
            lines.append("_none linked_")
        for res, body in resources:
            snippet = body.strip().split("\n\n")[0][:200] if body.strip() else ""
            lines.append(f"- **{res.citekey}** — {res.title}: {snippet}")

        if recent_log:
            lines.append("\n## Recent activity")
            lines.extend(recent_log)

        return "\n".join(lines) + "\n"

    def _context_for_experiment(self, ident: str) -> str:
        exp, body = parse_page(self._page_path("experiments", ident), Experiment)
        resources = [
            (p, b) for p, b in load_bucket(self._bucket_dir("resources"), Resource) if p.citekey in exp.resource_refs
        ]
        recent_log = self._recent_log_lines({ident})

        lines = [f"# {exp.id}", f"_status: {exp.status}, updated {exp.updated}_", ""]
        aim = _extract_section(body, "Aim")
        if aim:
            lines += ["## Aim", aim, ""]
        current_best = _extract_section(body, "Current best")
        if current_best:
            lines += [f"Current best: {current_best}", ""]
        if exp.origin == "backfilled":
            lines.append(f"_origin: backfilled, verified: {exp.verified}_\n")

        attempts = _extract_attempts(body)
        lines.append("## Attempts")
        lines.extend(attempts)

        lines.append("\n## Resources")
        if not resources:
            lines.append("_none linked_")
        for res, res_body in resources:
            snippet = res_body.strip().split("\n\n")[0][:200] if res_body.strip() else ""
            lines.append(f"- **{res.citekey}** — {res.title}: {snippet}")

        if recent_log:
            lines.append("\n## Recent activity")
            lines.extend(recent_log)

        return "\n".join(lines) + "\n"

    def _context_for_resource(self, ident: str) -> str:
        res, body = parse_page(self._page_path("resources", ident), Resource)
        return f"# {res.title} ({res.citekey})\n\n{body}"
