"""Application settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ASSETS_DIR = Path(__file__).resolve().parent.parent / "kometa_assets"


class Settings(BaseSettings):
    """Runtime configuration, overridable by environment or a local .env file."""

    model_config = SettingsConfigDict(env_prefix="KOMETAUI_", env_file=".env", extra="ignore")

    # Path to a Kometa checkout. When present, validation uses Kometa's own validator
    # instead of the bundled JSON schemas. Optional -- the app works without it.
    kometa_source_path: Path | None = None

    # Directory the user is editing. Set at runtime by opening a workspace.
    workspace_path: Path | None = None

    # Writes are opt-in. A freshly opened workspace is read-only until the user
    # explicitly unlocks it, so pointing the app at a real config directory can never
    # modify it by accident.
    allow_writes: bool = False

    # Number of timestamped backups to retain per file.
    backup_retention: int = 20

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def schemas_dir(self) -> Path:
        return ASSETS_DIR / "schemas"

    @property
    def catalog_path(self) -> Path:
        return ASSETS_DIR / "catalog.json"


settings = Settings()
