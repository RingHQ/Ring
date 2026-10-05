# Writing plugins for Ring

A plugin is a Python file that adds actions to Ring or reacts to what Ring
does. The plugin interface is new and may still change; `api.version` tells
you which revision you are running against (currently 2; `set_colors` came
with 2).

## Getting plugins

Ready-made plugins are collected in <https://github.com/RingHQ/Plugins>.
The settings window lists them under **Plugins**: Install, then Enable;
later Disable or Uninstall. From a terminal:

```sh
ring plugins available        # what is in that repository
ring plugins install tile     # copy one to ~/.config/ring/plugins/
ring plugins enable tile      # load it
ring plugins                  # what is installed, and what is enabled
```

`ring plugins update <name>` fetches the current version and
`ring plugins remove <name>` deletes a plugin. `enable` and `disable` rewrite
the configuration file, like the settings window does.

## Where plugins live

- A folder `~/.config/ring/plugins/<name>/` holding `plugin.py` and a
  `README.md`. The first heading of the README is the plugin's name and the
  first paragraph after it its description; both are shown in the settings
  window. This is the layout of the plugin repository, where every plugin
  is such a folder. The folder may hold more Python files, imported with
  `from . import ...`.
- A single file `~/.config/ring/plugins/<name>.py`, described by the first
  line of its docstring.
- Or an installed Python package with an entry point in the
  `ring.plugins` group:

  ```toml
  [project.entry-points."ring.plugins"]
  myplugin = "my_package.ring_plugin"
  ```

`<name>` is lowercase letters, digits and `_`; it is the first half of the
names of the plugin's actions.

A plugin does nothing until it is enabled, with `ring plugins enable`, in
the settings window under **Plugins**, or in the configuration file:

```toml
[plugins]
enabled = ["example"]
```

Plugins run inside Ring with your full permissions. Only enable code you
trust.

## The shape of a plugin

```python
"""One line saying what this plugin does (shown in the settings window)."""

from ring.actions import Rect


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
`ring snap example.small`.

The handler receives a context with:

| Attribute | What it is |
|---|---|
| `context.window` | The window to act on: `id`, `app_id`, `title`, `geometry`, `fullscreen` |
| `context.monitor` | The monitor in use: `name`, `geometry` |
| `context.area` | That monitor without panels, as a `Rect` |
| `context.backend` | The window backend, for anything beyond moving one window |
| `context.gaps` | The gaps set for that monitor: `outer`, `inner` |

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

## Colors

`api.set_colors(accent=None, gradient=None, ring=None)` recolors the ring
until Ring restarts. Colors are `"#RRGGBB"`; what is left out stays as it is.

| Argument | What it colors |
|---|---|
| `accent` | The lit segment, and the preview border unless that has its own color |
| `gradient` | What the accent fades to; the accent itself if left out |
| `ring` | The ring where it is not lit (the configured opacity still applies) |

Call it from an event, for example `menu_opened`. While `register` runs
there is no ring yet, so a call there does nothing. A plugin that animates
may run a timer while the menu is open; stop it in `menu_closed`, because
Ring must not use the processor while idle.

## Trying it

The plugins in the [plugin repository](https://github.com/RingHQ/Plugins)
are complete examples; `presets` is the shortest. To try a plugin
folder before it is published, `ring plugins install <name> --from <folder>`
takes it from a local copy of the repository. Errors while loading a plugin show up in the settings window and in
`journalctl --user -u ring`.
