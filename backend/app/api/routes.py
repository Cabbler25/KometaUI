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
from ..services import documents, plex_client, schema_form, validation
from ..services.workspace import ReadOnlyError, Workspace, WorkspaceError
from ..services.yaml_edit import EditError

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
                "relative": r.relative,
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


# ----------------------------------------------------------------------------------
# Forms
# ----------------------------------------------------------------------------------


# NOTE: `/forms/builder/{builder}` must be declared before the generic
# `/forms/{schema_name}/{definition}` below. Both have two path segments, and FastAPI
# matches in declaration order -- with the generic route first, every builder request was
# read as schema "builder" and 404'd.
@router.get("/forms/builder/{builder}")
def builder_form(builder: str) -> dict[str, Any]:
    """The form for a single collection builder, plus its service grouping."""
    schema = validation.load_schema("collection-schema.json")
    catalog = validation.load_catalog()
    if schema is None:
        raise HTTPException(status_code=503, detail="Collection schema unavailable")

    definition = schema.get("definitions", {}).get("collection-definition", {})
    node = definition.get("properties", {}).get(builder)

    service = next(
        (meta["label"] for meta in catalog.get("services", {}).values() if builder in meta["builders"]),
        None,
    )
    if node is None and service is None:
        raise HTTPException(status_code=404, detail=f"Unknown builder: {builder}")

    # Builders in the schema gap have no node; they still take a value, so offer raw YAML.
    field = (
        schema_form.build_field(schema, builder, node)
        if node is not None
        else schema_form.FormField(
            name=builder,
            label=builder.replace("_", " ").capitalize(),
            control="yaml",
            description="Kometa accepts this builder but its JSON schema does not describe it.",
        )
    )
    return {
        "builder": builder,
        "service": service,
        "inSchema": node is not None,
        "field": schema_form.asdict(field),
    }


@router.get("/forms/{schema_name}/{definition}")
def form_model(schema_name: str, definition: str) -> dict[str, Any]:
    """Field descriptors for a schema definition, e.g. ``config/settings``."""
    filename = f"{schema_name}-schema.json"
    schema = validation.load_schema(filename)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"No such schema: {schema_name}")
    try:
        fields = schema_form.form_model(schema, definition)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"No such definition: {definition}") from exc
    return {"schema": schema_name, "definition": definition, "fields": schema_form.to_dict(fields)}


@router.get("/forms/defaults/{kind}/{name}")
def default_form(kind: str, name: str) -> dict[str, Any]:
    """The options form for one Kometa default.

    Which definition applies is decided by the catalog, which reads it out of the config
    schema's if/then chain -- so ``oscars`` gets the award options, ``genre`` gets the
    genre ones, and neither is hand-maintained here.
    """
    catalog = validation.load_catalog()
    group = {"collection": "collections", "overlay": "overlays", "playlist": "playlists"}.get(kind)
    if group is None:
        raise HTTPException(status_code=400, detail=f"Unknown default kind: {kind}")

    entry = catalog.get("defaults", {}).get(group, {})
    if name not in entry.get("names", []):
        raise HTTPException(status_code=404, detail=f"{name} is not an available {kind} default")

    definition = entry["template_variable_refs"].get(name) or entry.get("shared_template_variable_ref")
    schema = validation.load_schema("config-schema.json")
    if schema is None or not definition:
        raise HTTPException(status_code=503, detail="Schema or catalog unavailable")

    fields = schema_form.form_model(schema, definition)
    return {
        "name": name,
        "kind": kind,
        "definition": definition,
        "file": entry.get("files", {}).get(name),
        "fields": schema_form.to_dict(fields),
    }


# ----------------------------------------------------------------------------------
# Structured document edits
# ----------------------------------------------------------------------------------


def _apply(path: str, mutate) -> dict[str, Any]:
    """Read, transform, save, and re-validate a workspace file."""
    workspace = _workspace()
    try:
        original = workspace.read(path)
        updated = mutate(original)
    except EditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if updated == original:
        return {"path": path, "changed": False, "text": original}

    try:
        backup = workspace.write(path, updated, settings.backup_retention)
    except ReadOnlyError as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from exc
    except YAMLError as exc:
        raise HTTPException(status_code=500, detail=f"Edit produced invalid YAML: {exc}") from exc

    result = validation.validate_text(updated, path, prefer_kometa_path=workspace.resolve(path))
    return {
        "path": path,
        "changed": True,
        "text": updated,
        "backup": backup,
        "validation": validation.result_to_dict(result),
    }


class AddDefaultRequest(BaseModel):
    config: str
    library: str
    kind: str
    name: str
    template_variables: dict[str, Any] = Field(default_factory=dict)


class RemoveDefaultRequest(BaseModel):
    config: str
    library: str
    list_key: str
    index: int


class TemplateVariablesRequest(BaseModel):
    config: str
    library: str
    list_key: str
    index: int
    template_variables: dict[str, Any] = Field(default_factory=dict)


class AddDefinitionRequest(BaseModel):
    path: str
    name: str
    definition: dict[str, Any]


class SetValueRequest(BaseModel):
    path: str
    pointer: list[Any]
    value: Any = None


@router.get("/defaults/enabled")
def enabled_defaults(config: str, library: str | None = None) -> dict[str, Any]:
    workspace = _workspace()
    try:
        text = workspace.read(config)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"enabled": documents.enabled_defaults(text, library)}


@router.post("/defaults/add")
def add_default(request: AddDefaultRequest) -> dict[str, Any]:
    return _apply(
        request.config,
        lambda text: documents.add_default(
            text, request.library, request.kind, request.name, request.template_variables
        ),
    )


@router.post("/defaults/remove")
def remove_default(request: RemoveDefaultRequest) -> dict[str, Any]:
    return _apply(
        request.config,
        lambda text: documents.remove_default(text, request.library, request.list_key, request.index),
    )


@router.post("/defaults/template-variables")
def set_template_variables(request: TemplateVariablesRequest) -> dict[str, Any]:
    return _apply(
        request.config,
        lambda text: documents.set_default_template_variables(
            text, request.library, request.list_key, request.index, request.template_variables
        ),
    )


@router.post("/collections/add")
def add_collection(request: AddDefinitionRequest) -> dict[str, Any]:
    return _apply(request.path, lambda text: documents.add_collection(text, request.name, request.definition))


@router.post("/overlays/add")
def add_overlay(request: AddDefinitionRequest) -> dict[str, Any]:
    return _apply(request.path, lambda text: documents.add_overlay(text, request.name, request.definition))


@router.post("/documents/set")
def set_value(request: SetValueRequest) -> dict[str, Any]:
    return _apply(request.path, lambda text: documents.set_value(text, request.pointer, request.value))


@router.post("/documents/remove")
def remove_value(request: SetValueRequest) -> dict[str, Any]:
    return _apply(request.path, lambda text: documents.remove_value(text, request.pointer))


# ----------------------------------------------------------------------------------
# Connections (read-only)
# ----------------------------------------------------------------------------------


class ConnectionRequest(BaseModel):
    url: str
    token: str


class TmdbRequest(BaseModel):
    apikey: str


class TokenRequest(BaseModel):
    token: str


@router.post("/plex/pin")
def plex_pin() -> dict[str, Any]:
    """Begin the plex.tv sign-in flow and return the code to approve."""
    try:
        pin = plex_client.start_pin()
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"id": pin.id, "code": pin.code, "authUrl": pin.auth_url}


@router.get("/plex/pin/{pin_id}")
def plex_pin_status(pin_id: int) -> dict[str, Any]:
    """Check whether the code has been approved yet.

    Returns the token when it is ready. The frontend holds it only long enough to test the
    connection and write it into config.yml; nothing is persisted server-side.
    """
    try:
        token = plex_client.poll_pin(pin_id)
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"linked": token is not None, "token": token}


@router.post("/plex/servers")
def plex_servers(request: TokenRequest) -> dict[str, Any]:
    """Servers reachable by this account, so the user need not know their local URL."""
    try:
        return {"servers": plex_client.list_servers(request.token)}
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/plex/test")
def plex_test(request: ConnectionRequest) -> dict[str, Any]:
    try:
        info = plex_client.test_connection(request.url, request.token)
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": info.name, "version": info.version, "platform": info.platform}


@router.post("/plex/libraries")
def plex_libraries(request: ConnectionRequest) -> dict[str, Any]:
    try:
        return {"libraries": plex_client.discover_libraries(request.url, request.token)}
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tmdb/test")
def tmdb_test(request: TmdbRequest) -> dict[str, Any]:
    try:
        return plex_client.test_tmdb(request.apikey)
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
