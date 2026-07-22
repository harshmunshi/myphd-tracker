"""Vault orchestration: locking, reindex-on-mutation, alias resolution, and get_context.

Built on top of models.py's pure parse/dump/render helpers — this module owns all the I/O
and side effects; models.py stays a pure schema/rendering layer. See CLAUDE.md for the
conventions this implements.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path
from typing import Optional

from filelock import FileLock

from server.models import (
    CodeRef,
    Experiment,
    ExperimentStatus,
    MetricRecord,
    PAGE_MODELS,
    ProgressReport,
    Resource,
    ResearchTopic,
    atomic_write,
    dump_page,
    experiment_id,
    extract_attempts,
    extract_dated_notes,
    extract_section,
    load_bucket,
    parse_page,
    render_index,
    section_bounds,
    slugify,
)

BUCKET_TITLES = {
    "research": "Research",
    "experiments": "Experiments",
    "resources": "Resources",
    "progress": "Progress",
}

# Surfaced directly in tool output (start_research, get_context), not just the server-level
# MCP instructions — a reminder sitting in the system prompt is easy to lose track of deep
# into an agentic session; one repeated in the actual tool result the calling LLM just read
# is much harder to miss. See CLAUDE.md Scene A: findings that never get log_research_note'd
# are invisible to every future session.
RESEARCH_LOGGING_REMINDER = (
    "If you investigate this topic further in this turn (web search, deep-research pass, "
    "reading a paper), call log_research_note with a summary of what you found before you "
    "finish responding — findings that only live in the chat transcript are lost forever."
)


class VaultError(Exception):
    pass


class NotFound(VaultError):
    pass


class AlreadyExists(VaultError):
    pass


# --- body mutation helpers (light text-splice, not a full markdown AST) -----
# Read-only parsing (section_bounds, extract_section/attempts/dated_notes) lives in
# models.py, shared with dashboard/render.py — these three only ever mutate.


def _append_under_heading(body: str, heading: str, text: str) -> str:
    bounds = section_bounds(body, heading)
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
    bounds = section_bounds(body, heading)
    if bounds is None:
        return body.rstrip("\n") + f"\n\n## {heading}\n{text}\n"
    lines, idx, end = bounds
    new_lines = lines[: idx + 1] + [text.rstrip("\n"), ""] + lines[end:]
    return "\n".join(new_lines)


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
        elif bucket == "progress":
            content = render_index(BUCKET_TITLES[bucket], pages, sort_key=lambda p: p.week_end)
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
            return {"bucket": "research", "id": slug, "reminder": RESEARCH_LOGGING_REMINDER}

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
        status: Optional[ExperimentStatus] = None,
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

    # --- weekly progress -------------------------------------------------------

    def _log_lines_in_range(self, week_start: dt.date, week_end: dt.date) -> list[str]:
        log_path = self.root / "log.md"
        if not log_path.exists():
            return []
        lines = []
        for line in log_path.read_text().splitlines():
            if not line.strip() or line.startswith("<!--"):
                continue
            try:
                line_date = dt.date.fromisoformat(line[:10])
            except ValueError:
                continue
            if week_start <= line_date <= week_end:
                lines.append(line)
        return lines

    def _git_log_since(self, repo_path: str, week_start: dt.date, week_end: dt.date) -> list[str]:
        path = Path(repo_path).expanduser()
        if not path.exists():
            return [f"(repo path not found: {repo_path})"]
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(path),
                    "log",
                    f"--since={week_start.isoformat()}",
                    f"--until={(week_end + dt.timedelta(days=1)).isoformat()}",
                    "--oneline",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as e:
            return [f"(could not read git history at {repo_path}: {e})"]
        if result.returncode != 0:
            return [f"(not a git repo or git error at {repo_path}: {result.stderr.strip()})"]
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _scan_linked_code_repos(
        self, week_start: dt.date, week_end: dt.date, experiment_log_lines: list[str]
    ) -> list[dict]:
        activity = []
        for exp, _ in load_bucket(self._bucket_dir("experiments"), Experiment):
            if not exp.code_ref or not exp.code_ref.path:
                continue
            commits = self._git_log_since(exp.code_ref.path, week_start, week_end)
            # Deliberately keyed off log.md "attempt ... logged" entries, not exp.updated —
            # link_code/status-only updates also bump `updated`, which would otherwise mask
            # the exact gap (code changed, no attempt logged) this flag exists to catch.
            attempt_logged = any(
                f"[experiments:{exp.id}]" in line and "attempt" in line for line in experiment_log_lines
            )
            activity.append(
                {
                    "experiment_id": exp.id,
                    "repo_path": exp.code_ref.path,
                    "commits": commits,
                    "attempt_logged_this_week": attempt_logged,
                }
            )
        return activity

    def _touched_idents(self, log_lines: list[str], bucket: str) -> list[str]:
        """Distinct idents referenced in this bucket's log lines, in first-seen order."""
        pattern = re.compile(rf"\[{bucket}:([^\]]+)\]")
        seen: list[str] = []
        for line in log_lines:
            m = pattern.search(line)
            if m and m.group(1) not in seen:
                seen.append(m.group(1))
        return seen

    @staticmethod
    def _dated_block_in_range(block: str, heading_re: re.Pattern, week_start: dt.date, week_end: dt.date) -> bool:
        m = heading_re.search(block)
        if not m:
            return True  # can't tell the date — err toward including rather than silently dropping it
        return week_start <= dt.date.fromisoformat(m.group(1)) <= week_end

    _NOTE_DATE_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2})")
    _ATTEMPT_DATE_RE = re.compile(r"### Attempt \d+ — (\d{4}-\d{2}-\d{2})")

    @staticmethod
    def _demote_heading(block: str) -> str:
        """Notes/attempts carry their own `### ...` heading in the source page, but the weekly
        report nests them under a `### <topic/experiment>` heading of its own. Shift every ATX
        heading in the block down one level (not just the leading one) — a note written from a
        deep-research pass often brings its own internal `##`/`###` structure, and demoting only
        the first line would leave those at the wrong depth relative to the report's own headings."""
        return re.sub(r"^(#{1,5}) ", r"#\1 ", block, flags=re.MULTILINE)

    def _research_updates_this_week(
        self, idents: list[str], log_lines: list[str], week_start: dt.date, week_end: dt.date
    ) -> list[dict]:
        updates = []
        for ident in idents:
            path = self._page_path("research", ident)
            if not path.exists():
                continue
            topic, body = parse_page(path, ResearchTopic)
            is_new = any(f"[research:{ident}]" in line and "created" in line for line in log_lines)
            notes = [
                n
                for n in extract_dated_notes(body)
                if self._dated_block_in_range(n, self._NOTE_DATE_RE, week_start, week_end)
            ]
            updates.append(
                {"id": ident, "title": topic.title, "status": topic.status, "is_new": is_new, "notes": notes}
            )
        return updates

    def _experiment_updates_this_week(
        self, idents: list[str], log_lines: list[str], week_start: dt.date, week_end: dt.date
    ) -> list[dict]:
        updates = []
        for ident in idents:
            path = self._page_path("experiments", ident)
            if not path.exists():
                continue
            exp, body = parse_page(path, Experiment)
            is_new = any(f"[experiments:{ident}]" in line and "created" in line for line in log_lines)
            attempts = [
                a
                for a in extract_attempts(body)
                if self._dated_block_in_range(a, self._ATTEMPT_DATE_RE, week_start, week_end)
            ]
            updates.append(
                {
                    "id": ident,
                    "title": exp.title,
                    "status": exp.status,
                    "is_new": is_new,
                    "attempts": attempts,
                    "current_best": extract_section(body, "Current best"),
                    "blocked": exp.status == "blocked",
                    "unverified_backfill": exp.origin == "backfilled" and not exp.verified,
                }
            )
        return updates

    def _resource_updates_this_week(self, idents: list[str]) -> list[dict]:
        updates = []
        for ident in idents:
            path = self._page_path("resources", ident)
            if not path.exists():
                continue
            res, body = parse_page(path, Resource)
            snippet = body.strip().split("\n\n")[0][:200] if body.strip() else ""
            updates.append({"citekey": ident, "title": res.title, "snippet": snippet})
        return updates

    def _render_weekly_progress(
        self,
        week_start: dt.date,
        week_end: dt.date,
        research_updates: list[dict],
        experiment_updates: list[dict],
        resource_updates: list[dict],
        code_activity: list[dict],
    ) -> str:
        blocked = [e for e in experiment_updates if e["blocked"]]
        unverified = [e for e in experiment_updates if e["unverified_backfill"]]
        undocumented_code = [
            entry
            for entry in code_activity
            if not entry["attempt_logged_this_week"] and any(not c.startswith("(") for c in entry["commits"])
        ]

        lines = [
            f"# Weekly Progress: {week_start.isoformat()} to {week_end.isoformat()}",
            f"_Generated {dt.date.today().isoformat()}_",
            "",
        ]

        # --- Summary — the part meant to be read first, e.g. in a supervisor meeting ---
        lines.append("## Summary")
        new_topics = sum(1 for r in research_updates if r["is_new"])
        new_experiments = sum(1 for e in experiment_updates if e["is_new"])
        total_attempts = sum(len(e["attempts"]) for e in experiment_updates)
        lines.append(f"- **Research:** {len(research_updates)} topic(s) touched ({new_topics} new)")
        lines.append(
            f"- **Experiments:** {len(experiment_updates)} touched ({new_experiments} new, "
            f"{total_attempts} attempt(s) logged)"
        )
        lines.append(f"- **Resources:** {len(resource_updates)} added to the bibliography")
        if code_activity:
            commit_total = sum(len([c for c in e["commits"] if not c.startswith("(")]) for e in code_activity)
            lines.append(f"- **Code:** {commit_total} commit(s) across {len(code_activity)} linked repo(s)")
        else:
            lines.append("- **Code:** no linked repos yet")
        if blocked or unverified or undocumented_code:
            lines.append(
                f"- **Needs attention:** {len(blocked)} blocked, {len(unverified)} unverified backfill, "
                f"{len(undocumented_code)} undocumented code change(s) — see Flags below"
            )

        # --- Research ---
        lines.append("\n## Research")
        if not research_updates:
            lines.append("_none this week_")
        for r in research_updates:
            tag = " (new)" if r["is_new"] else ""
            lines.append(f"\n### {r['title']}{tag} — {r['status']} `{r['id']}`")
            if not r["notes"]:
                lines.append("_no new notes logged this week_")
            else:
                lines.extend(self._demote_heading(n) for n in r["notes"])

        # --- Experiments ---
        lines.append("\n## Experiments")
        if not experiment_updates:
            lines.append("_none this week_")
        for e in experiment_updates:
            tag = " (new)" if e["is_new"] else ""
            lines.append(f"\n### {e['title']}{tag} — {e['status']} `{e['id']}`")
            if e["current_best"]:
                lines.append(f"Current best: {e['current_best']}")
            if not e["attempts"]:
                lines.append("_no attempts logged this week_")
            else:
                lines.extend(self._demote_heading(a) for a in e["attempts"])

        # --- Resources ---
        lines.append("\n## Resources reviewed")
        if not resource_updates:
            lines.append("_none this week_")
        for r in resource_updates:
            snippet = f" — {r['snippet']}" if r["snippet"] else ""
            lines.append(f"- **{r['citekey']}** — {r['title']}{snippet}")

        # --- Code activity ---
        lines.append("\n## Code activity (linked repos)")
        if not code_activity:
            lines.append("_no experiments have linked code repos yet — use link_code_")
        for entry in code_activity:
            commits = [c for c in entry["commits"] if not c.startswith("(")]
            errors = [c for c in entry["commits"] if c.startswith("(")]
            lines.append(f"\n### {entry['experiment_id']} — {entry['repo_path']}")
            lines.extend(errors)
            if not commits and not errors:
                lines.append("_no commits this week_")
            else:
                lines.extend(f"- {c}" for c in commits)
            if commits and not entry["attempt_logged_this_week"]:
                lines.append(
                    f"**Note:** code changed this week but `update_experiment` was not called "
                    f"for {entry['experiment_id']} — consider logging what happened."
                )

        # --- Flags for discussion — the risks/blockers a supervisor would want called out ---
        lines.append("\n## Flags for discussion")
        flags = []
        for e in blocked:
            flags.append(f"- **BLOCKED:** {e['title']} (`{e['id']}`)")
        for e in unverified:
            flags.append(f"- **UNVERIFIED (backfilled):** {e['title']} (`{e['id']}`)")
        for entry in undocumented_code:
            flags.append(
                f"- **UNDOCUMENTED CODE:** {entry['experiment_id']} has commits this week not "
                f"reflected in a logged attempt"
            )
        lines.extend(flags or ["_none_"])

        lines.append("\n## Next steps")
        lines.append("_(fill in before the meeting)_")

        return "\n".join(lines) + "\n"

    def weekly_progress(
        self, since: Optional[dt.date] = None, until: Optional[dt.date] = None
    ) -> str:
        with self._lock:
            today = dt.date.today()
            week_end = until or today
            week_start = since or (week_end - dt.timedelta(days=7))

            log_lines = self._log_lines_in_range(week_start, week_end)
            research_updates = self._research_updates_this_week(
                self._touched_idents(log_lines, "research"), log_lines, week_start, week_end
            )
            experiment_updates = self._experiment_updates_this_week(
                self._touched_idents(log_lines, "experiments"), log_lines, week_start, week_end
            )
            resource_updates = self._resource_updates_this_week(self._touched_idents(log_lines, "resources"))
            experiment_lines = [line for line in log_lines if "[experiments:" in line]
            code_activity = self._scan_linked_code_repos(week_start, week_end, experiment_lines)

            body = self._render_weekly_progress(
                week_start, week_end, research_updates, experiment_updates, resource_updates, code_activity
            )

            report_id = week_end.isoformat()
            model = ProgressReport(
                id=report_id,
                title=f"Week ending {report_id}",
                week_start=week_start,
                week_end=week_end,
                created=today,
            )
            path = self._page_path("progress", report_id)
            atomic_write(path, dump_page(model, body))
            self._mutate("progress", report_id, f"report generated ({week_start}–{week_end})")
            return body

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
        if bucket == "progress":
            return self._context_for_progress(ident)
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
        aim = extract_section(topic_body, "Aim")
        if aim:
            lines += ["## Aim", aim, ""]
        background = extract_section(topic_body, "Background")
        if background:
            lines += ["## Background", background, ""]

        notes = extract_dated_notes(topic_body)
        if notes:
            lines.append("## Notes & Findings")
            shown_notes = notes[-3:]
            omitted_notes = len(notes) - len(shown_notes)
            if omitted_notes > 0:
                lines.append(f"_{omitted_notes} earlier note(s) omitted — ask for the full history if needed._")
            lines.extend(shown_notes)
            lines.append("")

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
            current_best = extract_section(body, "Current best")
            attempts = extract_attempts(body)
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

        lines.append(f"\n---\n**Reminder:** {RESEARCH_LOGGING_REMINDER}")

        return "\n".join(lines) + "\n"

    def _context_for_experiment(self, ident: str) -> str:
        exp, body = parse_page(self._page_path("experiments", ident), Experiment)
        resources = [
            (p, b) for p, b in load_bucket(self._bucket_dir("resources"), Resource) if p.citekey in exp.resource_refs
        ]
        recent_log = self._recent_log_lines({ident})

        lines = [f"# {exp.id}", f"_status: {exp.status}, updated {exp.updated}_", ""]
        aim = extract_section(body, "Aim")
        if aim:
            lines += ["## Aim", aim, ""]
        current_best = extract_section(body, "Current best")
        if current_best:
            lines += [f"Current best: {current_best}", ""]
        if exp.origin == "backfilled":
            lines.append(f"_origin: backfilled, verified: {exp.verified}_\n")

        attempts = extract_attempts(body)
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

    def _context_for_progress(self, ident: str) -> str:
        report, body = parse_page(self._page_path("progress", ident), ProgressReport)
        return f"# {report.title}\n\n{body}"
