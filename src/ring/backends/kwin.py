"""KDE Plasma backend, driven through KWin's scripting D-Bus interface.

KWin has no D-Bus API to move arbitrary windows, but it can load and run a
JavaScript snippet with full access to its window list. Each request below is
such a snippet; it reports its result by calling back to this process over
D-Bus.

Requires Plasma 6.
"""

import json
import os
import secrets
import tempfile
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from jeepney import (
    DBusAddress,
    MatchRule,
    Message,
    MessageType,
    new_method_call,
    new_method_return,
)
from jeepney.io.blocking import DBusConnection, open_dbus_connection

from ring.actions import Rect
from ring.backends.base import (
    BackendError,
    Monitor,
    Scene,
    StashEntry,
    Window,
    WindowBackend,
)

_SCRIPTING = DBusAddress("/Scripting", bus_name="org.kde.KWin", interface="org.kde.kwin.Scripting")
_CALLBACK_INTERFACE = "io.github.ringhq.Ring.KWinCallback"
_CALLBACK_MEMBER = "reply"
_TIMEOUT = 3.0
_STASH_PLUGIN = "ring-stash"
# How long past its nominal end an animation script may still be running.
_ANIMATION_SLACK = 0.15

# Helpers available to every request body. `args` holds the request arguments.
_PRELUDE = """
function rect(r) {
    return {
        x: Math.round(r.x),
        y: Math.round(r.y),
        width: Math.round(r.width),
        height: Math.round(r.height),
    };
}
function describe(w) {
    return {
        id: String(w.internalId),
        app_id: String(w.resourceClass),
        title: String(w.caption),
        geometry: rect(w.frameGeometry),
        fullscreen: Boolean(w.fullScreen),
    };
}
function find(id) {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        if (String(windows[i].internalId) === id) {
            return windows[i];
        }
    }
    throw new Error("the window no longer exists");
}
// Cubic bezier easing through (0,0), (x1,y1), (x2,y2), (1,1).
function bezier(x1, y1, x2, y2) {
    function along(a, b, s) {
        return 3 * (1 - s) * (1 - s) * s * a + 3 * (1 - s) * s * s * b + s * s * s;
    }
    return function (t) {
        var low = 0, high = 1, s = t;
        for (var i = 0; i < 24; i++) {
            if (along(x1, x2, s) < t) {
                low = s;
            } else {
                high = s;
            }
            s = (low + high) / 2;
        }
        return along(y1, y2, s);
    };
}
var moving = {};
// Give a window a new frame, gliding there if an animation is given. `done`
// is called once a glide has ended, however it ended.
function moveTo(w, target, animation, done) {
    var id = String(w.internalId);
    if (moving[id]) {
        moving[id].stop();
        delete moving[id];
    }
    if (!animation || animation.duration_ms <= 0) {
        w.frameGeometry = target;
        return;
    }
    var from = rect(w.frameGeometry);
    var c = animation.curve;
    var ease = bezier(c[0], c[1], c[2], c[3]);
    var started = Date.now();
    var timer = new QTimer();
    function finish() {
        timer.stop();
        delete moving[id];
        if (done) {
            done();
        }
    }
    timer.interval = 8;
    timer.timeout.connect(function () {
        if (w.deleted) {
            finish();
            return;
        }
        var t = Math.min((Date.now() - started) / animation.duration_ms, 1);
        if (t >= 1) {
            w.frameGeometry = target;
            finish();
            return;
        }
        var k = ease(t);
        w.frameGeometry = {
            x: Math.round(from.x + (target.x - from.x) * k),
            y: Math.round(from.y + (target.y - from.y) * k),
            width: Math.round(from.width + (target.width - from.width) * k),
            height: Math.round(from.height + (target.height - from.height) * k),
        };
    });
    moving[id] = timer;
    timer.start();
}
function output(name) {
    var screens = workspace.screens;
    for (var i = 0; i < screens.length; i++) {
        if (screens[i].name === name) {
            return screens[i];
        }
    }
    throw new Error("monitor " + name + " is no longer connected");
}
"""

_WRAPPER = """
(function () {{
    function send(payload) {{
        callDBus({service}, "/", {interface}, {member}, {token}, JSON.stringify(payload));
    }}
    try {{
        var args = {args};
        {prelude}
        send({{ok: true, value: (function () {{ {body} }})()}});
    }} catch (error) {{
        send({{ok: false, error: String(error && error.message ? error.message : error)}});
    }}
}})();
"""

_ACTIVE_WINDOW = """
var w = workspace.activeWindow;
// Our own overlay is a focused layer-shell surface while the menu is open.
if (!w || w.specialWindow || w.deleted || w.pid === args.pid) {
    return null;
}
return describe(w);
"""

_WINDOWS = """
var result = [];
var windows = workspace.windowList();
for (var i = 0; i < windows.length; i++) {
    var w = windows[i];
    if (!w.normalWindow || w.minimized || w.deleted || w.pid === args.pid) {
        continue;
    }
    var here = w.onAllDesktops;
    for (var j = 0; j < w.desktops.length && !here; j++) {
        here = w.desktops[j] === workspace.currentDesktop;
    }
    if (here) {
        result.push(describe(w));
    }
}
return result;
"""

_MOVE_TO_DESKTOP = """
var w = find(args.id);
var desktops = workspace.desktops;
var index = 0;
for (var i = 0; i < desktops.length; i++) {
    if (desktops[i] === workspace.currentDesktop) {
        index = i;
    }
}
var count = desktops.length;
var target = desktops[((index + args.offset) % count + count) % count];
w.desktops = [target];
workspace.currentDesktop = target;
return null;
"""

_MONITORS = """
return workspace.screens.map(function (screen) {
    return {name: String(screen.name), geometry: rect(screen.geometry)};
});
"""

_WORK_AREA = """
return rect(workspace.clientArea(KWin.MaximizeArea, output(args.name), workspace.currentDesktop));
"""

_CURSOR = """
return {x: Math.round(workspace.cursorPos.x), y: Math.round(workspace.cursorPos.y)};
"""

# Everything the menu needs to open, in one round trip.
_SCENE = """
var w = workspace.activeWindow;
var p = {x: Math.round(workspace.cursorPos.x), y: Math.round(workspace.cursorPos.y)};
var screens = workspace.screens;
var under = screens[0];
for (var i = 0; i < screens.length; i++) {
    var g = screens[i].geometry;
    if (p.x >= g.x && p.x < g.x + g.width && p.y >= g.y && p.y < g.y + g.height) {
        under = screens[i];
        break;
    }
}
if (!under) {
    throw new Error("no monitors found");
}
return {
    // Our own overlay is a focused layer-shell surface while the menu is open.
    window: !w || w.specialWindow || w.deleted || w.pid === args.pid ? null : describe(w),
    cursor: p,
    monitors: screens.map(function (screen) {
        return {name: String(screen.name), geometry: rect(screen.geometry)};
    }),
    monitor: String(under.name),
    area: rect(workspace.clientArea(KWin.MaximizeArea, under, workspace.currentDesktop)),
};
"""

# With an animation the script stays loaded while the window glides, and
# unloads itself from inside KWin when it is done.
_SET_GEOMETRY = """
var w = find(args.id);
if (w.fullScreen) {
    w.fullScreen = false;
}
w.setMaximize(false, false);
moveTo(w, args.target, args.animation, function () {
    callDBus("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "unloadScript", args.plugin);
});
return null;
"""

# Stays loaded for as long as any window is stashed. `args.entries` holds each
# window's id with its revealed and stashed frames; `args.hide` the ids to
# tuck away immediately.
_STASH_WATCHER = """
var entries = args.entries;
var shown = {};
// When each window was last hidden. Focus often comes straight back to a
// window that was just stashed, for instance when the radial menu closes;
// that must not count as the user asking for it.
var hiddenAt = {};
var GRACE_MS = 700;

function lookup(id) {
    try {
        return find(id);
    } catch (error) {
        return null;
    }
}
function inside(r, p, slack) {
    return p.x >= r.x - slack && p.x < r.x + r.width + slack
        && p.y >= r.y - slack && p.y < r.y + r.height + slack;
}
function isStashed(w) {
    var id = String(w.internalId);
    for (var i = 0; i < entries.length; i++) {
        if (entries[i].window_id === id) {
            return true;
        }
    }
    return false;
}
function focusAnother(w) {
    var order = workspace.stackingOrder;
    for (var i = order.length - 1; i >= 0; i--) {
        var other = order[i];
        if (other !== w && other.normalWindow && !other.minimized && !other.deleted
                && !isStashed(other) && other.output === w.output) {
            workspace.activeWindow = other;
            return;
        }
    }
}
function reveal(entry, w) {
    shown[entry.window_id] = true;
    if (args.focus) {
        workspace.activeWindow = w;
    }
    moveTo(w, entry.revealed, args.animation);
}
function hide(entry, w, refocus) {
    shown[entry.window_id] = false;
    hiddenAt[entry.window_id] = Date.now();
    moveTo(w, entry.stashed, args.animation);
    if (refocus && args.focus && workspace.activeWindow === w) {
        focusAnother(w);
    }
}
function hideOthers(entry) {
    for (var i = 0; i < entries.length; i++) {
        var other = entries[i];
        var w = shown[other.window_id] && other !== entry ? lookup(other.window_id) : null;
        if (w) {
            hide(other, w, false);
        }
    }
}
function check() {
    var p = workspace.cursorPos;
    for (var i = 0; i < entries.length; i++) {
        var entry = entries[i];
        var w = lookup(entry.window_id);
        if (!w || w.minimized) {
            continue;
        }
        if (shown[entry.window_id]) {
            if (!inside(entry.revealed, p, 15) && !inside(entry.stashed, p, 0)) {
                hide(entry, w, true);
            } else {
                return;
            }
        } else if (inside(entry.stashed, p, 0)) {
            hideOthers(entry);
            reveal(entry, w);
            return;
        }
    }
}

var missing = [];
for (var i = 0; i < entries.length; i++) {
    var entry = entries[i];
    var w = lookup(entry.window_id);
    if (!w) {
        missing.push(entry.window_id);
        continue;
    }
    if (args.hide.indexOf(entry.window_id) >= 0) {
        if (w.fullScreen) {
            w.fullScreen = false;
        }
        w.setMaximize(false, false);
        hide(entry, w, true);
    } else {
        var frame = rect(w.frameGeometry);
        shown[entry.window_id] = Math.abs(frame.x - entry.stashed.x) > 2
            || Math.abs(frame.y - entry.stashed.y) > 2;
    }
}

// Pointer motion arrives in bursts; act once it has settled for a moment.
var settle = new QTimer();
settle.singleShot = true;
settle.interval = 50;
settle.timeout.connect(check);
workspace.cursorPosChanged.connect(function () {
    settle.start();
});
var handBack = new QTimer();
handBack.singleShot = true;
handBack.interval = 1;
var handBackFrom = null;
handBack.timeout.connect(function () {
    if (handBackFrom && !handBackFrom.deleted && workspace.activeWindow === handBackFrom) {
        focusAnother(handBackFrom);
    }
});
workspace.windowActivated.connect(function (w) {
    if (!w) {
        return;
    }
    var id = String(w.internalId);
    for (var i = 0; i < entries.length; i++) {
        if (entries[i].window_id !== id || shown[id]) {
            continue;
        }
        if (Date.now() - (hiddenAt[id] || 0) < GRACE_MS) {
            // Not from inside this handler: KWin is still switching focus.
            if (args.focus) {
                handBackFrom = w;
                handBack.start();
            }
            return;
        }
        hideOthers(entries[i]);
        reveal(entries[i], w);
    }
});
return missing;
"""

_SET_FULLSCREEN = """
find(args.id).fullScreen = args.fullscreen;
return null;
"""

_MINIMIZE = """
find(args.id).minimized = true;
return null;
"""


def _to_rect(data: dict[str, Any]) -> Rect:
    return Rect(int(data["x"]), int(data["y"]), int(data["width"]), int(data["height"]))


def _to_window(data: dict[str, Any]) -> Window:
    return Window(
        id=data["id"],
        app_id=data["app_id"],
        title=data["title"],
        geometry=_to_rect(data["geometry"]),
        fullscreen=data["fullscreen"],
    )


class KWinBackend(WindowBackend):
    name = "kwin"

    def __init__(self) -> None:
        try:
            self._connection: DBusConnection = open_dbus_connection(bus="SESSION")
        except Exception as error:
            raise BackendError(
                f"Cannot connect to the session D-Bus ({error}). "
                "Run Ring from inside your Plasma session."
            ) from error
        # Windows still gliding: window id -> (plugin name of the script, time it ends).
        self._animating: dict[str, tuple[str, float]] = {}
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        self._script_dir = Path(runtime_dir)

    def close(self) -> None:
        # Animations still running finish and unload themselves inside KWin.
        self._connection.close()

    def get_active_window(self) -> Window | None:
        data = self._run(_ACTIVE_WINDOW, pid=os.getpid())
        return None if data is None else _to_window(data)

    def get_windows(self) -> list[Window]:
        return [_to_window(item) for item in self._run(_WINDOWS, pid=os.getpid())]

    def get_monitors(self) -> list[Monitor]:
        return [Monitor(item["name"], _to_rect(item["geometry"])) for item in self._run(_MONITORS)]

    def get_work_area(self, monitor: Monitor) -> Rect:
        return _to_rect(self._run(_WORK_AREA, name=monitor.name))

    def get_cursor_position(self) -> tuple[int, int]:
        data = self._run(_CURSOR)
        return int(data["x"]), int(data["y"])

    def get_scene(self) -> Scene:
        data = self._run(_SCENE, pid=os.getpid())
        monitors = [Monitor(item["name"], _to_rect(item["geometry"])) for item in data["monitors"]]
        return Scene(
            window=None if data["window"] is None else _to_window(data["window"]),
            cursor=(int(data["cursor"]["x"]), int(data["cursor"]["y"])),
            monitors=monitors,
            monitor=next(monitor for monitor in monitors if monitor.name == data["monitor"]),
            area=_to_rect(data["area"]),
        )

    def set_geometry(self, window: Window, rect: Rect) -> None:
        animation = self.animation
        # A glide still under way is cut short; the new one continues from
        # wherever the window has got to.
        self._stop_animation(window.id)
        if animation is None:
            self._run(_SET_GEOMETRY, id=window.id, target=asdict(rect), animation=None)
            return
        plugin = f"ring-{secrets.token_hex(8)}"
        self._run(
            _SET_GEOMETRY,
            plugin=plugin,
            keep=True,
            id=window.id,
            target=asdict(rect),
            animation=asdict(animation),
        )
        ends = time.monotonic() + animation.duration_ms / 1000 + _ANIMATION_SLACK
        self._animating[window.id] = (plugin, ends)

    def watch_stash(self, entries: Sequence[StashEntry], hide: Sequence[str] = ()) -> Sequence[str]:
        self._call(_SCRIPTING, "unloadScript", "s", _STASH_PLUGIN)
        if not entries:
            return ()
        # The watcher moves these itself.
        for window_id in hide:
            self._stop_animation(window_id)
        animation = self.stash_animation
        missing = self._run(
            _STASH_WATCHER,
            plugin=_STASH_PLUGIN,
            keep=True,
            entries=[asdict(entry) for entry in entries],
            hide=list(hide),
            focus=self.stash_shift_focus,
            animation=None if animation is None else asdict(animation),
        )
        return [str(window_id) for window_id in missing]

    def set_fullscreen(self, window: Window, fullscreen: bool) -> None:
        self._run(_SET_FULLSCREEN, id=window.id, fullscreen=fullscreen)

    def minimize(self, window: Window) -> None:
        self._run(_MINIMIZE, id=window.id)

    def move_to_desktop(self, window: Window, offset: int) -> None:
        self._run(_MOVE_TO_DESKTOP, id=window.id, offset=offset)

    def _call(self, address: DBusAddress, method: str, signature: str = "", *body: object) -> Any:
        """Call a KWin D-Bus method and return the first value of its reply."""
        message = new_method_call(address, method, signature or None, body)
        try:
            reply = self._connection.send_and_get_reply(message, timeout=_TIMEOUT)
        except Exception as error:
            raise BackendError(f"KWin did not answer on D-Bus ({method}): {error}") from error
        if reply.header.message_type is MessageType.error:
            detail = reply.body[0] if reply.body else "unknown error"
            raise BackendError(f"KWin rejected the D-Bus call {method}: {detail}")
        return reply.body[0] if reply.body else None

    def _stop_animation(self, window_id: str) -> None:
        """Unload the script that is still gliding a window, if there is one."""
        now = time.monotonic()
        plugin, ends = self._animating.pop(window_id, ("", 0.0))
        if ends > now:
            self._call(_SCRIPTING, "unloadScript", "s", plugin)
        # Glides that ran to their end have unloaded themselves.
        self._animating = {key: value for key, value in self._animating.items() if value[1] > now}

    def _run(
        self,
        body: str,
        *,
        plugin: str | None = None,
        keep: bool = False,
        **args: object,
    ) -> Any:
        """Run a script body inside KWin and return the value it returns.

        The script is unloaded as soon as it has answered, unless `keep` says
        it has timers or signal handlers that must live on; it then stays
        until it unloads itself or someone unloads `plugin` by name. The
        script finds its own plugin name in `args.plugin`.
        """
        token = secrets.token_hex(8)
        # Unique across processes and backends: KWin refuses a name twice.
        plugin = plugin or f"ring-{token}"
        unload = not keep
        source = _WRAPPER.format(
            service=json.dumps(self._connection.unique_name),
            interface=json.dumps(_CALLBACK_INTERFACE),
            member=json.dumps(_CALLBACK_MEMBER),
            token=json.dumps(token),
            args=json.dumps({**args, "plugin": plugin}),
            prelude=_PRELUDE,
            body=body,
        )
        rule = MatchRule(type="method_call", interface=_CALLBACK_INTERFACE, member=_CALLBACK_MEMBER)
        with tempfile.NamedTemporaryFile(
            "w", dir=self._script_dir, prefix="ring-", suffix=".js", encoding="utf-8"
        ) as file:
            file.write(source)
            file.flush()
            # Open the filter before running so an early callback is not dropped.
            with self._connection.filter(rule, queue=deque()) as replies:
                script_id = self._call(_SCRIPTING, "loadScript", "ss", file.name, plugin)
                if not isinstance(script_id, int) or script_id < 0:
                    raise BackendError("KWin refused to load the Ring helper script")
                try:
                    script = DBusAddress(
                        f"/Scripting/Script{script_id}",
                        bus_name="org.kde.KWin",
                        interface="org.kde.kwin.Script",
                    )
                    self._call(script, "run")
                    payload = self._wait_for_reply(replies, token)
                    unload = unload or not payload.get("ok")
                except BackendError:
                    unload = True
                    raise
                finally:
                    if unload:
                        self._call(_SCRIPTING, "unloadScript", "s", plugin)
        if not payload.get("ok"):
            raise BackendError(f"KWin could not complete the request: {payload.get('error')}")
        return payload.get("value")

    def _wait_for_reply(self, replies: deque[Message], token: str) -> dict[str, Any]:
        while True:
            try:
                message = self._connection.recv_until_filtered(replies, timeout=_TIMEOUT)
            except TimeoutError:
                raise BackendError(
                    "KWin ran the Ring helper script but never reported back. "
                    "Check `journalctl --user -b -g kwin` for script errors."
                ) from None
            self._connection.send(new_method_return(message))
            if len(message.body) == 2 and message.body[0] == token:
                payload = json.loads(message.body[1])
                if isinstance(payload, dict):
                    return payload
