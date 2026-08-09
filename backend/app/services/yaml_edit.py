"""Structured YAML edits that touch only the lines they need to.

The obvious way to change a YAML document from a form is load -> mutate -> dump. That is
not acceptable here: a dump cycle cosmetically rewrites 50 of the 93 files Kometa ships
(see ``tests/test_yaml_doc.py``), so changing one field would produce a diff touching
dozens of unrelated lines and would quietly restyle a user's hand-written config.

So edits are applied to the *text*. ruamel records the line and column of every key,
value, and sequence item while parsing; from those positions we work out which lines a
node occupies and splice only those. Everything outside the edited span comes through
byte-identical, including comments, anchors, quoting, and indentation style.

Insertions are the cheapest case and the one the "create things without writing YAML"
flows rely on: nothing existing is rewritten at all, a freshly rendered block is simply
placed at the right offset and indent.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import ruamel.yaml as ryaml
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from .yaml_doc import loads

# A path into a document: mapping keys as strings, sequence indices as ints.
Path = list[str | int]


class EditError(Exception):
    """Raised when an edit cannot be applied safely."""


@dataclass(frozen=True)
class Span:
    """The half-open line range ``[start, end)`` a node occupies, zero-based."""

    start: int
    end: int
    indent: int


# ----------------------------------------------------------------------------------
# Locating nodes
# ----------------------------------------------------------------------------------


def _descend(data: Any, path: Path) -> Any:
    node = data
    for step in path:
        try:
            node = node[step]
        except (KeyError, IndexError, TypeError) as exc:
            raise EditError(f"No such path: {_render_path(path)}") from exc
    return node


def _render_path(path: Path) -> str:
    return ".".join(str(p) for p in path) or "(root)"


def _child_position(parent: Any, step: str | int, path: Path) -> tuple[int, int]:
    """Line and column where a child's *key* (or sequence item) begins."""
    if isinstance(parent, CommentedMap):
        if step not in parent:
            raise EditError(f"No such path: {_render_path(path)}")
        return parent.lc.key(step)
    if isinstance(parent, CommentedSeq):
        if not isinstance(step, int) or not (0 <= step < len(parent)):
            raise EditError(f"No such path: {_render_path(path)}")
        return parent.lc.item(step)
    raise EditError(f"Cannot index into {type(parent).__name__} at {_render_path(path)}")


def _is_blank_or_comment(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def node_span(text: str, path: Path, data: Any | None = None) -> Span:
    """Find the lines occupied by the node at ``path``.

    A node starts at its key line and runs until the next line whose indentation is at or
    below the key's own -- that is how block YAML delimits nesting. Trailing blank lines
    and comments are excluded so they stay attached to whatever follows, which is almost
    always what the author intended.
    """
    if not path:
        raise EditError("Cannot take the span of the document root")

    data = loads(text) if data is None else data
    parent = _descend(data, path[:-1])
    line, col = _child_position(parent, path[-1], path)

    lines = text.splitlines()
    end = len(lines)
    for index in range(line + 1, len(lines)):
        candidate = lines[index]
        if _is_blank_or_comment(candidate):
            continue
        if _indent_of(candidate) <= col:
            end = index
            break

    # Give back any blank/comment lines at the tail; they belong to the next node.
    while end > line + 1 and _is_blank_or_comment(lines[end - 1]):
        end -= 1

    return Span(start=line, end=end, indent=col)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


# ----------------------------------------------------------------------------------
# Rendering fragments
# ----------------------------------------------------------------------------------


def render_fragment(value: Any, indent: int = 0, key: str | None = None) -> str:
    """Render a value as a standalone YAML block at the given indentation.

    Only the fragment passes through ruamel's emitter, so its formatting choices are
    confined to the new text and never reach the rest of the document.
    """
    payload = {key: value} if key is not None else value

    yaml = ryaml.YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)

    stream = io.StringIO()
    yaml.dump(payload, stream)
    body = stream.getvalue()

    # ruamel appends a document-end marker for bare scalars; drop it.
    if body.endswith("...\n"):
        body = body[: -len("...\n")]

    # Shift the block so its first line sits exactly at `indent`. Normalising against the
    # emitter's own leading offset matters for sequences: with `offset=2` a top-level list
    # already renders two columns in, and naively prepending padding would double-count it
    # and produce a nested list instead of a sibling item.
    lines = body.splitlines(keepends=True)
    first = next((line for line in lines if line.strip()), "")
    delta = indent - (len(first) - len(first.lstrip(" ")))

    if delta > 0:
        pad = " " * delta
        lines = [f"{pad}{line}" if line.strip() else line for line in lines]
    elif delta < 0:
        strip = -delta
        lines = [line[strip:] if line[:strip].isspace() else line.lstrip(" ") for line in lines]

    return "".join(lines)


# ----------------------------------------------------------------------------------
# Edits
# ----------------------------------------------------------------------------------


def set_scalar(text: str, path: Path, value: Any) -> str:
    """Replace the value of an existing scalar, leaving its line's layout intact.

    Preserves any trailing comment on the line, since that is frequently the note
    explaining why the value is what it is.
    """
    data = loads(text)
    parent = _descend(data, path[:-1])
    existing = _descend(data, path)
    if isinstance(existing, (CommentedMap, CommentedSeq, dict, list)):
        raise EditError(f"{_render_path(path)} is not a scalar")

    if not isinstance(parent, CommentedMap):
        raise EditError("set_scalar expects a mapping key")

    line_no, value_col = parent.lc.value(path[-1])
    lines = text.splitlines(keepends=True)
    line = lines[line_no]
    newline = "\n" if line.endswith("\n") else ""
    body = line.rstrip("\n")

    head = body[:value_col]
    tail = body[value_col:]

    # Keep an inline comment if there is one outside of quotes.
    comment = ""
    hash_at = _unquoted_hash(tail)
    if hash_at is not None:
        comment = tail[hash_at:]
        gap = len(tail[:hash_at]) - len(tail[:hash_at].rstrip())
        comment = " " * max(gap, 1) + comment

    lines[line_no] = f"{head}{_scalar_text(value)}{comment}{newline}"
    return "".join(lines)


def _unquoted_hash(fragment: str) -> int | None:
    """Index of a comment marker in ``fragment``, ignoring quoted regions."""
    quote: str | None = None
    for i, char in enumerate(fragment):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (i == 0 or fragment[i - 1] in " \t"):
            return i
    return None


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    rendered = render_fragment(value).rstrip("\n")
    return rendered


def insert_mapping_entry(text: str, path: Path, key: str, value: Any) -> str:
    """Add ``key: value`` to the mapping at ``path`` without disturbing its siblings.

    The new block is appended after the mapping's last entry. When the mapping is empty
    and written as ``{}``, the flow marker is replaced by a block entry.
    """
    data = loads(text)
    target = _descend(data, path) if path else data

    if not isinstance(target, (CommentedMap, dict)):
        raise EditError(f"{_render_path(path)} is not a mapping")
    if key in target:
        raise EditError(f"{key!r} already exists at {_render_path(path)}")

    lines = text.splitlines(keepends=True)

    if len(target) == 0:
        return _populate_empty_container(text, path, lines, render_key=key, value=value)

    # Indent to match the mapping's existing children.
    last_key = list(target.keys())[-1]
    _, child_col = target.lc.key(last_key)
    last_span = node_span(text, list(path) + [last_key], data)
    fragment = render_fragment(value, indent=child_col, key=key)

    return _splice(lines, last_span.end, fragment)


def append_sequence_item(text: str, path: Path, value: Any) -> str:
    """Append an item to the sequence at ``path``."""
    data = loads(text)
    target = _descend(data, path) if path else data

    if not isinstance(target, (CommentedSeq, list)):
        raise EditError(f"{_render_path(path)} is not a sequence")

    lines = text.splitlines(keepends=True)

    if len(target) == 0:
        return _populate_empty_container(text, path, lines, render_key=None, value=[value])

    _, item_col = target.lc.item(len(target) - 1)
    last_span = node_span(text, list(path) + [len(target) - 1], data)

    # A sequence item's recorded column is where its *content* starts; the dash sits two
    # columns to the left in the indentation style we emit.
    dash_indent = max(item_col - 2, 0)
    fragment = render_fragment([value], indent=dash_indent)

    return _splice(lines, last_span.end, fragment)


def _populate_empty_container(
    text: str,
    path: Path,
    lines: list[str],
    render_key: str | None,
    value: Any,
) -> str:
    """Replace an empty ``{}``/``[]`` marker with real block content."""
    if not path:
        raise EditError("Cannot populate an empty document root")

    data = loads(text)
    parent = _descend(data, path[:-1])
    if not isinstance(parent, CommentedMap):
        raise EditError("Empty container must be a mapping value")

    line_no, value_col = parent.lc.value(path[-1])
    line = lines[line_no]
    newline = "\n" if line.endswith("\n") else "\n"
    head = line.rstrip("\n")[:value_col].rstrip()

    child_indent = _indent_of(line) + 2
    fragment = render_fragment(value, indent=child_indent, key=render_key)

    return "".join(lines[:line_no]) + head + newline + fragment + "".join(lines[line_no + 1 :])


def delete_node(text: str, path: Path) -> str:
    """Remove the node at ``path`` along with the comment block introducing it."""
    data = loads(text)

    # Deleting a container's last child would leave a bare `key:` behind, which YAML reads
    # as null rather than as an empty mapping -- a different thing, and one Kometa may
    # reject. Collapse to an explicit `{}` / `[]` instead.
    if len(path) > 1:
        parent = _descend(data, path[:-1])
        if isinstance(parent, (CommentedMap, CommentedSeq)) and len(parent) == 1:
            empty: Any = [] if isinstance(parent, CommentedSeq) else {}
            return replace_node(text, list(path[:-1]), empty)

    span = node_span(text, path, data)
    lines = text.splitlines(keepends=True)

    # Take any comment lines immediately above that sit at the node's indentation --
    # they document the thing being removed.
    start = span.start
    while start > 0:
        previous = lines[start - 1]
        if previous.strip().startswith("#") and _indent_of(previous) == span.indent:
            start -= 1
        else:
            break

    return "".join(lines[:start]) + "".join(lines[span.end :])


def replace_node(text: str, path: Path, value: Any) -> str:
    """Replace a node's value wholesale, keeping its key and position."""
    data = loads(text)
    span = node_span(text, path, data)
    lines = text.splitlines(keepends=True)
    key = path[-1]

    if isinstance(key, int):
        fragment = render_fragment([value], indent=max(span.indent - 2, 0))
    else:
        fragment = render_fragment(value, indent=span.indent, key=str(key))

    return "".join(lines[: span.start]) + fragment + "".join(lines[span.end :])


def _splice(lines: list[str], at: int, fragment: str) -> str:
    """Insert ``fragment`` between lines, repairing a missing final newline.

    A file whose last line has no newline would otherwise have the inserted block run on
    from it, corrupting both.
    """
    before = "".join(lines[:at])
    if before and not before.endswith("\n"):
        before += "\n"
    if fragment and not fragment.endswith("\n"):
        fragment += "\n"
    return before + fragment + "".join(lines[at:])
