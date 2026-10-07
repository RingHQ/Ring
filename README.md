<p align="center"><img src="Linux/assets/ring-icon.svg" width="128" alt="Ring logo"></p>

# Ring

A radial window snapper for Linux.

Hold **Right Ctrl** and a ring appears around the cursor. Point in a
direction, or press a key, and a preview shows where the focused window will
go. Release to snap it there.

> **Status:** early. It works on **KDE Plasma**; other desktops are not
> supported yet.

| | Wayland | X11 |
|---|---|---|
| **Plasma 6** | yes, this is where Ring is developed | yes |
| **Plasma 5** (5.27) | untried, see below | yes |

Ring needs Python 3.12 or newer, which for Plasma 5 means Kubuntu 24.04 in
practice; Debian 12 is too old. On Plasma 5 Wayland the ring is shown through
Xwayland: pointing should work as everywhere, but keys pressed while it is
open also reach the application underneath, and which of them Ring sees is
up to KWin's "Legacy X11 App Support" setting. Nobody has run that
combination yet; reports are welcome.

## What it does

- **Point** towards a side for a half, towards a corner for a quarter.
- **Hover inside the ring** for maximize.
- **Left click** steps through sizes: half, third, two thirds.
- **Arrow keys, WASD or HJKL** do the same from the keyboard. Hold two for a
  quarter, add Shift to step backwards. **Space** maximizes, **Enter** centers.
- **Q / E / Z** stash the window behind the left, right or bottom screen edge (experimental).
  Hover the strip it leaves and it slides out; move away and it hides again.
  **R** brings it back.
- **Escape** or a right click cancels.
- Windows glide to their new place.

Many more actions can be put on any key or part of the ring: thirds, fourths,
grow, shrink, move, fill the free space, other screens and desktops, undo.
[`config/default.toml`](Linux/config/default.toml) lists them all.

## Install

### One command

```sh
curl -fsSL https://raw.githubusercontent.com/RingHQ/Ring/main/install.sh | sh
```

This installs Ring for your user, starts it and makes it start at login.
Ring itself needs no root. It does need two packages from your distribution,
PySide6 and LayerShellQt; if they are missing, the script shows the command
it is about to run and asks for your password to install them (Arch, Fedora,
Debian and Ubuntu). With `--no-packages` it never asks; install them
yourself first.

Where the distribution has no PySide6 package at all (Ubuntu 24.04), the
script fetches PySide6 from PyPI into Ring's own folder instead, about
100 MB.

Run the same command again to update. To remove Ring again (your settings
and those two packages stay):

```sh
curl -fsSL https://raw.githubusercontent.com/RingHQ/Ring/main/install.sh | sh -s -- --uninstall
```

Ring prefers your distribution's PySide6 on purpose: on Plasma 6 Wayland
only that one can draw above other windows properly. With any other PySide6
the ring is an X11 window, which is what X11 sessions and Plasma 5 use
anyway.

### AppImage

Download `Ring-x86_64.AppImage` from the
[latest release](https://github.com/RingHQ/Ring/releases), allow it to run
(right click, Properties, Permissions) and open it. A small window offers to
**Install** Ring: the `ring` command, start at login and "Ring Settings" in
your application launcher. If PySide6 or LayerShellQt are missing, it asks
for your password and installs them too. The same window can **Uninstall**
Ring again, or run it once without installing anything.

The downloaded file can be deleted afterwards. To update, download the new
one and install again.

From a terminal the same thing is:

```sh
chmod +x Ring-x86_64.AppImage
./Ring-x86_64.AppImage install
```

and `ring uninstall` removes it. For the reason above the AppImage does not
carry Qt, so it does not work on a distribution without a PySide6 package:
use the one command there.

### Arch Linux package

[`packaging/arch`](packaging/arch) has a `PKGBUILD`, which pulls in
everything Ring needs:

```sh
git clone https://github.com/RingHQ/Ring && cd Ring/packaging/arch
makepkg -si
systemctl --user enable --now ring
```

### Afterwards

Hold **Right Ctrl** and the ring appears. `ring doctor` checks your session
if it does not.

The AppImage and the one-command installer put Ring in `~/.local/share/ring`,
the `ring` command in `~/.local/bin`, a systemd user service in
`~/.config/systemd/user` and "Ring Settings" in your application launcher.
Options go after `install`, or after `sh -s --` with the one command:
`--no-service` skips the service, `--no-packages` never asks for root.

## Configure

Open **Ring Settings** from your application launcher, or run
`ring settings`: trigger, colors of the ring and the preview, sizes, what
each direction and key does, gaps, animation, stashing. The preview can be
a plain outline, or **Liquid Glass**: a pane of clear glass with a
bright rim that blurs what is behind it. **Apply** saves and
restarts Ring with the new settings.

The About tab has the **used counter**: how many windows Ring has moved for
you, and with which actions. `ring stats` prints the same.

Everything is stored in `~/.config/ring/config.toml`, which you can also
edit by hand; [`config/default.toml`](Linux/config/default.toml) documents every
option. The settings window rewrites that file, so comments in it are lost.

Ring sits in the **system tray**: left click opens the settings, the menu
shows the used counter and lets you pause Ring (middle click does that too)
or quit it. Opening **Ring Settings** starts Ring again if it is not
running. `ring trigger pause` and `resume` do the same from a script.

**Plugins** can add their own actions and react to what Ring does.
`ring plugins available` lists the ones in
[RingHQ/Plugins](https://github.com/RingHQ/Plugins), and
`ring plugins install <name>` followed by `ring plugins enable <name>` sets
one up. To write your own, see [`docs/PLUGINS.md`](docs/PLUGINS.md). The
plugin interface is new and may still change.

The trigger key is watched, not taken over, so applications still see it.
Pick a key you do not use for shortcuts, or set a delay (`trigger.delay_ms`,
"Hold for" in the settings): a quick Ctrl+C then no longer opens the ring.
`trigger.double_tap` opens it only on the second of two quick presses.

## Commands

```sh
ring run                 # the background service: trigger key + ring
ring settings            # the settings window
ring stats               # the used counter
ring plugins             # installed plugins; also available, install, enable
ring snap left_half      # apply one action to the focused window
ring trigger             # open the ring / apply, for your own key bindings
ring doctor              # check session, monitors and configuration
```

## Not there yet

- Desktops other than KDE Plasma.
- Switching focus between windows, hide, trackpad gestures.
- Packages in the distributions' own repositories and the AUR.

## Development

```sh
git clone https://github.com/RingHQ/Ring && cd Ring/Linux
python -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check . && .venv/bin/mypy
.venv/bin/ring run
```

## Credits and license

Ring's actions, window math, cycle rules, ring behavior, defaults and
animation curves are ported from [Loop](https://github.com/MrKai77/Loop) by
MrKai77 and contributors.

Ring is licensed under the [GNU GPL v3](LICENSE).
