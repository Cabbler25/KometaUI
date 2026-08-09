"""HTTP API."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ruamel.yaml.error import YAMLError

from .. import state
from ..config import settings
from ..services import validation
from ..services.workspace import ReadOnlyError, Workspace, WorkspaceError

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------------


class OpenWorkspaceRequest(BaseModel):
    path: str
    allow_writes: bool = False


class WriteRequest(BaseModel):
    text: str


class ValidateRequest(BaseModel):
    text: str
    filename: str = "untitled.yml"


class WritesRequest(BaseModel):
    allow: bool = Field(description="Unlock or re-lock writes for the open workspace")


# ----------------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------------


def _workspace() -> Workspace:
    try:
        return state.current()
    except WorkspaceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _node_to_dict(node: Any) -> dict[str, Any]:
    return {
        "name": node.name,
        "path": node.path,
        "isDir": node.is_dir,
        "size": node.size,
        "children": [_node_to_dict(c) for c in node.children],
    }


# ----------------------------------------------------------------------------------
# Status & assets
# ----------------------------------------------------------------------------------


@router.get("/status")
def status() -> dict[str, Any]:
    """Everything the UI needs to render its header and capability hints."""
    workspace = state.current_or_none()
    catalog = validation.load_catalog()
    engine = validation.kometa_engine_status()
    return {
        "workspace": (
            {
                "path": str(workspace.root),
                "name": workspace.root.name,
                "allowWrites": workspace.allow_writes,
            }
            if workspace
            else None
        ),
        "catalog": {
            "kometaVersion": catalog.get("kometa_version"),
            "builderCount": catalog.get("diagnostics", {}).get("all_builders_count"),
            "schemaGapCount": catalog.get("diagnostics", {}).get("schema_gap_count"),
            "loaded": bool(catalog),
        },
        "validationEngine": asdict(engine),
    }


@router.get("/catalog")
def catalog() -> dict[str, Any]:
    data = validation.load_catalog()
    if not data:
        raise HTTPException(status_code=503, detail="Catalog not generated. Run tools/generate_catalog.py.")
    return data


@router.get("/schemas/{name}")
def schema(name: str) -> dict[str, Any]:
    """Serve a vendored JSON schema, for monaco-yaml to consume."""
    if not name.endswith(".json") or "/" in name or "\\" in name or ".." in name:
        raise HTTPException(status_code=400, detail="Invalid schema name.")
    path = settings.schemas_dir / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"No such schema: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------------
# Workspace
# ----------------------------------------------------------------------------------


@router.post("/workspace/open")
def open_workspace(request: OpenWorkspaceRequest) -> dict[str, Any]:
    try:
        workspace = state.open_workspace(request.path, request.allow_writes)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "path": str(workspace.root),
        "name": workspace.root.name,
        "allowWrites": workspace.allow_writes,
        "configs": workspace.config_candidates(),
    }


@router.post("/workspace/writes")
def set_writes(request: WritesRequest) -> dict[str, Any]:
    workspace = _workspace()
    workspace.allow_writes = request.allow
    return {"allowWrites": workspace.allow_writes}


@router.get("/workspace/tree")
def tree() -> dict[str, Any]:
    return _node_to_dict(_workspace().tree())


@router.get("/workspace/configs")
def configs() -> dict[str, Any]:
    return {"configs": _workspace().config_candidates()}


@router.get("/workspace/references")
def references(config: str) -> dict[str, Any]:
    workspace = _workspace()
    try:
        refs = workspace.references(config)
    except (WorkspaceError, YAMLError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "config": config,
        "references": [
            {
                "kind": r.kind,
                "value": r.value,
                "library": r.library,
                "listKey": r.list_key,
                "resolved": r.resolved,
                "exists": r.exists,
                "templateVariables": r.template_variables,
            }
            for r in refs
        ],
        "missing": [r.value for r in refs if r.exists is False],
    }


# ----------------------------------------------------------------------------------
# Files
# ----------------------------------------------------------------------------------


@router.get("/files")
def read_file(path: str) -> dict[str, Any]:
    workspace = _workspace()
    try:
        text = workspace.read(path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = validation.validate_text(text, path, prefer_kometa_path=workspace.resolve(path))
    return {"path": path, "text": text, "validation": validation.result_to_dict(result)}


@router.put("/files")
def write_file(path: str, request: WriteRequest) -> dict[str, Any]:
    workspace = _workspace()
    try:
        backup = workspace.write(path, request.text, settings.backup_retention)
    except ReadOnlyError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except YAMLError as exc:
        # Never overwrite a good file with broken YAML.
        raise HTTPException(status_code=400, detail=f"Refusing to save invalid YAML: {exc}") from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    result = validation.validate_text(request.text, path, prefer_kometa_path=workspace.resolve(path))
    return {"path": path, "backup": backup, "validation": validation.result_to_dict(result)}


# ----------------------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------------------


@router.post("/validate")
def validate(request: ValidateRequest) -> dict[str, Any]:
    """Validate in-editor text that may not have been saved yet."""
    result = validation.validate_text(request.text, request.filename)
    return validation.result_to_dict(result)


@router.post("/validate/all")
def validate_all() -> dict[str, Any]:
    """Validate every YAML file in the workspace."""
    workspace = _workspace()
    results = []

    def walk(node: Any) -> None:
        for child in node.children:
            if child.is_dir:
                walk(child)
            else:
                try:
                    text = workspace.read(child.path)
                except (WorkspaceError, UnicodeDecodeError) as exc:
                    results.append(
                        {
                            "file": child.path,
                            "kind": None,
                            "engine": "syntax",
                            "ok": False,
                            "findings": [{"message": str(exc), "severity": "error", "engine": "syntax"}],
                        }
                    )
                    continue
                result = validation.validate_text(
                    text, child.path, prefer_kometa_path=Path(workspace.resolve(child.path))
                )
                results.append(validation.result_to_dict(result))

    walk(workspace.tree())
    return {
        "results": results,
        "summary": {
            "files": len(results),
            "withErrors": sum(1 for r in results if not r["ok"]),
            "findings": sum(len(r["findings"]) for r in results),
        },
    }
