"""Carry over what earlier versions kept under their old name, "looplinux"."""

import os
from pathlib import Path

_OLD_NAME = "looplinux"
_NEW_NAME = "ring"


def _bases() -> list[Path]:
    home = Path.home()
    return [
        Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config"),
        Path(os.environ.get("XDG_STATE_HOME") or home / ".local" / "state"),
    ]


def migrate_legacy_paths() -> list[tuple[Path, Path]]:
    """Rename the old settings and counter folders; return what was moved.

    A folder is only moved if nothing is in the way: whatever already sits
    under the new name is left alone, and so is the old folder then.
    """
    moved = []
    for base in _bases():
        old, new = base / _OLD_NAME, base / _NEW_NAME
        if not old.is_dir() or new.exists():
            continue
        try:
            old.rename(new)
        except OSError:
            continue
        moved.append((old, new))
    return moved
