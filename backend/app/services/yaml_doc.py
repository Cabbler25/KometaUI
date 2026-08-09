"""Comment-preserving YAML document handling.

Kometa configs are hand-written and heavily commented -- users annotate why a collection
exists, keep disabled blocks around, and rely on anchors. An editor that silently strips
that on save is worse than no editor, so every read/write goes through ruamel's
round-trip mode, pinned to the same version Kometa uses.
"""

from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import ruamel.yaml as ryaml
from ruamel.yaml.error import YAMLError

BACKUP_SUFFIX = ".kometaui-bak"


def _yaml() -> ryaml.YAML:
    """A round-trip YAML instance configured to leave formatting alone.

    ``width`` is set very high because ruamel otherwise re-wraps long lines (URLs,
    summaries), which shows up as spurious diff noise in files we only partially edit.
    """
    y = ryaml.YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


@dataclass
class ParseError:
    """A YAML syntax error located in the source."""

    message: str
    line: int | None = None
    column: int | None = None

    @classmethod
    def from_yaml_error(cls, exc: YAMLError) -> ParseError:
        mark = getattr(exc, "problem_mark", None)
        problem = getattr(exc, "problem", None) or str(exc)
        return cls(
            message=problem.strip(),
            # ruamel marks are zero-based; editors are one-based.
            line=(mark.line + 1) if mark is not None else None,
            column=(mark.column + 1) if mark is not None else None,
        )


class YamlDocument:
    """A YAML file loaded in round-trip mode."""

    def __init__(self, path: Path, text: str, data: Any):
        self.path = path
        self.text = text
        self.data = data

    @classmethod
    def load(cls, path: Path) -> YamlDocument:
        text = path.read_text(encoding="utf-8")
        return cls(path, text, loads(text))

    def dumps(self) -> str:
        return dumps(self.data)


def loads(text: str) -> Any:
    """Parse YAML text in round-trip mode. Raises ``YAMLError`` on invalid input."""
    return _yaml().load(text)


def dumps(data: Any) -> str:
    """Serialise a round-trip document back to text."""
    stream = io.StringIO()
    _yaml().dump(data, stream)
    return stream.getvalue()


def round_trip(text: str) -> str:
    """Parse and re-serialise. Used by tests to assert formatting stability.

    Measured against Kometa 2.4.6's 90 default files plus the three prototype configs:
    all 93 survive round-tripping with **identical semantics**, and the operation is
    idempotent for every one of them. 43 are byte-identical; the rest differ only
    cosmetically -- 36 by trailing-whitespace stripping and 14 by indentation
    normalisation (``defaults/award/sag.yml`` indents a mapping by one space, which
    ruamel rewrites to two).

    Those cosmetic rewrites are harmless but noisy: a user who changes one field does not
    want a diff touching 40 unrelated lines. So a full parse/dump cycle is reserved for
    documents we are rewriting wholesale. Text the user edited directly is saved verbatim
    via :func:`save_text`, and form-driven edits should splice the specific node rather
    than re-dumping (see the module TODO).
    """
    return dumps(loads(text))


# TODO(M2): form-driven edits currently have no surgical path. When forms land, locate the
# target node via ruamel's ``.lc`` line/column data and splice just those lines, falling
# back to a full dump only when the node cannot be located. Without this, editing one
# field in a form would reformat the whole file.


def save_text(path: Path, text: str, retention: int = 20) -> Path | None:
    """Persist user-authored text exactly as written, after validating it parses.

    Verbatim so the editor never reformats what the user typed. Parsing first means a
    syntax error is reported before the old contents are replaced.
    """
    loads(text)  # raises YAMLError if invalid; caller converts to a 400
    return write_with_backup(path, text, retention)


def safe_parse(text: str) -> tuple[Any | None, ParseError | None]:
    """Parse without raising; returns ``(data, error)``."""
    try:
        return loads(text), None
    except YAMLError as exc:
        return None, ParseError.from_yaml_error(exc)


def write_with_backup(path: Path, text: str, retention: int = 20) -> Path | None:
    """Write ``text`` to ``path``, copying the previous contents aside first.

    Returns the backup path, or ``None`` when the file is new. Backups live in a
    ``.kometaui-bak`` sibling directory so they never appear in the user's config tree
    and are never picked up by Kometa's own directory scanning.
    """
    backup_path: Path | None = None
    if path.exists():
        backup_dir = path.parent / BACKUP_SUFFIX
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{path.name}.{stamp}"
        shutil.copy2(path, backup_path)
        _prune_backups(backup_dir, path.name, retention)

    # Kometa reads these files with universal newlines, and writing "\n" keeps diffs
    # clean for users who keep their config in git.
    path.write_text(text, encoding="utf-8", newline="\n")
    return backup_path


def _prune_backups(backup_dir: Path, filename: str, retention: int) -> None:
    """Keep only the newest ``retention`` backups for a given file."""
    existing = sorted(
        backup_dir.glob(f"{filename}.*"),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in existing[retention:]:
        stale.unlink(missing_ok=True)
