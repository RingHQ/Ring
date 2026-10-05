# Writing plugins for Ring

A plugin is a Python file that adds actions to Ring or reacts to what Ring
does. The plugin interface is new and may still change; `api.version` tells
you which revision you are running against (currently 1).

## Where plugins live

- A file `~/.config/looplinux/plugins/<name>.py`, or a package
  `~/.config/looplinux/plugins/<name>/__init__.py`.
- Or an installed Python package with an entry point in the
  `looplinux.plugins` group:

  ```toml
  [project.entry-points."looplinux.plugins"]
  myplugin = "my_package.ring_plugin"
  ```

A plugin does nothing until it is enabled, in the settings window under
**Plugins** or in the configuration file:

```toml
[plugins]
enabled = ["example"]
```

Plugins run inside Ring with your full permissions. Only enable code you
trust.

## The shape of a plugin

```python
"""One line saying what this plugin does (shown in the settings window)."""

from looplinux.actions import Rect


def small(context):
    area = context.area
    width, height = round(area.width * 0.6), round(area.height * 0.6)
    return Rect(
        area.x + (area.width - width) // 2, area.y + (area.height - height) // 2, width, height
    )


def register(api):
    api.add_action("small", small, label="Small and centered")
```

`register(api)` is called once when Ring starts.

## Actions

`api.add_action(name, handler, label="")` adds an action called
`<plugin>.<name>`, here `example.small`. It can be used wherever a built-in
action can: on a key, on a direction of the ring, inside a cycle, or with
`looplinux snap example.small`.

The handler receives a context with:

| Attribute | What it is |
|---|---|
| `context.window` | The window to act on: `id`, `app_id`, `title`, `geometry`, `fullscreen` |
| `context.monitor` | The monitor in use: `name`, `geometry` |
| `context.area` | That monitor without panels, as a `Rect` |
| `context.backend` | The window backend, for anything beyond moving one window |

Return a `Rect(x, y, width, height)` and Ring moves the window there, with
animation, undo and the used counter all working as for built-in actions.
Return `None` if the handler did everything itself through
`context.backend` (`get_windows()`, `set_geometry()`, `minimize()`, ...).

Plugin actions have no preview in the ring yet.

## Events

`api.on(event, callback)` calls `callback` with keyword arguments:

| Event | Arguments |
|---|---|
| `menu_opened` | `window` |
| `menu_closed` | `applied` (bool) |
| `action_applied` | `action` (its name), `window`, `rect` (new frame or `None`) |

An exception in a callback is logged and otherwise ignored.

## Trying it

[`examples/plugins/example.py`](../examples/plugins/example.py) is a complete
plugin. Errors while loading a plugin show up in the settings window and in
`journalctl --user -u looplinux`.
