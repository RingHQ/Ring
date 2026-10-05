<p align="center"><img src="assets/ring-icon.svg" width="128" alt="Ring logo"></p>

# Ring

A radial window snapper for Linux: a port of [Loop](https://github.com/MrKai77/Loop)
for macOS.

Hold **Right Ctrl** and a ring appears around the cursor. Point in a
direction, or press a key, and a preview shows where the focused window will
go. Release to snap it there.

> **Status:** early. It works on **KDE Plasma 6 (Wayland)** only. Other
> desktops are not supported yet.

## What it does

- **Point** towards a side for a half, towards a corner for a quarter.
- **Hover inside the ring** for maximize.
- **Left click** steps through sizes: half, third, two thirds.
- **Arrow keys, WASD or HJKL** do the same from the keyboard. Hold two for a
  quarter, add Shift to step backwards. **Space** maximizes, **Enter** centers.
- !!WIP!! **Q / E / Z** stash the window behind the left, right or bottom screen edge.
  Hover the strip it leaves and it slides out; move away and it hides again. 
  **R** brings it back.
- **Escape** or a right click cancels.
- Windows glide to their new place.

Many more actions can be put on any key or part of the ring: thirds, fourths,
grow, shrink, move, fill the free space, other screens and desktops, undo.
[`config/default.toml`](config/default.toml) lists them all.

## Install

Requirements, from your distribution's packages (Arch names):
`pyside6`, `layer-shell-qt`, and Xwayland.

```sh
git clone <this repository> ring && cd ring
python -m venv --system-site-packages .venv
.venv/bin/pip install .
.venv/bin/looplinux doctor    # checks your session
.venv/bin/looplinux run       # start it
```

The venv must see the system packages: the PyPI build of PySide6 cannot draw
above other windows on Wayland.

To start it at login, copy `packaging/looplinux.service` to
`~/.config/systemd/user/`, point `ExecStart` at your `looplinux`, and run
`systemctl --user enable --now looplinux`.

The command and the Python package are still called `looplinux`.

## Configure

Run `looplinux settings` for the settings window: trigger, colors of the ring
and the preview, sizes, what each direction and key does, gaps, animation,
stashing. **Apply** saves and restarts Ring with the new settings. To get it
in your application launcher, copy `packaging/looplinux-settings.desktop` to
`~/.local/share/applications/` and `assets/ring-icon.svg` to
`~/.local/share/icons/hicolor/scalable/apps/looplinux.svg`.

The About tab has the **used counter**: how many windows Ring has moved for
you, and with which actions. `looplinux stats` prints the same.

Everything is stored in `~/.config/looplinux/config.toml`, which you can also
edit by hand; [`config/default.toml`](config/default.toml) documents every
option. The settings window rewrites that file, so comments in it are lost.

Ring sits in the **system tray**: left click opens the settings, the menu
shows the used counter and lets you pause Ring (middle click does that too)
or quit it. `looplinux trigger pause` and `resume` do the same from a script.

**Plugins** can add their own actions and react to what Ring does; see
[`docs/PLUGINS.md`](docs/PLUGINS.md) and the example in `examples/plugins/`.
The plugin interface is new and may still change.

The trigger key is watched, not taken over, so applications still see it.
Pick a key you do not use for shortcuts.

## Commands

```sh
looplinux run                 # the background service: trigger key + ring
looplinux settings            # the settings window
looplinux stats               # the used counter
looplinux snap left_half      # apply one action to the focused window
looplinux trigger             # open the ring / apply, for your own key bindings
looplinux doctor              # check session, monitors and configuration
```

## Not there yet

- Desktops other than KDE Plasma 6 on Wayland.
- From Loop: switching focus between windows, hide, trackpad gestures.
- Distribution packages.

## Development

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/mypy
```

## Credits and license

Ring's actions, window math, cycle rules, ring behavior, defaults and
animation curves are ported from [Loop](https://github.com/MrKai77/Loop) by
MrKai77 and contributors.

Like Loop, Ring is licensed under the [GNU GPL v3](LICENSE).
