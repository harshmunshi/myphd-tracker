# myphd_tracker — schema & workflow

This repo is a lab journal, not a codebase. It replaces ad-hoc notes with a structured,
LLM-maintained wiki following Andrej Karpathy's "LLM Wiki" pattern: an immutable **sources**
layer (papers, external code repos — referenced, never copied in), an LLM-maintained **wiki**
layer (this repo's markdown pages), and this file as the **schema** layer.

Read this file before creating or editing anything here. Its job is to make you a disciplined
wiki maintainer, not a generic chatbot: follow the conventions below exactly, don't improvise
new frontmatter fields or folder structures ad hoc.

## Layout

```
research/<topic-slug>.md       one page per research idea/topic
research/index.md               GENERATED — full rescan + rewrite, never hand-patched
experiments/<experiment-id>.md  one page per experiment
experiments/index.md            GENERATED
resources/<citekey>.md          one page per paper/resource
resources/index.md              GENERATED — this IS the bibliography, there is no separate file
progress/<week-ending-date>.md  one page per weekly digest, id = ISO date of the week's last day
progress/index.md               GENERATED
log.md                          single root append-only log, greppable, tags every line by bucket+ref
dashboard/                      generated static HTML site (build_dashboard output) — never hand-edit
server/                         the MCP server implementation:
  app.py                          shared state (FastMCP instance, Vault, VAULT_ROOT) — no
                                   decorators here, just what tools/prompts/resources import
  tools.py                        @mcp.tool() — atomic actions, one unit of vault work each
  prompts.py                      @mcp.prompt() — composed, multi-step workflows (see below)
  resources.py                    @mcp.resource() — read-only, client-pulled data
  server.py                       thin entrypoint: imports the three above to register them
  storage.py                      Vault: all I/O, locking, reindexing, alias resolution
  models.py                       pure frontmatter schemas + parse/dump/render helpers
  dashboard/                      static-site generator (see Visualizing below)
```

**Golden rule: `index.md` files are caches, never sources of truth.** They are always produced by
fully rescanning a bucket's pages and rewriting the file from scratch — never incrementally
patched from N different call sites. If you ever find yourself tempted to append a line to an
`index.md` by hand, stop — call the reindex tool/function instead. This is what keeps the index
from silently drifting out of sync with the pages.

Code and data are **never copied into this repo**. Experiment pages only reference an external
repo/path via `code_ref` / `data_ref`. This repo owns the narrative and the numbers, not the code.

## Page types & frontmatter

### Research topic (`research/<slug>.md`)
```yaml
id: <slug>
title: <string>
aliases: []            # required — this is what lets "let's work on research A" resolve
status: active | paused | abandoned | published
origin: live | backfilled
created: YYYY-MM-DD
updated: YYYY-MM-DD
```
Body: freeform `## Aim`, `## Background`, running brainstorm notes appended with a `### <date>`
heading per session (never rewrite an earlier session's notes, only append a new one).

### Experiment (`experiments/<experiment-id>.md`)
Id format: `YYYY-MM-DD-<topic-slug>-<short-title-slug>` — chronological and greppable.

```yaml
id: <experiment-id>
title: <string>
aliases: []
status: planned | running | blocked | done | failed
research_refs: [<topic-slug>, ...]
resource_refs: [<citekey>, ...]
code_ref: {path: <str>, remote: <str|null>, commit: <str|null>, dirty: <bool>, entrypoint: <str|null>}
data_ref: {path: <str|null>, url: <str|null>}
supersedes: <experiment-id|null>
superseded_by: <experiment-id|null>
origin: live | backfilled
verified: <bool>        # for origin=backfilled: was this reconstructed from a real eval artifact
                         # (true) or from a stale log/README claim (false)? Never conflate the two.
tags: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
latest_attempt: <int>
```

Body grammar — **most experiments fail before they work; the page must show that history, not
just the final state**:

```markdown
## Aim
...

## Setup (current)
...

### Attempt 1 — YYYY-MM-DD (failed)
​```metrics
- {name: val_ppl, value: 14.2, split: val, attempt: 1}
​```
Notes: ...

### Attempt 2 — YYYY-MM-DD (running)
Notes: ...

## Current best
Attempt N — <metric summary>.
```

Rules for updating an experiment page:
- **Never edit a previous `### Attempt N` block.** Append a new attempt block, or append a line
  to the notes of the currently-open (latest) attempt.
- The `metrics` fenced block always uses the fixed shape `{name, value, split, attempt}` — this is
  what lets the dashboard plot trends without parsing prose. Don't invent a different shape.
- `## Current best` is the one section that gets rewritten in place on every update — it is a
  pointer to the best attempt so far, not a history.
- Bump `updated` and `latest_attempt` in frontmatter on every update.

### Resource (`resources/<citekey>.md`)
```yaml
citekey: <str>
title: <string>
authors: []
tags: []
origin: live | backfilled
path_or_url: <str|null>
research_refs: [<topic-slug>, ...]   # which research topic(s) this belongs to — required for a
                                      # resource to show up on that topic's own page/get_context
                                      # rather than only the global, undifferentiated bibliography
created: YYYY-MM-DD
```
Body: freeform annotation — why this paper matters, what it's cited for.

**The bibliography is per-idea, not one flat list.** Every resource found while researching a
specific topic must be linked to it via `research_refs` — pass `research_ref` to `add_resource`
at creation time, or call `link_resource` afterward to attach it retroactively (a resource can
belong to more than one topic; linking is additive, never replaces existing links). A resource
with no `research_refs` still exists but only shows up in the bibliography's "Unlinked" group —
that's a sign it was added without tying it to the topic being worked on, not a valid end state
for anything found during an actual investigation.

### Progress report (`progress/<week-ending-date>.md`)
```yaml
id: <week-ending-date>   # e.g. 2026-07-21 — also the filename
title: <string>          # e.g. "Week ending 2026-07-21"
week_start: YYYY-MM-DD
week_end: YYYY-MM-DD
created: YYYY-MM-DD
```
Unlike every other page type, this one is **regenerable, not append-only** — calling
`weekly_progress` again for the same week overwrites its report rather than accumulating history.
It's a digest of `log.md` activity across all buckets for the date range, plus raw `git log`
activity in any repo linked via `code_ref`, so real coding work shows up here even when
`update_experiment` wasn't called for every run — the tool flags that gap explicitly rather than
silently missing it.

## Naming

- Slugs: lowercase, hyphen-separated, no special characters (`sparse-attention`, not `Sparse Attention!`).
- Experiment ids collide if two experiments on the same topic+day pick the same short title —
  disambiguate with a suffix (`-2`, `-3`) rather than silently overwriting.

## log.md

One line per event, oldest first, never edited or deleted, tagged so a single bucket's history is
just a `grep`:
```
2026-07-21T12:00Z [research:sparse-attention] created — status active
2026-07-21T13:10Z [experiment:2026-07-21-sparse-attention-baseline] attempt 2 logged — status running
```

## Workflows

### Scene A — starting from scratch
1. Brainstorming with Claude (Desktop or CLI) → create/update a page under `research/` (aim,
   background, running notes). Nothing goes under `experiments/` yet.

   **Disambiguation:** "I want to do research on X" / "let's look into X" is ambiguous — it can
   mean "track this as a topic" (call `track_research_topic`) and/or "go investigate X for me"
   (a literature search / deep-research pass). These are different, non-exclusive actions. Track
   the topic first, regardless of whether a separate investigation also happens — don't let the
   phrase get absorbed entirely into a research/deep-research skill and skip the tracker.

   **Do not stop there.** If an investigation *does* happen — a deep-research pass, a web search,
   reading a paper — the tracker is worthless unless the findings make it back in. Summarize what
   was learned and call `log_research_note` on the topic before ending the turn. A page that only
   ever says "started thinking about X" and never records what was found is not a lab journal,
   it's a to-do list. `get_context` only surfaces what was actually logged this way — findings
   that stay in the chat transcript are invisible to every future session.

   **Any specific paper/dataset that surfaces during that investigation also needs `add_resource`**
   (with `research_ref` set to the topic), not just a mention inside the `log_research_note` prose.
   `log_research_note` captures the synthesis; `add_resource` is what makes each source a real,
   citable bibliography entry scoped to that topic instead of prose the citekey never exists for.
2. When the user moves to actually writing/running code (Claude Code or any tool) → create an
   `experiments/` page referencing the research topic via `research_refs`. From here on, every
   run/attempt — success or failure — gets appended as a new `### Attempt N` block. Do not wait
   for something to "work" before logging it; log the failed attempts too, that's the point.

### Scene B — existing codebase
When asked to get up to speed on an already-functional repo:
1. Read the repo's README, recent git log, and any eval-output artifacts (result files, logs,
   notebooks) — do **not** copy any of this into the vault, just read it to understand it.
2. Backfill one or more `experiments/` pages summarizing what's already been done, with
   `origin: backfilled`.
3. Set `verified: true` only if a claim traces to an actual eval-output artifact you read;
   `verified: false` if it's reconstructed from a README/commit-message claim you couldn't confirm
   against real output. Never mark backfilled work `verified: true` on a guess.
4. Use `link_code` to point the experiment at the repo (path/remote/commit/dirty/entrypoint) —
   record `dirty: true` if the working tree had uncommitted changes when you looked.
5. Continue forward exactly as in Scene A step 2 from there.

### Resuming — "let's work on research A"
Resolve the reference against topic/experiment `aliases`, then pull: the topic's aim/background,
each linked experiment's status + current best + last 1-2 attempts, linked resources with their
annotation snippets, and recent relevant `log.md` lines. Surface any `blocked` or
`origin: backfilled, verified: false` items first — those are what the user needs to see before
diving back in.

### Weekly progress
When asked for a status update, a weekly summary, or "what did I get done," call `weekly_progress`
rather than trying to reconstruct it from memory — it aggregates `log.md` across all buckets for
the date range AND inspects `git log` in every repo linked via `code_ref`, so it reflects research,
experiments, *and* raw coding activity even when the user never explicitly logged a given run.
Read its output for any "not reflected" flag (code changed but no `update_experiment` call this
week) and proactively offer to log those runs properly rather than letting them stay undocumented.

### Visualizing — `build_dashboard`
A deterministic static-site generator (`server/dashboard/render.py`), not LLM-authored: it parses
frontmatter/markdown directly and renders an overview page, one experiment-timeline page per
research topic (status pill, attempt count, current best, a metric-trend sparkline), and a
bibliography page, all under `dashboard/` with zero external dependencies — no CDN scripts, no
client-side charting library, so it opens fully offline via `file://`. It's a full rebuild every
call (stale pages for renamed/removed topics are deleted, not left behind), so call it any time —
never hand-edit anything under `dashboard/`. Reach for this when the user wants to browse visually
rather than read `get_context` in chat.

## MCP primitives: tools vs. prompts vs. resources

Three different jobs, three different primitives — don't blur them:

- **Tools** (`server/tools.py`) are atomic: each does exactly one unit of vault work (create a
  topic, log a note, start an experiment, add a resource...) and nothing conditional beyond it.
  The one deliberate exception is that every mutating tool also rebuilds `dashboard/` as part of
  the same call — that's kept as a code-guaranteed side effect (not a prompt) specifically
  because relying on an LLM to remember a follow-up call was the exact bug this fixed (stale
  bibliography/experiment timeline after a real mutation).
- **Prompts** (`server/prompts.py`) are where multi-step composition and judgment calls live. A
  prompt is *not* a way to execute tool calls server-side — it's a template that returns text,
  which the client inserts into the conversation for the calling LLM to then act on by choosing
  which tools to call. Current prompts:
  - `start_or_resume_research(topic)` — checks the vault (via `Vault.find_similar_topics`, stdlib
    `difflib`, no search infra) for an existing match before creating a duplicate topic, working
    correctly regardless of which chat session raised the question.
  - `log_code_run(research_ref)` — code activity defaults to an experiment attempt
    (`start_experiment`/`update_experiment`), never a `log_research_note` prose entry; lists
    existing experiments (via `Vault.list_experiments_for_topic`) so the LLM can tell new-vs-
    continuing instead of guessing.
  - `wrap_up_session()` — a session-end checklist (via `Vault.session_flags`): blocked
    experiments, unverified backfills, resources never linked to a topic.
- **Resources** (`server/resources.py`) are read-only, client-pulled data, namespaced under
  `myphd://` to avoid confusion with the vault's own "Resource" (bibliography) model — an
  unrelated concept. `myphd://topics` lists every tracked topic; `myphd://topics/{topic_id}`
  mirrors the `get_context` tool's output. The `get_context` tool stays as-is alongside it —
  tools are reliably callable by an LLM in an agentic loop regardless of client, resource-read
  support varies by client, so this is additive, not a replacement.

## Non-goals

- No full-text-search-at-scale infrastructure (SQLite FTS5, embeddings). This is a solo-user vault
  that will realistically hold a few hundred pages at most — grep/ripgrep over markdown is enough.
  Do not build this unless the vault has actually grown large enough to need it.
- No copying source code or datasets into this repo. Reference, don't vendor.
- No hand-editing any generated file (`*/index.md`, anything under `dashboard/`).
