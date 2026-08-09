"""Shared fixtures.

Tests that need real-world YAML read from a Kometa checkout. It is optional: set
``KOMETAUI_KOMETA_SOURCE_PATH`` (or keep a checkout at the default location) to run them,
otherwise they skip rather than fail.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

DEFAULT_KOMETA_SOURCE = Path(r"C:\Projects\KometaSource")


def _kometa_source() -> Path | None:
    configured = os.environ.get("KOMETAUI_KOMETA_SOURCE_PATH")
    candidate = Path(configured) if configured else DEFAULT_KOMETA_SOURCE
    return candidate if (candidate / "modules" / "validator.py").exists() else None


@pytest.fixture(scope="session")
def kometa_source() -> Path:
    source = _kometa_source()
    if source is None:
        pytest.skip("No Kometa checkout available")
    return source


@pytest.fixture(scope="session")
def real_yaml_files(kometa_source: Path) -> list[Path]:
    """Every YAML file Kometa ships: 90 defaults plus the prototype configs.

    Broad, adversarial, and authored by the Kometa team rather than by us -- the best
    corpus available for checking that we do not mangle real files.
    """
    files = sorted(kometa_source.glob("defaults/**/*.yml"))
    for extra in ("kitchen_sink_config.yml", "prototype_config.yml", "prototype_comprehensive.yml"):
        candidate = kometa_source / "json-schema" / extra
        if candidate.exists():
            files.append(candidate)
    return files


@pytest.fixture
def workspace(tmp_path: Path):
    """A writable throwaway workspace."""
    from app.services.workspace import Workspace

    (tmp_path / "config.yml").write_text(
        "libraries:\n  Movies:\n    collection_files:\n      - file: config/movies.yml\n",
        encoding="utf-8",
    )
    return Workspace(tmp_path, allow_writes=True)
