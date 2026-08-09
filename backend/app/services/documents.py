"""High-level document operations behind the create-without-YAML flows.

Each function takes the current file text and returns new text, applying the smallest
possible edit via :mod:`yaml_edit`. Nothing here loads-and-dumps a whole document, so a
user's comments and formatting survive every operation the UI offers.
"""

from __future__ import annotations

from typing import Any

from .yaml_doc import loads
from .yaml_edit import (
    EditError,
    append_sequence_item,
    delete_node,
    insert_mapping_entry,
    replace_node,
    set_scalar,
)

# Kometa reads defaults from these keys; `pmm` is the pre-rename spelling still found in
# live configs. New entries are always written as `default`, but existing `pmm` entries
# are recognised so the UI can show what is already enabled.
DEFAULT_KEYS = ("default", "pmm")

FILE_LIST_FOR_KIND = {
    "collection": "collection_files",
    "overlay": "overlay_files",
    "playlist": "playlist_files",
}


def _library_block(data: Any, library: str) -> dict[str, Any]:
    libraries = data.get("libraries") if isinstance(data, dict) else None
    if not isinstance(libraries, dict) or library not in libraries:
        raise EditError(f"No such library: {library}")
    block = libraries[library]
    if not isinstance(block, dict):
        raise EditError(f"Library {library} has no settings block")
    return block


def enabled_defaults(config_text: str, library: str | None = None) -> list[dict[str, Any]]:
    """List the Kometa defaults a config currently enables.

    Used to render the Defaults browser as a set of toggles rather than a blind list.
    """
    data = loads(config_text)
    if not isinstance(data, dict):
        return []

    found: list[dict[str, Any]] = []

    def scan(block: dict[str, Any], owner: str | None) -> None:
        for kind, list_key in FILE_LIST_FOR_KIND.items():
            entries = block.get(list_key)
            if not isinstance(entries, list):
                continue
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                for key in DEFAULT_KEYS:
                    if key in entry:
                        found.append(
                            {
                                "name": entry[key],
                                "kind": kind,
                                "library": owner,
                                "listKey": list_key,
                                "index": index,
                                "legacyKey": key == "pmm",
                                "templateVariables": entry.get("template_variables") or {},
                            }
                        )
                        break

    libraries = data.get("libraries")
    if isinstance(libraries, dict):
        for name, block in libraries.items():
            if isinstance(block, dict) and (library is None or name == library):
                scan(block, str(name))

    # playlist_files live at the top level, not under a library.
    if library is None:
        scan(data, None)

    return found


def add_default(
    config_text: str,
    library: str,
    kind: str,
    name: str,
    template_variables: dict[str, Any] | None = None,
) -> str:
    """Enable a Kometa default for a library.

    Creates the ``collection_files``/``overlay_files`` list if the library does not have
    one yet, so enabling the first default on a bare library works.
    """
    list_key = FILE_LIST_FOR_KIND.get(kind)
    if list_key is None:
        raise EditError(f"Unknown default kind: {kind}")

    data = loads(config_text)
    block = _library_block(data, library)

    entry: dict[str, Any] = {"default": name}
    if template_variables:
        entry["template_variables"] = template_variables

    for existing in block.get(list_key) or []:
        if isinstance(existing, dict) and any(existing.get(k) == name for k in DEFAULT_KEYS):
            raise EditError(f"{name} is already enabled for {library}")

    if list_key not in block:
        return insert_mapping_entry(config_text, ["libraries", library], list_key, [entry])
    return append_sequence_item(config_text, ["libraries", library, list_key], entry)


def remove_default(config_text: str, library: str, list_key: str, index: int) -> str:
    """Disable a default by removing its list entry."""
    return delete_node(config_text, ["libraries", library, list_key, index])


def set_default_template_variables(
    config_text: str,
    library: str,
    list_key: str,
    index: int,
    template_variables: dict[str, Any],
) -> str:
    """Write a default's ``template_variables`` block, adding or replacing as needed."""
    data = loads(config_text)
    path = ["libraries", library, list_key, index]
    entry = data["libraries"][library][list_key][index]

    if not template_variables:
        if "template_variables" in entry:
            return delete_node(config_text, [*path, "template_variables"])
        return config_text

    if "template_variables" in entry:
        return replace_node(config_text, [*path, "template_variables"], template_variables)
    return insert_mapping_entry(config_text, path, "template_variables", template_variables)


def add_collection(file_text: str, name: str, definition: dict[str, Any]) -> str:
    """Add a collection to a collection file, creating the ``collections`` key if absent."""
    data = loads(file_text)

    if not isinstance(data, dict) or "collections" not in data:
        if not file_text or file_text.isspace():
            # A brand-new file: emit the whole structure at once.
            from .yaml_edit import render_fragment

            return render_fragment({name: definition}, key="collections")
        return insert_mapping_entry(file_text, [], "collections", {name: definition})

    return insert_mapping_entry(file_text, ["collections"], name, definition)


def add_overlay(file_text: str, name: str, definition: dict[str, Any]) -> str:
    """Add an overlay to an overlay file."""
    data = loads(file_text)
    if not isinstance(data, dict) or "overlays" not in data:
        if not file_text or file_text.isspace():
            from .yaml_edit import render_fragment

            return render_fragment({name: definition}, key="overlays")
        return insert_mapping_entry(file_text, [], "overlays", {name: definition})
    return insert_mapping_entry(file_text, ["overlays"], name, definition)


def set_value(file_text: str, path: list[str | int], value: Any) -> str:
    """Set a value, choosing the narrowest edit that fits.

    Scalars are patched in place so the line keeps its layout and trailing comment;
    anything structural replaces just its own subtree.
    """
    data = loads(file_text)
    node: Any = data
    for step in path[:-1]:
        node = node[step]

    key = path[-1]
    exists = (isinstance(node, dict) and key in node) or (
        isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node)
    )

    if not exists:
        if isinstance(node, list):
            return append_sequence_item(file_text, list(path[:-1]), value)
        return insert_mapping_entry(file_text, list(path[:-1]), str(key), value)

    current = node[key]
    if isinstance(current, (dict, list)) or isinstance(value, (dict, list)):
        return replace_node(file_text, list(path), value)
    return set_scalar(file_text, list(path), value)


def remove_value(file_text: str, path: list[str | int]) -> str:
    return delete_node(file_text, list(path))


def merge_mapping(file_text: str, path: list[str | int], values: dict[str, Any]) -> str:
    """Make the mapping at ``path`` match ``values``, one surgical edit per key.

    Used when a form is saved. Writing the whole mapping back in one go would re-emit
    every line of it; instead only genuinely changed keys are touched, so a form with
    forty fields and one edit produces a one-line diff.

    Keys absent from ``values`` are removed -- the form is the complete intended state of
    that mapping, so clearing a field must delete it rather than leave a stale value.
    """
    data = loads(file_text)

    node: Any = data
    for step in path:
        if isinstance(node, dict) and step in node:
            node = node[step]
        elif isinstance(node, list) and isinstance(step, int) and 0 <= step < len(node):
            node = node[step]
        else:
            node = None
            break

    if node is None:
        # The mapping does not exist yet; create it whole.
        return set_value(file_text, path, dict(values)) if values else file_text
    if not isinstance(node, dict):
        raise EditError(f"{'.'.join(str(p) for p in path)} is not a mapping")

    text = file_text

    # Removals first: deleting later would invalidate positions computed now.
    for key in [k for k in node if k not in values]:
        text = delete_node(text, [*path, key])

    for key, value in values.items():
        current = node.get(key) if key in node else None
        if key in node and current == value:
            continue
        text = set_value(text, [*path, key], value)

    return text
