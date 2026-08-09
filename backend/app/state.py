"""Process-wide workspace state.

KometaUI is a single-user, single-workspace tool, so the open workspace lives in module
state rather than a session store.
"""

from __future__ import annotations

from pathlib import Path

from .config import settings
from .services.workspace import Workspace, WorkspaceError

_workspace: Workspace | None = None


def open_workspace(path: str | Path, allow_writes: bool = False) -> Workspace:
    global _workspace
    _workspace = Workspace(Path(path), allow_writes=allow_writes)
    return _workspace


def current() -> Workspace:
    if _workspace is None:
        raise WorkspaceError("No workspace is open.")
    return _workspace


def current_or_none() -> Workspace | None:
    return _workspace


def set_allow_writes(allow: bool) -> Workspace:
    workspace = current()
    workspace.allow_writes = allow
    return workspace


def bootstrap() -> None:
    """Open the configured workspace at startup, if one was provided."""
    if settings.workspace_path is not None:
        try:
            open_workspace(settings.workspace_path, settings.allow_writes)
        except WorkspaceError:
            # A bad configured path should not stop the server from starting; the user
            # can pick a valid one from the UI.
            pass
