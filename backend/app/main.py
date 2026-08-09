"""KometaUI backend entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import state
from .api.routes import router
from .config import settings

# Present only in the container image, where the built frontend is copied alongside the
# backend. In development Vite serves the frontend and proxies here instead.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.bootstrap()
    yield


app = FastAPI(
    title="KometaUI",
    description="Edit and validate Kometa configuration. Never runs Kometa; never writes to Plex.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if STATIC_DIR.is_dir():
    # Hashed assets are safe to cache hard; index.html is not, so it is served by the
    # catch-all below rather than by StaticFiles.
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        """Serve the single-page app.

        Registered last so it never shadows /api or /health. A real file is returned when
        one exists (favicon and friends); everything else gets index.html so client-side
        routes survive a refresh.
        """
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and STATIC_DIR in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
