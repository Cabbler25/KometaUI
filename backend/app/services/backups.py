"""Access to the timestamped backups taken on every write.

Backups have existed since the first save; nothing surfaced them. That made the safety
net invisible, which is nearly the same as not having one. These functions turn the
``.kometaui-bak`` directory into a change history a user can read and roll back.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .yaml_doc import BACKUP_SUFFIX, write_with_backup

# Written by write_with_backup as "<name>.<YYYYmmdd-HHMMSS-mmm>". The second form is the
# pre-millisecond layout, still read so history taken by an older build stays visible.
STAMP_FORMATS = ("%Y%m%d-%H%M%S-%f", "%Y%m%d-%H%M%S")


def _parse_stamp(stamp: str) -> datetime | None:
    for fmt in STAMP_FORMATS:
        try:
            return datetime.strptime(stamp, fmt)
        except ValueError:
            continue
    return None


@dataclass
class Backup:
    stamp: str
    taken_at: str
    size: int

    def as_dict(self) -> dict[str, Any]:
        return {"stamp": self.stamp, "takenAt": self.taken_at, "size": self.size}


def _backup_dir(target: Path) -> Path:
    return target.parent / BACKUP_SUFFIX


def list_backups(target: Path) -> list[Backup]:
    """Every retained version of a file, newest first."""
    directory = _backup_dir(target)
    if not directory.is_dir():
        return []

    found: list[Backup] = []
    for path in directory.glob(f"{target.name}.*"):
        stamp = path.name[len(target.name) + 1 :]
        taken = _parse_stamp(stamp)
        if taken is None:
            # Not one of ours; leave it alone rather than guess.
            continue
        found.append(
            Backup(stamp=stamp, taken_at=taken.isoformat(timespec="seconds"), size=path.stat().st_size)
        )

    return sorted(found, key=lambda b: b.stamp, reverse=True)


def read_backup(target: Path, stamp: str) -> str:
    path = _backup_dir(target) / f"{target.name}.{stamp}"
    if not path.is_file():
        raise FileNotFoundError(f"No backup {stamp} for {target.name}")
    return path.read_text(encoding="utf-8")


def restore(target: Path, stamp: str, retention: int = 20) -> str:
    """Roll a file back to an earlier version.

    The current contents are backed up first, so a restore is itself undoable -- rolling
    back to the wrong version should never be a one-way door.
    """
    previous = read_backup(target, stamp)
    write_with_backup(target, previous, retention)
    return previous


def unified_diff(before: str, after: str, filename: str) -> list[str]:
    """A unified diff, or an empty list when nothing changed."""
    if before == after:
        return []
    return list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"{filename} (current)",
            tofile=f"{filename} (proposed)",
            lineterm="",
            n=3,
        )
    )


def diff_stats(diff: list[str]) -> dict[str, int]:
    """Added/removed line counts, for a one-line summary above the diff."""
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return {"added": added, "removed": removed}
