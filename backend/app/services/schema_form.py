"""Turn Kometa's JSON Schema into a form model the UI can render generically.

Kometa's configuration surface is far too large to hand-build forms for: 40+ global
settings, 279 collection attributes, and 11 distinct template-variable definitions behind
the Defaults. Writing a React component per field would be thousands of lines that go
stale the moment Kometa ships a release.

Instead the schema *is* the form. Each property becomes a field descriptor carrying a
control hint, and the frontend renders one generic component per control type. Kometa
authored a description for essentially every property, so the help text comes free and
stays accurate.

Where a construct has no sensible widget -- a deeply polymorphic ``anyOf``, a recursive
builder block -- the field is marked ``yaml`` and the UI offers a small text area holding
raw YAML for that field alone, rather than pretending it can be a dropdown.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Control = Literal[
    "text",
    "textarea",
    "number",
    "integer",
    "boolean",
    "select",
    "multiselect",
    "list",
    "object",
    "yaml",
]


@dataclass
class FormField:
    """One rendered input."""

    name: str
    label: str
    control: Control
    description: str = ""
    required: bool = False
    default: Any = None
    options: list[Any] = field(default_factory=list)
    # Bounds, for numeric inputs.
    minimum: float | None = None
    maximum: float | None = None
    # Nested fields, for `object` controls.
    fields: list[FormField] = field(default_factory=list)
    # What a `list` control holds.
    item_control: Control | None = None
    placeholder: str = ""


# ----------------------------------------------------------------------------------
# Schema walking
# ----------------------------------------------------------------------------------


def resolve_ref(schema: dict[str, Any], node: Any) -> dict[str, Any]:
    """Follow ``$ref`` chains within one document."""
    seen = 0
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return node
        target: Any = schema
        for part in ref[2:].split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        node = target
        seen += 1
        if seen > 32:  # guard against a cyclic schema
            return {}
    return node if isinstance(node, dict) else {}


def _humanise(name: str) -> str:
    """`mass_poster_update` -> `Mass poster update`."""
    words = name.replace("_", " ").replace("-", " ").strip()
    return words[:1].upper() + words[1:] if words else name


def _first_sentence(description: str) -> str:
    """Kometa descriptions lead with a summary line, then elaborate. Keep the summary."""
    text = (description or "").strip()
    if not text:
        return ""
    return text.split("\n", 1)[0].strip()


def _branches(node: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("anyOf", "oneOf"):
        if key in node and isinstance(node[key], list):
            return [b for b in node[key] if isinstance(b, dict)]
    return []


def _is_null(node: dict[str, Any]) -> bool:
    return node.get("type") == "null"


def _control_for(schema: dict[str, Any], node: dict[str, Any]) -> tuple[Control, dict[str, Any]]:
    """Choose a widget for a schema node, and return the node it was chosen from.

    Kometa uses ``anyOf`` heavily, usually to mean "this, or null", or "a single value or
    a list of them". Both collapse to a sensible single control; anything genuinely
    polymorphic falls through to raw YAML.
    """
    node = resolve_ref(schema, node)

    branches = _branches(node)
    if branches:
        meaningful = [b for b in (resolve_ref(schema, b) for b in branches) if not _is_null(b)]
        if len(meaningful) == 1:
            return _control_for(schema, meaningful[0])
        # "scalar or list of that scalar" is a list input.
        scalars = [b for b in meaningful if b.get("type") in ("string", "number", "integer")]
        arrays = [b for b in meaningful if b.get("type") == "array"]
        if arrays and scalars:
            return "list", arrays[0]
        # A union of enums merges into one select.
        if meaningful and all("enum" in b for b in meaningful):
            merged = {"enum": [v for b in meaningful for v in b["enum"]], "description": node.get("description", "")}
            return "select", merged
        # A union of plain scalars -- `string | number` for values like
        # `collection_section`, or `string | boolean` for tri-state flags. A text input is
        # the superset that accepts all of them, and is far friendlier than raw YAML.
        scalar_types = {"string", "number", "integer", "boolean"}
        if meaningful and all(b.get("type") in scalar_types for b in meaningful):
            return "text", {**node, "description": node.get("description", "")}
        return "yaml", node

    if "enum" in node:
        return "select", node

    node_type = node.get("type")
    if node_type == "boolean":
        return "boolean", node
    if node_type == "integer":
        return "integer", node
    if node_type == "number":
        return "number", node
    if node_type == "array":
        return "list", node
    if node_type == "object":
        return ("object", node) if node.get("properties") else ("yaml", node)
    if node_type == "string":
        return "text", node

    return "yaml", node


def build_field(schema: dict[str, Any], name: str, node: Any, required: bool = False) -> FormField:
    """Describe one property as a form field."""
    node = resolve_ref(schema, node)
    control, source = _control_for(schema, node)
    description = _first_sentence(node.get("description") or source.get("description", ""))

    field_model = FormField(
        name=name,
        label=_humanise(name),
        control=control,
        description=description,
        required=required,
        default=node.get("default", source.get("default")),
        minimum=source.get("minimum"),
        maximum=source.get("maximum"),
    )

    if control == "select":
        field_model.options = list(dict.fromkeys(source.get("enum", [])))
    elif control == "list":
        items = resolve_ref(schema, source.get("items", {}))
        item_control, item_source = _control_for(schema, items) if items else ("text", {})
        field_model.item_control = item_control
        if item_control == "select":
            field_model.options = list(dict.fromkeys(item_source.get("enum", [])))
            field_model.control = "multiselect"
    elif control == "object":
        field_model.fields = build_fields(schema, source)

    return field_model


def build_fields(schema: dict[str, Any], node: dict[str, Any]) -> list[FormField]:
    """Describe every property of an object schema, alphabetically."""
    node = resolve_ref(schema, node)
    properties = dict(node.get("properties", {}))
    required = set(node.get("required", []))

    # Some definitions are a bare `oneOf` over object variants rather than a single
    # object -- `operations` is written that way, offering the same keys in long and
    # short forms. A form wants the union of what may be set, so merge the branches.
    # Keys are required only if every branch requires them.
    if not properties:
        branches = [resolve_ref(schema, b) for b in _branches(node)]
        objects = [b for b in branches if b.get("properties")]
        for branch in objects:
            for name, body in branch["properties"].items():
                properties.setdefault(name, body)
        if objects:
            required = set.intersection(*(set(b.get("required", [])) for b in objects))

    return [
        build_field(schema, name, body, name in required)
        for name, body in sorted(properties.items())
        if isinstance(body, dict)
    ]


def form_model(schema: dict[str, Any], definition: str) -> list[FormField]:
    """Build the form for a named definition, e.g. ``settings``."""
    node = schema.get("definitions", {}).get(definition)
    if node is None:
        raise KeyError(definition)
    return build_fields(schema, node)


def to_dict(fields: list[FormField]) -> list[dict[str, Any]]:
    return [asdict(f) for f in fields]
