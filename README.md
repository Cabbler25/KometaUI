# KometaUI

A web UI for [Kometa](https://github.com/Kometa-Team/Kometa) — browse, edit, and validate your
Kometa YAML configuration without hand-writing it.

**Files-only by design.** KometaUI never runs Kometa and never touches your Plex server's
metadata. It authors and validates YAML; your existing Kometa run consumes it.

## Status

Early development. See `docs/ARCHITECTURE.md` for the design.

| Milestone | Contents | State |
|---|---|---|
| M0 | Repo scaffold, catalog generator, vendored schemas | done |
| M1 | File tree, schema-aware YAML editor, validation | done |
| M2 | Surgical YAML editing, schema-derived forms | done |
| M4 | Defaults browser, New Collection builder | done |
| M3 | Plex read-only, connections, library discovery | done |
| M5 | Overlay authoring, settings and operations forms | done |
| M6 | Collection preview, Windows packaging | done |

## Creating things without writing YAML

Two surfaces cover most of what people build:

- **Defaults** — browse Kometa's 57 collection and 24 overlay defaults grouped by
  category, enable them per library, and tune their options through forms generated from
  each default's own schema definition.
- **+ Collection** — pick from 137 builders grouped by service and filtered to the
  library type, fill in a generated form, and watch the YAML preview update before it is
  written.

Neither has hand-written per-field code. The backend turns Kometa's JSON Schema into
field descriptors and the frontend renders one component per control type, so the forms
track Kometa releases instead of drifting from them.

Edits are applied surgically — the affected lines are spliced, never the whole document —
so comments, key order, and formatting survive. Every write makes a timestamped backup.

## Connections

The **Connections** tab signs in to Plex with a linking code rather than making you dig
`X-Plex-Token` out of an item's XML, lists the servers your account can reach (offering
direct addresses before relays), and reads your real library names and types. Library
names must match `config.yml` exactly — a typo is a silent no-op in Kometa — so they are
written for you, and their types let the collection builder hide builders that cannot
apply.

Plex access is **read-only**: KometaUI lists libraries and reports versions, and writes
only to your own config files.

The Plex token is held by the backend, not the browser — it survives a page reload, and it
is never sent to the client. If your config already contains a working token, the session
adopts it and you are never asked to sign in.

## Previewing a collection

Collections built from `plex_all`, `plex_search`, or plain `filters` can be previewed
against the live library before Kometa ever runs, closing its slowest feedback loop. The
translation from Kometa's filter syntax to PlexAPI's is driven by the tables lifted out of
`modules/plex.py`, so the vocabulary stays in step with Kometa.

Remote builders (`tmdb_*`, `trakt_*`, `imdb_*`, …) are **not** previewable — each needs its
own credentials, rate-limit handling, and Kometa's ID-mapping cache. Rather than previewing
a subset and letting the count mislead, the UI says which builders it cannot resolve, and
flags any individual condition it had to skip.

## Layout

```
backend/    FastAPI service (Python 3.12+)
frontend/   React + TypeScript + Vite
tools/      Build-time extraction from Kometa source
docs/       Architecture notes
```

## Setup (Windows)

Requires Python 3.12+ and Node 20+ on PATH.

```powershell
# One-time. -KometaSource is optional but recommended: it enables validation with
# Kometa's own validator instead of the bundled schemas.
.\setup.ps1 -KometaSource C:\Projects\KometaSource

# Start it. Opens http://127.0.0.1:8770
.\start.ps1 -Workspace "C:\Users\me\Plex Meta Manager\Plex-Meta-Manager\config"
```

`start.ps1` runs a single process serving both the API and the built UI. Writes are
locked until you unlock them in the header, so pointing it at a live config cannot
change anything by accident; pass `-AllowWrites` to skip that step.

Kometa itself is only ever **read** — for its schemas, validator, and examples.
KometaUI never runs it.

### Working on the code

```powershell
cd backend;  .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8770
cd frontend; npm run dev     # http://localhost:5173, proxies /api to 8770
```

> **Known limitation — in-editor schema hints need a production build.**
> Monaco's YAML language service runs in a web worker, and Vite's dev server cannot
> currently construct that worker for `monaco-yaml` (`Could not create web worker(s)`),
> so completion, hover docs, and inline squiggles are missing under `npm run dev`.
> Everything else — the file tree, saving, and validation via the Problems panel — works
> normally, because that validation runs on the backend rather than in the worker.
> To exercise the full editor, use a production build:
>
> ```bash
> npm run build && npm run preview   # http://localhost:5174
> ```
>
> `src/lib/monaco.ts` documents the two related Vite/monaco resolution problems that are
> already fixed, and `vite.config.ts` explains which dependencies must and must not be
> pre-bundled. Worth retrying after a `monaco-yaml` release.

### Regenerating the Kometa catalog

KometaUI derives its knowledge of Kometa's builders, filters, and defaults from Kometa's own
source, rather than duplicating it by hand. After upgrading Kometa, re-run:

```bash
python tools/generate_catalog.py --kometa-source /path/to/Kometa
```

This rewrites `backend/kometa_assets/catalog.json` and refreshes the vendored JSON schemas.
The diff is intended to be reviewed in git.

## Safety

- Every write creates a timestamped backup alongside the original.
- All file access is confined to the opened workspace directory (path-traversal guarded).
- Credentials in your config are never logged and are redacted from API error messages.
