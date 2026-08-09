"""Validation of Kometa YAML files.

Two engines, in preference order:

**Kometa's own validator** (when a Kometa checkout is configured). Authoritative, and
catches things a schema cannot -- cross-file references, deprecated keys, structural rules
expressed in Python.

**Bundled JSON Schema** (always available). Kometa's schemas, vendored at build time and
checked with ``jsonschema``. Good coverage, no Kometa checkout required.

The bundled path needs one correction. Kometa's schemas are acknowledged to be incomplete
(``json-schema/README.md``, "Known Limitations"), and the catalog generator measures the
gap precisely: 20 builders exist in Kometa's Python but not in ``collection-schema.json``
-- ``trakt_watchlist``, ``mal_genre``, the ``trakt_*_weekly`` chart variants and so on.
Those are valid in a real config, so flagging them as unknown properties would be a false
positive. :func:`_is_schema_gap` suppresses exactly those.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft7Validator
from ruamel.yaml.error import YAMLError

from ..config import settings
from .yaml_doc import ParseError, loads

Severity = Literal["error", "warning"]
Engine = Literal["kometa", "schema", "syntax"]

SCHEMA_BY_KIND = {
    "config": "config-schema.json",
    "collection": "collection-schema.json",
    "overlay": "overlay-schema.json",
    "metadata": "metadata-schema.json",
    "playlist": "playlist-schema.json",
    "template": "template-schema.json",
}


@dataclass
class Finding:
    """A single validation result, positioned in the file where possible."""

    message: str
    severity: Severity = "error"
    engine: Engine = "schema"
    path: str = ""  # dotted location within the document, e.g. "libraries.Movies"
    line: int | None = None
    column: int | None = None


@dataclass
class FileResult:
    file: str
    kind: str | None
    findings: list[Finding]
    engine: Engine

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)


# ----------------------------------------------------------------------------------
# Assets
# ----------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    if not settings.catalog_path.exists():
        return {}
    return json.loads(settings.catalog_path.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def load_schema(filename: str) -> dict[str, Any] | None:
    path = settings.schemas_dir / filename
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def schema_gap_builders() -> frozenset[str]:
    """Builders Kometa accepts but its own schema omits."""
    return frozenset(load_catalog().get("builders_missing_from_schema", []))


def detect_kind(data: Any) -> str | None:
    """Infer which Kometa file type a parsed document is.

    Mirrors ``modules/validator.py::detect_schema_type``. Order matters: a config is
    identified by ``libraries``/``plex``, and only files lacking those are considered as
    definition files. ``templates`` is checked last because collection, overlay, and
    playlist files may all carry it alongside their primary key.
    """
    if not isinstance(data, dict):
        return None
    if any(key in data for key in ("libraries", "plex", "tmdb")):
        return "config"
    if "collections" in data or "dynamic_collections" in data:
        return "collection"
    if "overlays" in data:
        return "overlay"
    if "playlists" in data:
        return "playlist"
    if "metadata" in data:
        return "metadata"
    if "templates" in data or "external_templates" in data:
        return "template"
    return None


# ----------------------------------------------------------------------------------
# Tier C: Kometa's own validator
# ----------------------------------------------------------------------------------


@dataclass
class EngineStatus:
    """Whether Kometa's validator is usable, and why not when it isn't."""

    kometa_available: bool
    kometa_source: str | None = None
    kometa_version: str | None = None
    detail: str | None = None


@lru_cache(maxsize=1)
def kometa_engine_status() -> EngineStatus:
    """Probe for Kometa's validator without letting a failure break the app."""
    source = settings.kometa_source_path
    if source is None:
        return EngineStatus(False, detail="No Kometa source path configured.")
    source = Path(source).expanduser().resolve()
    if not (source / "modules" / "validator.py").exists():
        return EngineStatus(False, str(source), detail=f"No modules/validator.py under {source}.")

    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    try:
        from modules.validator import FileSetValidator  # noqa: F401
    except ImportError as exc:
        # Kometa's validator pulls in modules.util, which imports several third-party
        # packages. Naming the missing one turns a dead end into an actionable message.
        return EngineStatus(
            False,
            str(source),
            detail=(
                f"Kometa's validator needs a dependency that isn't installed: {exc.name}. "
                f"Install Kometa's requirements to enable it: pip install -r {source / 'requirements.txt'}"
            ),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return EngineStatus(False, str(source), detail=f"Import failed: {exc!r}")

    version_file = source / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else None
    return EngineStatus(True, str(source), version)


def _validate_with_kometa(path: Path) -> list[Finding] | None:
    """Run Kometa's validator over one file.

    Uses ``_process_file`` rather than the public ``validate()``. ``validate()`` calls
    ``_print_report()``, which writes through ``modules.util.logger`` -- and that logger is
    ``None`` until Kometa's CLI initialises it, so calling the public method here raises
    ``AttributeError``. ``_process_file`` touches no logger and returns the structured
    result directly.
    """
    status = kometa_engine_status()
    if not status.kometa_available or status.kometa_source is None:
        return None
    try:
        from modules.validator import FileSetValidator

        validator = FileSetValidator([str(path)], str(Path(status.kometa_source) / "json-schema"))
        result = validator._process_file(str(path))
    except Exception:
        return None

    if result.get("skipped"):
        return []
    return [
        Finding(message=str(message), severity="error", engine="kometa")
        for message in result.get("errors", [])
    ]


# ----------------------------------------------------------------------------------
# Tier A: bundled JSON Schema
# ----------------------------------------------------------------------------------


# jsonschema renders these as:
#   Additional properties are not allowed ('a', 'b' were unexpected)
_UNEXPECTED_PROPERTY = re.compile(r"'([^']+)'")


def _is_schema_gap(error: Any) -> bool:
    """True when an error only fires because Kometa's schema omits a valid builder.

    Suppression is deliberately narrow. It applies only when *every* property the error
    names is one the catalog measured as missing from the schema; a mix of a known gap and
    a genuine typo still surfaces.
    """
    if error.validator != "additionalProperties":
        return False
    gaps = schema_gap_builders()
    if not gaps:
        return False
    named = set(_UNEXPECTED_PROPERTY.findall(error.message))
    return bool(named) and named.issubset(gaps)


def _location(error: Any) -> str:
    return ".".join(str(part) for part in error.absolute_path)


def _validate_with_schema(data: Any, kind: str) -> list[Finding]:
    schema = load_schema(SCHEMA_BY_KIND[kind])
    if schema is None:
        return [Finding(f"No bundled schema for '{kind}' files.", severity="warning")]

    findings: list[Finding] = []
    for error in sorted(Draft7Validator(schema).iter_errors(data), key=lambda e: list(e.absolute_path)):
        if _is_schema_gap(error):
            continue
        findings.append(
            Finding(
                message=error.message,
                severity="error",
                engine="schema",
                path=_location(error),
            )
        )
    return findings


# ----------------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------------


def validate_text(text: str, filename: str, prefer_kometa_path: Path | None = None) -> FileResult:
    """Validate YAML source, returning findings positioned for the editor."""
    try:
        data = loads(text)
    except YAMLError as exc:
        err = ParseError.from_yaml_error(exc)
        return FileResult(
            file=filename,
            kind=None,
            engine="syntax",
            findings=[Finding(err.message, engine="syntax", line=err.line, column=err.column)],
        )

    if data is None:
        return FileResult(filename, None, [Finding("File is empty.", severity="warning")], "schema")

    kind = detect_kind(data)
    if kind is None:
        return FileResult(
            filename,
            None,
            [
                Finding(
                    "Could not tell what kind of Kometa file this is. Expected one of: "
                    "libraries/plex (config), collections, overlays, metadata, playlists, templates.",
                    severity="warning",
                )
            ],
            "schema",
        )

    # Prefer Kometa's own verdict when we can get it.
    if prefer_kometa_path is not None:
        kometa_findings = _validate_with_kometa(prefer_kometa_path)
        if kometa_findings is not None:
            return FileResult(filename, kind, kometa_findings, "kometa")

    return FileResult(filename, kind, _validate_with_schema(data, kind), "schema")


def result_to_dict(result: FileResult) -> dict[str, Any]:
    return {
        "file": result.file,
        "kind": result.kind,
        "engine": result.engine,
        "ok": result.ok,
        "findings": [asdict(f) for f in result.findings],
    }
