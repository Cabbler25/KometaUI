"""KometaUI backend entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import state
from .api.routes import router
from .config import settings


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
