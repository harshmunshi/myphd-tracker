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
log.md                          single root append-only log, greppable, tags every line by bucket+ref
dashboard/                      generated static HTML site (build_dashboard output) — never hand-edit
server/                         the MCP server implementation
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
created: YYYY-MM-DD
```
Body: freeform annotation — why this paper matters, what it's cited for.

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

## Non-goals

- No full-text-search-at-scale infrastructure (SQLite FTS5, embeddings). This is a solo-user vault
  that will realistically hold a few hundred pages at most — grep/ripgrep over markdown is enough.
  Do not build this unless the vault has actually grown large enough to need it.
- No copying source code or datasets into this repo. Reference, don't vendor.
- No hand-editing any generated file (`*/index.md`, anything under `dashboard/`).
