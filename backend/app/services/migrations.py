"""Detect and rewrite outdated Kometa config keys.

Configs accumulate. A file written for an older Kometa keeps working right up until it
quietly does not, and the failure mode is usually silence rather than an error. These
checks find the keys Kometa has renamed or dropped and offer the mechanical rewrite.

Two rules the rewrites follow:

*Never change behaviour without saying so.* A rename is safe to apply in bulk. A change
that could start deleting collections is not, so it is marked ``changes_behaviour`` and
the UI keeps it out of any apply-all.

*Verify the mapping against Kometa's source, not the wiki.* The
``delete_unmanaged_collections`` rewrite below is inverted relative to what its name
suggests, which a docs-based reading would have got backwards.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .yaml_doc import loads
from .yaml_edit import delete_node, insert_mapping_entry

Severity = Literal["error", "warning", "info"]

# Renamed in the Plex-Meta-Manager -> Kometa transition. Kometa still reads `pmm:` but the
# wiki, schema and defaults all use `default:`.
LEGACY_DEFAULT_KEY = "pmm"

# modules/validator.py::DEPRECATED_KEYS — kept in step with Kometa's own warnings.
DEPRECATED_LIBRARY_KEYS = {
    "metadata_path": "collection_files",
    "overlay_path": "overlay_files",
}


@dataclass
class Finding:
    """One outdated key, and what to do about it."""

    id: str
    file: str
    # Dotted description of where it is, for display.
    location: str
    key: str
    message: str
    severity: Severity = "warning"
    # False when the fix needs a human decision rather than a rewrite.
    fixable: bool = True
    # True when applying the fix changes what Kometa does, not just how it is spelled.
    changes_behaviour: bool = False
    detail: str = ""
    # Internal: how to apply it.
    _action: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "location": self.location,
            "key": self.key,
            "message": self.message,
            "severity": self.severity,
            "fixable": self.fixable,
            "changesBehaviour": self.changes_behaviour,
            "detail": self.detail,
        }


# ----------------------------------------------------------------------------------
# Detection
# ----------------------------------------------------------------------------------


FILE_LIST_KEYS = ("collection_files", "overlay_files", "metadata_files", "playlist_files")


def scan_config(text: str, filename: str) -> list[Finding]:
    """Find outdated keys in a main config file."""
    data = loads(text)
    if not isinstance(data, dict):
        return []

    findings: list[Finding] = []
    libraries = data.get("libraries")

    if isinstance(libraries, dict):
        for library_name, block in libraries.items():
            if not isinstance(block, dict):
                continue
            findings.extend(_scan_library(filename, str(library_name), block))

    # playlist_files sit at the top level.
    findings.extend(_scan_file_list(filename, [], "playlist_files", data.get("playlist_files")))
    return findings


def _scan_library(filename: str, library: str, block: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    base = ["libraries", library]

    for old_key, replacement in DEPRECATED_LIBRARY_KEYS.items():
        if old_key in block:
            findings.append(
                Finding(
                    id=f"{filename}:{library}:{old_key}",
                    file=filename,
                    location=f"libraries.{library}",
                    key=old_key,
                    message=f"‘{old_key}’ was renamed to ‘{replacement}’.",
                    detail="A rename only; Kometa reads the same files either way.",
                    _action={"kind": "rename_key", "path": [*base, old_key], "to": replacement},
                )
            )

    for list_key in FILE_LIST_KEYS:
        findings.extend(_scan_file_list(filename, base, list_key, block.get(list_key), library))

    operations = block.get("operations")
    if isinstance(operations, dict):
        findings.extend(_scan_operations(filename, library, [*base, "operations"], operations))

    return findings


def _scan_file_list(
    filename: str,
    base: list[str | int],
    list_key: str,
    entries: Any,
    library: str | None = None,
) -> list[Finding]:
    """Flag `pmm:` entries, which predate the rename to `default:`."""
    if not isinstance(entries, list):
        return []

    location = f"libraries.{library}.{list_key}" if library else list_key
    findings = []
    for index, entry in enumerate(entries):
        if isinstance(entry, dict) and LEGACY_DEFAULT_KEY in entry:
            name = entry[LEGACY_DEFAULT_KEY]
            findings.append(
                Finding(
                    id=f"{filename}:{location}:{index}:pmm",
                    file=filename,
                    location=f"{location}[{index}]",
                    key=LEGACY_DEFAULT_KEY,
                    message=f"‘pmm: {name}’ uses the pre-rename key; ‘default:’ is current.",
                    severity="info",
                    detail="Kometa still accepts ‘pmm’, so this is cosmetic — but the wiki, "
                    "schema and every example use ‘default’.",
                    _action={
                        "kind": "rename_key",
                        "path": [*base, list_key, index, LEGACY_DEFAULT_KEY],
                        "to": "default",
                    },
                )
            )
    return findings


def _scan_operations(
    filename: str, library: str, path: list[str | int], operations: dict[str, Any]
) -> list[Finding]:
    """Flag operations Kometa 2.4.x no longer reads."""
    findings: list[Finding] = []
    location = f"libraries.{library}.operations"

    if "delete_unmanaged_collections" in operations:
        value = operations["delete_unmanaged_collections"]
        enabled = value is True

        # Kometa's own legacy bridge (modules/config.py) rewrites this into
        # delete_collections["unmanaged"], but the parser a few hundred lines later only
        # reads "managed"/"configured"/"less" — so the value is dropped and the option
        # currently does nothing at all.
        base_detail = (
            "Kometa 2.4.6 no longer reads this key: its compatibility shim writes "
            "delete_collections[‘unmanaged’], which the parser never looks at. "
            "Whatever it is set to, it currently has no effect."
        )

        if enabled:
            findings.append(
                Finding(
                    id=f"{filename}:{library}:delete_unmanaged_collections",
                    file=filename,
                    location=location,
                    key="delete_unmanaged_collections",
                    message="‘delete_unmanaged_collections: true’ is no longer read by Kometa.",
                    severity="warning",
                    changes_behaviour=True,
                    detail=base_detail
                    + " Replacing it with delete_collections: {managed: false} restores the "
                    "behaviour you originally asked for — which means Kometa will start "
                    "deleting collections it did not create. Review before applying.",
                    _action={
                        "kind": "replace_operation",
                        "path": path,
                        "remove": "delete_unmanaged_collections",
                        "add": ("delete_collections", {"managed": False}),
                    },
                )
            )
        else:
            findings.append(
                Finding(
                    id=f"{filename}:{library}:delete_unmanaged_collections",
                    file=filename,
                    location=location,
                    key="delete_unmanaged_collections",
                    message="‘delete_unmanaged_collections’ is obsolete and can be removed.",
                    severity="info",
                    detail=base_detail
                    + " It is set to false, so removing it changes nothing.",
                    _action={"kind": "remove_key", "path": [*path, "delete_unmanaged_collections"]},
                )
            )

    if "delete_collections_with_less" in operations:
        value = operations["delete_collections_with_less"]
        findings.append(
            Finding(
                id=f"{filename}:{library}:delete_collections_with_less",
                file=filename,
                location=location,
                key="delete_collections_with_less",
                message="‘delete_collections_with_less’ moved into ‘delete_collections’.",
                severity="warning",
                changes_behaviour=True,
                detail=f"Becomes delete_collections: {{less: {value}}}, which deletes collections "
                "with fewer than that many items.",
                _action={
                    "kind": "replace_operation",
                    "path": path,
                    "remove": "delete_collections_with_less",
                    "add": ("delete_collections", {"less": value}),
                },
            )
        )

    return findings


# ----------------------------------------------------------------------------------
# Applying
# ----------------------------------------------------------------------------------


def apply_finding(text: str, finding: Finding) -> str:
    """Apply one rewrite, surgically."""
    action = finding._action
    kind = action.get("kind")

    if kind == "rename_key":
        return _rename_key(text, action["path"], action["to"])

    if kind == "remove_key":
        return delete_node(text, action["path"])

    if kind == "replace_operation":
        path = action["path"]
        new_key, new_value = action["add"]
        data = loads(text)
        node: Any = data
        for step in path:
            node = node[step]

        # Merge rather than overwrite: a config may already have a delete_collections
        # block, and clobbering it would silently drop the other criteria.
        if isinstance(node, dict) and new_key in node and isinstance(node[new_key], dict):
            merged = {**node[new_key], **new_value}
            text = delete_node(text, [*path, new_key])
            text = insert_mapping_entry(text, path, new_key, merged)
        else:
            text = insert_mapping_entry(text, path, new_key, new_value)
        return delete_node(text, [*path, action["remove"]])

    raise ValueError(f"Unknown migration action: {kind}")


def _rename_key(text: str, path: list[str | int], new_key: str) -> str:
    """Rename a mapping key in place, preserving its value, position and comments.

    Rewrites just the key token on its own line. That works whether the value is a scalar
    or a whole nested block, and — unlike re-inserting under the new name and deleting the
    old — it keeps the entry where the author put it. Moving `metadata_path` to the bottom
    of a library block just to rename it would be a needlessly large diff.
    """
    data = loads(text)
    parent: Any = data
    for step in path[:-1]:
        parent = parent[step]
    old_key = path[-1]

    lines = text.splitlines(keepends=True)
    line_no, col = parent.lc.key(old_key)
    line = lines[line_no]
    prefix, remainder = line[:col], line[col:]

    if remainder.startswith(str(old_key)):
        lines[line_no] = prefix + new_key + remainder[len(str(old_key)) :]
        return "".join(lines)

    # Quoted or flow-style key, where the token is not where the position says. Fall back
    # to re-inserting the value under the new name; the entry moves, but nothing is lost.
    value = parent[old_key]
    text = insert_mapping_entry(text, list(path[:-1]), new_key, value)
    return delete_node(text, list(path))


def scan_and_apply(text: str, filename: str, ids: list[str]) -> tuple[str, list[str]]:
    """Apply the selected findings, re-scanning between each.

    Every edit shifts the positions of everything after it, so applying a batch from one
    stale scan would corrupt the file. Re-scanning after each change keeps every path
    valid, at the cost of a few reparses.
    """
    applied: list[str] = []
    remaining = list(ids)

    while remaining:
        findings = {f.id: f for f in scan_config(text, filename)}
        target = next((i for i in remaining if i in findings), None)
        if target is None:
            break
        text = apply_finding(text, findings[target])
        applied.append(target)
        remaining.remove(target)

    return text, applied
