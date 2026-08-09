# KometaUI

A web UI for [Kometa](https://github.com/Kometa-Team/Kometa) — browse, edit, and validate your
Kometa YAML configuration without hand-writing it.

**Files-only by design.** KometaUI never runs Kometa and never touches your Plex server's
metadata. It authors and validates YAML; your existing Kometa run consumes it.

## Status

Early development. See `docs/ARCHITECTURE.md` for the design.

| Milestone | Contents | State |
|---|---|---|
| M0 | Repo scaffold, catalog generator, vendored schemas | in progress |
| M1 | File tree, schema-aware YAML editor, validation | |
| M2 | Schema-driven config forms | |
| M3 | Plex read-only + setup wizard | |
| M4 | Defaults browser | |
| M5 | Collection/overlay authoring | |
| M6 | Collection preview, packaging | |

## Layout

```
backend/    FastAPI service (Python 3.12+)
frontend/   React + TypeScript + Vite
tools/      Build-time extraction from Kometa source
docs/       Architecture notes
```

## Development

Requires Python 3.12+ and Node 20+.

```bash
# Backend
cd backend
python -m venv .venv && .venv/Scripts/activate   # Windows
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

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
