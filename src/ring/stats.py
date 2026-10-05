"""The usage counter: how often Ring has moved a window, and with what."""

import contextlib
import json
import os
from datetime import date
from pathlib import Path


def default_stats_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "ring" / "stats.json"


class Stats:
    """Counts applied actions, in total and per action, across sessions."""

    def __init__(self, path: Path | None = None) -> None:
        """Open the counter stored at `path`, or keep it in memory if None."""
        self._path = path
        self.total = 0
        self.actions: dict[str, int] = {}
        self.since = date.today().isoformat()
        self.reload()

    def reload(self) -> None:
        """Read the counts again; another process may have added to them."""
        if self._path is None:
            return
        # A missing or damaged file simply starts the count over.
        with contextlib.suppress(OSError, ValueError, TypeError, KeyError, AttributeError):
            data = json.loads(self._path.read_text(encoding="utf-8"))
            actions = {str(name): int(count) for name, count in data["actions"].items()}
            self.total, self.since = int(data["total"]), str(data["since"])
            self.actions = actions

    def record(self, action: str) -> None:
        """Count one use of `action`."""
        self.reload()
        self.total += 1
        self.actions[action] = self.actions.get(action, 0) + 1
        self._save()

    def reset(self) -> None:
        self.total, self.actions, self.since = 0, {}, date.today().isoformat()
        self._save()

    def top(self, count: int) -> list[tuple[str, int]]:
        """Return the most used actions, most used first."""
        ranked = sorted(self.actions.items(), key=lambda item: (-item[1], item[0]))
        return ranked[:count]

    def _save(self) -> None:
        if self._path is None:
            return
        data = {"total": self.total, "since": self.since, "actions": self.actions}
        with contextlib.suppress(OSError):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(data), encoding="utf-8")
            temporary.replace(self._path)
