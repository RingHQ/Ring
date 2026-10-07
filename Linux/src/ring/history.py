"""Remember window geometries so actions can be undone and windows restored.

The history is stored in XDG_RUNTIME_DIR, so separate `ring snap`
invocations share it and it disappears at logout together with the window ids
it refers to.
"""

import contextlib
import json
import os
import tempfile
from pathlib import Path

from ring.actions import Rect
from ring.backends.base import StashEntry

_MAX_WINDOWS = 64
_MAX_UNDO_STEPS = 16


def default_history_path() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return Path(base) / "ring" / "history.json"


class History:
    """Per-window geometry history.

    For each window it keeps the geometry from before Ring first touched
    it (for `initial_frame`), the most recent geometries (for `undo`) and the
    action it is currently snapped to (so cycles know where to continue).
    """

    def __init__(self, path: Path | None = None) -> None:
        """Create a history persisted at `path`, or kept in memory if None."""
        self._path = path
        self._original: dict[str, Rect] = {}
        self._undo: dict[str, list[Rect]] = {}
        self._last: dict[str, tuple[str, Rect]] = {}
        self._stash: dict[str, StashEntry] = {}
        if path is not None:
            self._load(path)

    def record(self, window_id: str, geometry: Rect) -> None:
        """Note the geometry a window has right before an action changes it."""
        # Re-insert so the dicts stay ordered from least to most recently used.
        self._original[window_id] = self._original.pop(window_id, geometry)
        steps = self._undo.pop(window_id, [])
        steps.append(geometry)
        self._undo[window_id] = steps[-_MAX_UNDO_STEPS:]
        for stale in list(self._original)[:-_MAX_WINDOWS]:
            self.forget(stale)
        self._save()

    def pop_undo(self, window_id: str) -> Rect | None:
        """Return and drop the geometry from before the window's last action."""
        steps = self._undo.get(window_id)
        if not steps:
            return None
        geometry = steps.pop()
        self._save()
        return geometry

    def original(self, window_id: str) -> Rect | None:
        """Return the geometry from before the window was first snapped."""
        return self._original.get(window_id)

    def set_last_action(self, window_id: str, action: str, geometry: Rect) -> None:
        """Remember which action gave the window the geometry it has now."""
        self._last[window_id] = (action, geometry)
        self._save()

    def last_action(self, window_id: str, geometry: Rect) -> str | None:
        """Return the action the window is still snapped to, if any.

        A window that was moved or resized by other means since then no
        longer counts as snapped.
        """
        entry = self._last.get(window_id)
        if entry is None:
            return None
        action, snapped = entry
        close = all(
            abs(a - b) <= 2
            for a, b in zip(
                (geometry.x, geometry.y, geometry.width, geometry.height),
                (snapped.x, snapped.y, snapped.width, snapped.height),
                strict=True,
            )
        )
        return action if close else None

    def stashed(self) -> list[StashEntry]:
        """Return every stashed window."""
        return list(self._stash.values())

    def stash_entry(self, window_id: str) -> StashEntry | None:
        return self._stash.get(window_id)

    def set_stash(self, entry: StashEntry) -> None:
        self._stash[entry.window_id] = entry
        self._save()

    def unstash(self, window_id: str) -> StashEntry | None:
        """Stop treating a window as stashed; return its entry if it was."""
        entry = self._stash.pop(window_id, None)
        if entry is not None:
            self._save()
        return entry

    def forget(self, window_id: str) -> None:
        """Drop everything known about a window."""
        self._original.pop(window_id, None)
        self._undo.pop(window_id, None)
        self._last.pop(window_id, None)
        self._save()

    def _load(self, path: Path) -> None:
        # A missing or damaged history only costs the ability to undo.
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError, AttributeError):
            data = json.loads(path.read_text(encoding="utf-8"))
            original = {key: Rect(*value) for key, value in data["original"].items()}
            undo = {key: [Rect(*step) for step in steps] for key, steps in data["undo"].items()}
            last = {
                key: (str(name), Rect(*rect)) for key, (name, rect) in data.get("last", {}).items()
            }
            self._last = last
            self._stash = {
                key: StashEntry(key, str(action), str(monitor), *(Rect(*rect) for rect in rects))
                for key, (action, monitor, *rects) in data.get("stash", {}).items()
            }
            self._original, self._undo = original, undo

    def _save(self) -> None:
        if self._path is None:
            return

        def encode(rect: Rect) -> list[int]:
            return [rect.x, rect.y, rect.width, rect.height]

        data = {
            "original": {key: encode(rect) for key, rect in self._original.items()},
            "undo": {key: [encode(step) for step in steps] for key, steps in self._undo.items()},
            "last": {key: [name, encode(rect)] for key, (name, rect) in self._last.items()},
            "stash": {
                key: [
                    entry.action,
                    entry.monitor,
                    encode(entry.restore),
                    encode(entry.revealed),
                    encode(entry.stashed),
                ]
                for key, entry in self._stash.items()
            },
        }
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(self._path)
