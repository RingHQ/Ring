#!/bin/sh
# Install Ring for the current user. No root needed.
#
#   curl -fsSL https://raw.githubusercontent.com/RingHQ/Ring/main/install.sh | sh
#
# To pass options through the pipe:  ... | sh -s -- --no-service
#
#   --no-service   do not set Ring up to start at login
#   --no-packages  do not install missing distribution packages (needs sudo)
#   --uninstall    remove what this script installed (your settings stay)
#
# RING_REF picks what to install: a branch or a tag such as v0.2.1 (default
# main). RING_SOURCE installs from a local checkout instead of downloading.
#
# The AppImage runs this same script for its own "install" and "uninstall",
# with RING_APPIMAGE naming the AppImage file: that file is then kept instead
# of building a Python environment.
set -eu

REPOSITORY="https://github.com/RingHQ/Ring"
REF="${RING_REF:-main}"

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN="$HOME/.local/bin"
VENV="$DATA/ring/venv"
PACKED="$DATA/ring/Ring.AppImage"
UNIT="$CONFIG/systemd/user/ring.service"
LAUNCHER="$DATA/applications/ring-settings.desktop"
ICON="$DATA/icons/hicolor/scalable/apps/ring.svg"

service=yes
packages=yes
uninstall=no
for option in "$@"; do
    case "$option" in
        --no-service) service=no ;;
        --no-packages) packages=no ;;
        --uninstall) uninstall=yes ;;
        *) echo "install.sh: unknown option $option" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
fail() { printf 'Ring was not installed: %s\n' "$*" >&2; exit 1; }
has_systemd() { command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; }

if [ "$uninstall" = yes ]; then
    if [ -f "$UNIT" ] && has_systemd; then
        systemctl --user disable --now ring.service >/dev/null 2>&1 || true
    fi
    rm -f "$UNIT" "$LAUNCHER" "$ICON"
    if [ -L "$BIN/ring" ]; then
        case "$(readlink "$BIN/ring")" in
            "$VENV/bin/ring" | "$PACKED") rm -f "$BIN/ring" ;;
        esac
    fi
    rm -rf "$DATA/ring"
    if has_systemd; then
        systemctl --user daemon-reload || true
    fi
    say "Ring is removed. Your settings are still in $CONFIG/ring."
    exit 0
fi

# What Ring needs from the system. On Plasma 6 Wayland the ring is a
# layer-shell surface, which only the distribution's Qt can make: PySide6 then
# has to be the distribution's package, with LayerShellQt. Everywhere else
# (X11, Plasma 5) the ring is an X11 window, and any PySide6 will do.
PYTHON=/usr/bin/python3
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || fail "python3 was not found."

"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
    || fail "Python 3.12 or newer is needed; $PYTHON is older."

has_pyside() {
    "$PYTHON" -c 'import PySide6.QtNetwork, PySide6.QtQml, PySide6.QtQuick, PySide6.QtWidgets' \
        2>/dev/null
}
has_layershell() {
    "$PYTHON" -c '
import pathlib, sys
from PySide6.QtCore import QLibraryInfo
imports = pathlib.Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))
sys.exit(not (imports / "org" / "kde" / "layershell").is_dir())
' 2>/dev/null
}
# The AppImage needs no Python environment of its own.
has_venv() { [ -n "${RING_APPIMAGE:-}" ] || "$PYTHON" -c 'import venv, ensurepip' 2>/dev/null; }
has_library() { ldconfig -p 2>/dev/null | grep -q "$1"; }

needs_layershell=no
case "$(printf %s "${XDG_SESSION_TYPE:-}" | tr '[:upper:]' '[:lower:]'):${KDE_SESSION_VERSION:-}" in
    wayland:5) ;;
    wayland:*) needs_layershell=yes ;;
esac

# How this distribution installs them; empty where that is not known. `pypi`
# is yes where the distribution has no PySide6 at all, so that it has to come
# from PyPI: the X11 kind of ring is all that one can draw.
get=""
pypi=no
if command -v pacman >/dev/null 2>&1; then
    get="pacman -S --needed --noconfirm pyside6 layer-shell-qt"
elif command -v dnf >/dev/null 2>&1; then
    get="dnf install -y python3-pyside6 layer-shell-qt"
elif command -v apt-get >/dev/null 2>&1; then
    if apt-cache show python3-pyside6.qtcore >/dev/null 2>&1; then
        get="apt-get install -y python3-venv python3-pyside6.qtcore python3-pyside6.qtgui"
        get="$get python3-pyside6.qtnetwork python3-pyside6.qtqml python3-pyside6.qtquick"
        get="$get python3-pyside6.qtwidgets qml6-module-qtquick qml6-module-qtquick-window"
        get="$get qml6-module-org-kde-layershell"
    else
        # Ubuntu 24.04 and older. Qt from PyPI needs this one library.
        get="apt-get install -y python3-venv libxcb-cursor0"
        pypi=yes
    fi
else
    pypi=yes
fi
# RING_SUDO: what asks for the password. The graphical installer has no
# terminal for sudo to ask in, and names pkexec.
[ "$(id -u)" -eq 0 ] || get="${get:+${RING_SUDO:-sudo} $get}"
how="${get:-PySide6 and LayerShellQt from your distribution's packages}"

if [ "$pypi" = yes ]; then
    complete() { has_venv && { has_pyside || has_library libxcb-cursor.so.0; }; }
else
    complete() { has_venv && has_pyside && has_layershell; }
fi
if [ "$packages" = yes ] && [ -n "$get" ] && ! complete; then
    say "Ring needs a few packages from your distribution. Installing what is missing:"
    say "    $get"
    # Not from this script's input: piped into sh, that is the script itself.
    $get </dev/null || fail "the packages could not be installed. Install them yourself:
    $how"
fi

has_venv || fail "Python's venv module is missing. Install it (Debian and Ubuntu: python3-venv)."
if [ "$pypi" = no ]; then
    has_pyside || fail "PySide6 is missing. Install it first:
    $how"
    [ "$needs_layershell" = no ] || has_layershell || fail "LayerShellQt is missing. Install it first:
    $how"
elif ! has_pyside && [ -n "${RING_APPIMAGE:-}" ]; then
    fail "this distribution has no PySide6 package, which the AppImage relies on. Use the
installer instead, which fetches PySide6 from PyPI:
    curl -fsSL https://raw.githubusercontent.com/RingHQ/Ring/main/install.sh | sh"
fi

case "${XDG_CURRENT_DESKTOP:-}" in
    *KDE*) ;;
    *) say "Note: Ring only works on KDE Plasma. This does not look like it." ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

if [ -n "${RING_APPIMAGE:-}" ]; then
    # The AppImage brings the files to copy along.
    source="$RING_SOURCE"
elif [ -n "${RING_SOURCE:-}" ]; then
    source="$RING_SOURCE"
    [ -f "$source/Linux/pyproject.toml" ] || fail "$source is not a checkout of Ring."
else
    say "Downloading Ring ($REF) ..."
    command -v curl >/dev/null 2>&1 || fail "curl was not found."
    curl -fsSL "$REPOSITORY/archive/$REF.tar.gz" -o "$work/ring.tar.gz" \
        || fail "could not download $REPOSITORY/archive/$REF.tar.gz"
    mkdir "$work/source"
    tar -xzf "$work/ring.tar.gz" -C "$work/source" --strip-components=1
    source="$work/source"
fi

say "Installing into $DATA/ring ..."
# The service must not run while its files are replaced.
if [ -f "$UNIT" ] && has_systemd; then
    systemctl --user stop ring.service >/dev/null 2>&1 || true
fi
mkdir -p "$DATA/ring"
if [ -n "${RING_APPIMAGE:-}" ]; then
    program="$PACKED"
    rm -rf "$VENV"
    # Not when it is the installed file itself that was asked to install.
    if ! [ "$RING_APPIMAGE" -ef "$PACKED" ]; then
        cp "$RING_APPIMAGE" "$PACKED.new"
        chmod 755 "$PACKED.new"
        mv -f "$PACKED.new" "$PACKED"
    fi
else
    program="$VENV/bin/ring"
    rm -rf "$VENV" "$PACKED"
    "$PYTHON" -m venv --system-site-packages "$VENV"
    # Built from a copy, so nothing is written into the source folder.
    cp -R "$source/Linux" "$work/build"
    cp "$source/LICENSE" "$work/build/"
    "$VENV/bin/pip" install --quiet --disable-pip-version-check "$work/build" \
        || fail "pip could not install Ring."
    if ! has_pyside; then
        say "This distribution has no PySide6 package; fetching it from PyPI (about 100 MB) ..."
        "$VENV/bin/pip" install --quiet --disable-pip-version-check PySide6-Essentials \
            || fail "pip could not install PySide6."
    fi
fi

mkdir -p "$BIN" "$(dirname "$LAUNCHER")" "$(dirname "$ICON")"
ln -sf "$program" "$BIN/ring"
cp "$source/Linux/assets/ring-icon.svg" "$ICON"
sed "s|^Exec=.*|Exec=$program settings|" "$source/packaging/ring-settings.desktop" >"$LAUNCHER"

if [ "$service" = yes ] && has_systemd; then
    mkdir -p "$(dirname "$UNIT")"
    sed "s|^ExecStart=.*|ExecStart=$program run|" "$source/packaging/ring.service" >"$UNIT"
    systemctl --user daemon-reload
    systemctl --user enable --now ring.service >/dev/null 2>&1 \
        || say "Note: Ring could not be started. See: journalctl --user -u ring"
    started="Ring is running and starts at login."
elif [ "$service" = yes ]; then
    started="No systemd user session was found: start Ring yourself with '$BIN/ring run'."
else
    started="Start Ring with '$BIN/ring run'."
fi

say ""
say "$("$program" --version) is installed. $started"
say "Hold Right Ctrl to open the ring. 'Ring Settings' is in your application launcher."
case ":$PATH:" in
    *":$BIN:"*) say "'ring doctor' checks your session if something does not work." ;;
    *) say "'$BIN/ring doctor' checks your session if something does not work."
       say "($BIN is not on your PATH, so type the full path or add it.)" ;;
esac
