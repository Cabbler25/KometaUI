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
from ..services import connections, documents, plex_client, preview, schema_form, validation
from ..services.workspace import ReadOnlyError, Workspace, WorkspaceError
from ..services.yaml_doc import loads
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
    # Worked examples from Kometa's own galleries. A generated form can say `plex_search`
    # is an object; only an example shows what belongs inside it.
    documentation = catalog.get("builder_examples", {}).get(builder, {})

    return {
        "builder": builder,
        "service": service,
        "inSchema": node is not None,
        "field": schema_form.asdict(field),
        "hint": documentation.get("hint", ""),
        "examples": documentation.get("examples", []),
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


class MergeRequest(BaseModel):
    path: str
    pointer: list[Any]
    values: dict[str, Any] = Field(default_factory=dict)


@router.post("/documents/merge")
def merge_mapping(request: MergeRequest) -> dict[str, Any]:
    """Save a form: reconcile a mapping to the submitted values, key by key."""
    return _apply(
        request.path,
        lambda text: documents.merge_mapping(text, request.pointer, request.values),
    )


@router.get("/documents/value")
def read_value(path: str, pointer: str = "") -> dict[str, Any]:
    """Read the mapping a form should be populated from.

    ``pointer`` is a dotted path; an empty one reads the document root.
    """
    workspace = _workspace()
    try:
        data = loads(workspace.read(path))
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse {path}: {exc}") from exc

    node: Any = data
    for step in [p for p in pointer.split(".") if p]:
        if isinstance(node, dict) and step in node:
            node = node[step]
        else:
            return {"path": path, "pointer": pointer, "exists": False, "value": None}

    return {"path": path, "pointer": pointer, "exists": True, "value": _plain(node)}


def _plain(value: Any) -> Any:
    """Strip ruamel's comment-carrying wrappers so the value serialises as plain JSON."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# ----------------------------------------------------------------------------------
# Connections (read-only)
# ----------------------------------------------------------------------------------


class TmdbRequest(BaseModel):
    apikey: str


class TokenRequest(BaseModel):
    token: str


class UrlRequest(BaseModel):
    """Only the address; the token comes from the server-side session."""

    url: str | None = None


class SaveConnectionsRequest(BaseModel):
    config: str


@router.get("/connections")
def get_connections(config: str | None = None) -> dict[str, Any]:
    """Current connection state, restored across page reloads.

    Seeded from the open config when asked, so a user who already has a working token in
    ``config.yml`` is not made to sign in again.
    """
    session = connections.current()
    if config:
        workspace = state.current_or_none()
        if workspace is not None:
            try:
                connections.seed_from_config(workspace.read(config))
            except (WorkspaceError, UnicodeDecodeError):
                pass
    return session.public()


@router.post("/connections/token")
def set_connection_token(request: TokenRequest) -> dict[str, Any]:
    """Accept a manually pasted token."""
    connections.set_token(request.token)
    return connections.current().public()


@router.post("/connections/reset")
def reset_connections() -> dict[str, Any]:
    """Forget the held token and everything derived from it."""
    return connections.reset().public()


@router.post("/plex/pin")
def plex_pin() -> dict[str, Any]:
    """Begin the plex.tv sign-in flow."""
    try:
        pin = plex_client.start_pin()
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"id": pin.id, "code": pin.code, "authUrl": pin.auth_url}


@router.get("/plex/pin/{pin_id}")
def plex_pin_status(pin_id: int) -> dict[str, Any]:
    """Check whether the code has been approved.

    On success the token is stored in the session and *not* returned: the browser never
    needs to see it, since every Plex call is made by the backend.
    """
    try:
        token = plex_client.poll_pin(pin_id)
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if token is None:
        return {"linked": False, "connection": connections.current().public()}

    connections.set_token(token)
    session = connections.current()

    # Offer the account's servers straight away; picking an address is the next thing the
    # user has to do, and they should not need to know their own local IP.
    servers: list[dict[str, Any]] = []
    try:
        servers = plex_client.list_servers(token)
    except plex_client.PlexError:
        pass
    if servers and not session.url:
        first = servers[0]["connections"]
        if first:
            session.url = first[0]["uri"]

    return {"linked": True, "servers": servers, "connection": session.public()}


@router.post("/plex/servers")
def plex_servers() -> dict[str, Any]:
    """Servers reachable by the held token."""
    session = connections.current()
    if not session.token:
        raise HTTPException(status_code=400, detail="Not signed in to Plex.")
    try:
        return {"servers": plex_client.list_servers(session.token)}
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/plex/test")
def plex_test(request: UrlRequest) -> dict[str, Any]:
    """Verify the connection and discover libraries in one step.

    Both are wanted together every time, and doing them in one call means the session's
    view of the server can never be half-updated.
    """
    session = connections.current()
    url = (request.url or session.url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="A Plex server address is required.")
    if not session.token:
        raise HTTPException(status_code=400, detail="Sign in to Plex, or paste a token first.")

    session.url = url
    try:
        info = plex_client.test_connection(url, session.token)
        libraries = plex_client.discover_libraries(url, session.token)
    except plex_client.PlexError as exc:
        session.plex_error = str(exc)
        session.server_name = None
        session.libraries = []
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.server_name = info.name
    session.server_version = info.version
    session.libraries = libraries
    session.plex_error = None
    return session.public()


@router.post("/tmdb/test")
def tmdb_test(request: TmdbRequest) -> dict[str, Any]:
    session = connections.current()
    try:
        plex_client.test_tmdb(request.apikey)
    except plex_client.PlexError as exc:
        session.tmdb_ok = False
        session.tmdb_error = str(exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    session.apikey = request.apikey
    session.tmdb_ok = True
    session.tmdb_error = None
    return session.public()


@router.post("/connections/save")
def save_connections(request: SaveConnectionsRequest) -> dict[str, Any]:
    """Write the session's connection details into the config.

    Each value goes in as its own surgical edit, so the rest of the file -- comments
    included -- is untouched.
    """
    session = connections.current()
    written: list[str] = []

    def mutate(text: str) -> str:
        nonlocal written
        updated = text
        if session.url:
            updated = documents.set_value(updated, ["plex", "url"], session.url)
            written.append("plex.url")
        if session.token:
            updated = documents.set_value(updated, ["plex", "token"], session.token)
            written.append("plex.token")
        if session.apikey:
            updated = documents.set_value(updated, ["tmdb", "apikey"], session.apikey)
            written.append("tmdb.apikey")
        return updated

    result = _apply(request.config, mutate)
    return {**result, "written": written}


class PreviewRequest(BaseModel):
    library: str
    definition: dict[str, Any]


@router.post("/preview")
def preview_collection(request: PreviewRequest) -> dict[str, Any]:
    """Show which library items a Plex-native definition would match."""
    try:
        result = preview.preview_definition(request.library, request.definition)
    except preview.PreviewUnsupported as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except plex_client.PlexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "total": result.total,
        "items": result.items,
        "applied": result.applied,
        "skipped": result.skipped,
        "truncated": result.truncated,
    }


@router.post("/preview/supported")
def preview_supported(request: PreviewRequest) -> dict[str, Any]:
    """Whether a definition could be previewed, without contacting Plex."""
    ok, blocking = preview.previewable(request.definition)
    return {"previewable": ok, "blocking": blocking}


class SnippetRequest(BaseModel):
    text: str
    key: str | None = None


@router.post("/yaml/parse")
def parse_snippet(request: SnippetRequest) -> dict[str, Any]:
    """Parse a YAML snippet into plain data.

    Used when the UI offers "use this example": the snippet is Kometa's own text, and
    parsing it here avoids shipping a YAML parser to the browser -- where the npm `yaml`
    package collides with monaco's YAML grammar under Vite (see frontend/vite.config.ts).
    """
    try:
        data = loads(request.text)
    except YAMLError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse snippet: {exc}") from exc

    value = data
    if request.key and isinstance(data, dict) and request.key in data:
        value = data[request.key]
    return {"value": _plain(value)}


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
