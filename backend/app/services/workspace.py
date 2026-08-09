"""Workspace: safe, scoped access to a Kometa config directory."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ruamel.yaml.error import YAMLError

from .yaml_doc import BACKUP_SUFFIX, loads, save_text

# Kometa file-reference forms. ``default`` and ``pmm`` both point at Kometa's bundled
# defaults -- ``pmm`` is the pre-rename spelling and still appears in real configs, so
# both must be understood.
PATH_KEYS = ("file", "folder", "url", "git", "repo", "default", "pmm")
LOCAL_PATH_KEYS = ("file", "folder")

FILE_LIST_KEYS = (
    "collection_files",
    "overlay_files",
    "metadata_files",
    "playlist_files",
    "image_files",
)

YAML_SUFFIXES = {".yml", ".yaml"}

# Directories that hold generated data rather than configuration.
IGNORED_DIRS = {".git", "logs", "assets", BACKUP_SUFFIX, "anidb_cache", "__pycache__", ".vscode"}


class WorkspaceError(Exception):
    """Raised for invalid workspace operations."""


class ReadOnlyError(WorkspaceError):
    """Raised when a write is attempted on a read-only workspace."""


@dataclass
class FileNode:
    """One entry in the workspace file tree."""

    name: str
    path: str  # workspace-relative, forward-slashed
    is_dir: bool
    size: int | None = None
    children: list[FileNode] = field(default_factory=list)
    # Set when the file is referenced by config.yml, so the UI can distinguish
    # "in use" from "sitting in the folder".
    referenced_as: str | None = None


@dataclass
class FileReference:
    """A file reference discovered inside config.yml."""

    kind: Literal["file", "folder", "url", "git", "repo", "default", "pmm"]
    value: str
    library: str | None
    list_key: str
    # Resolved absolute path for local references, when it exists on disk.
    resolved: str | None = None
    exists: bool | None = None
    template_variables: dict[str, Any] | None = None


class Workspace:
    """A rooted view of a Kometa config directory.

    Every path handed in from the API is resolved against the root and rejected if it
    escapes, so a crafted request cannot read or write outside the directory the user
    opened.
    """

    def __init__(self, root: Path, allow_writes: bool = False):
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise WorkspaceError(f"Not a directory: {root}")
        self.root = root
        self.allow_writes = allow_writes

    # -- path safety ---------------------------------------------------------------

    def resolve(self, relative: str) -> Path:
        """Resolve a workspace-relative path, refusing anything outside the root."""
        if not relative or relative in (".", "/"):
            return self.root
        candidate = (self.root / relative.lstrip("/\\")).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError(f"Path escapes the workspace: {relative}")
        return candidate

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    # -- reading -------------------------------------------------------------------

    def tree(self) -> FileNode:
        """Build a tree of YAML files under the workspace root."""
        return self._walk(self.root)

    def _walk(self, directory: Path) -> FileNode:
        node = FileNode(
            name=directory.name or self.root.name,
            path="" if directory == self.root else self.relative(directory),
            is_dir=True,
        )
        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return node

        for entry in entries:
            if entry.is_dir():
                if entry.name in IGNORED_DIRS or entry.name.startswith("."):
                    continue
                child = self._walk(entry)
                # Hide directories that contain no YAML at any depth.
                if child.children:
                    node.children.append(child)
            elif entry.suffix.lower() in YAML_SUFFIXES:
                node.children.append(
                    FileNode(
                        name=entry.name,
                        path=self.relative(entry),
                        is_dir=False,
                        size=entry.stat().st_size,
                    )
                )
        return node

    def read(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise WorkspaceError(f"Not a file: {relative}")
        return path.read_text(encoding="utf-8")

    def config_candidates(self) -> list[dict[str, Any]]:
        """Find and describe every main config file in the workspace root.

        Real installs keep several side by side and select between them with ``--config``:
        a primary ``config.yml``, a poster-only variant, a debug cut, per-library configs.
        They are all legitimate, so rather than guessing one we describe each -- which
        libraries it defines, when it was last touched -- and let the user choose.

        A file qualifies if it parses and has the top-level ``libraries`` key that only a
        main config carries.
        """
        found: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.y*ml")):
            if path.suffix.lower() not in YAML_SUFFIXES:
                continue
            try:
                data = loads(path.read_text(encoding="utf-8"))
            except (YAMLError, OSError, UnicodeDecodeError):
                continue
            if not isinstance(data, dict) or "libraries" not in data:
                continue
            libraries = data.get("libraries")
            stat = path.stat()
            found.append(
                {
                    "path": self.relative(path),
                    "libraries": [str(k) for k in libraries] if isinstance(libraries, dict) else [],
                    "modified": stat.st_mtime,
                    "size": stat.st_size,
                    # config.yml is Kometa's default when --config is omitted.
                    "isConventionalDefault": path.name.lower() == "config.yml",
                }
            )
        # Surface the conventional default first, then most-recently-modified.
        found.sort(key=lambda c: (not c["isConventionalDefault"], -c["modified"]))
        return found

    # -- config reference resolution -----------------------------------------------

    def references(self, config_relative: str) -> list[FileReference]:
        """List every file reference declared by a config, resolving local ones.

        This is what turns a flat directory listing into "here is what Kometa will
        actually read", including files that are referenced but missing.
        """
        data = loads(self.read(config_relative))
        if not isinstance(data, dict):
            return []

        refs: list[FileReference] = []

        libraries = data.get("libraries")
        if isinstance(libraries, dict):
            for library_name, library in libraries.items():
                if isinstance(library, dict):
                    refs.extend(self._refs_from_block(library, str(library_name)))

        # playlist_files sits at the top level rather than under a library.
        refs.extend(self._refs_from_block(data, None))

        return refs

    def _refs_from_block(self, block: dict[str, Any], library: str | None) -> list[FileReference]:
        out: list[FileReference] = []
        for list_key in FILE_LIST_KEYS:
            entries = block.get(list_key)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for kind in PATH_KEYS:
                    if kind not in entry:
                        continue
                    value = entry[kind]
                    if not isinstance(value, str):
                        continue
                    ref = FileReference(
                        kind=kind,  # type: ignore[arg-type]
                        value=value,
                        library=library,
                        list_key=list_key,
                        template_variables=entry.get("template_variables"),
                    )
                    if kind in LOCAL_PATH_KEYS:
                        self._resolve_local(ref)
                    out.append(ref)
                    break
        return out

    def _resolve_local(self, ref: FileReference) -> None:
        """Locate a ``file:``/``folder:`` reference on disk.

        Kometa resolves these relative to its own working directory, which for a typical
        install is the Kometa root -- so a config living in ``<root>/config`` refers to
        itself as ``config/Movies.yml``. We try the workspace root, then its parent, then
        treat the value as absolute.
        """
        raw = ref.value.replace("\\", "/")
        candidates = [
            self.root / raw,
            self.root.parent / raw,
            Path(raw),
        ]
        # A config directory named "config" referenced as "config/x.yml" from its own
        # parent is the overwhelmingly common layout; strip the redundant prefix too.
        if raw.startswith(f"{self.root.name}/"):
            candidates.insert(0, self.root / raw[len(self.root.name) + 1 :])

        for candidate in candidates:
            try:
                if candidate.exists():
                    ref.resolved = str(candidate.resolve())
                    ref.exists = True
                    return
            except OSError:
                continue
        ref.exists = False

    # -- writing -------------------------------------------------------------------

    def write(self, relative: str, text: str, retention: int = 20) -> str | None:
        """Save text to a workspace file. Refused unless writes are unlocked."""
        if not self.allow_writes:
            raise ReadOnlyError(
                "This workspace is read-only. Unlock writes before saving."
            )
        path = self.resolve(relative)
        if path.is_dir():
            raise WorkspaceError(f"Cannot write to a directory: {relative}")
        backup = save_text(path, text, retention)
        return str(backup) if backup else None
