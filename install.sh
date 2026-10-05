#!/bin/sh
# Install Ring for the current user. No root needed.
#
#   curl -fsSL https://raw.githubusercontent.com/RingHQ/Ring/main/install.sh | sh
#
# To pass options through the pipe:  ... | sh -s -- --no-service
#
#   --no-service   do not set Ring up to start at login
#   --uninstall    remove what this script installed (your settings stay)
#
# RING_REF picks what to install: a branch or a tag such as v0.1.0 (default
# main). RING_SOURCE installs from a local checkout instead of downloading.
set -eu

REPOSITORY="https://github.com/RingHQ/Ring"
REF="${RING_REF:-main}"

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}"
BIN="$HOME/.local/bin"
VENV="$DATA/ring/venv"
UNIT="$CONFIG/systemd/user/ring.service"
LAUNCHER="$DATA/applications/ring-settings.desktop"
ICON="$DATA/icons/hicolor/scalable/apps/ring.svg"

service=yes
uninstall=no
for option in "$@"; do
    case "$option" in
        --no-service) service=no ;;
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
    if [ -L "$BIN/ring" ] && [ "$(readlink "$BIN/ring")" = "$VENV/bin/ring" ]; then
        rm -f "$BIN/ring"
    fi
    rm -rf "$DATA/ring"
    if has_systemd; then
        systemctl --user daemon-reload || true
    fi
    say "Ring is removed. Your settings are still in $CONFIG/ring."
    exit 0
fi

# What Ring needs from the system. The ring is drawn with the distribution's
# Qt, so PySide6 has to be the distribution's package, not the one from PyPI.
PYTHON=/usr/bin/python3
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || fail "python3 was not found."

packages="PySide6 and LayerShellQt from your distribution's packages"
if command -v pacman >/dev/null 2>&1; then
    packages="sudo pacman -S --needed pyside6 layer-shell-qt"
elif command -v dnf >/dev/null 2>&1; then
    packages="sudo dnf install python3-pyside6 layer-shell-qt"
fi

"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))' \
    || fail "Python 3.12 or newer is needed; $PYTHON is older."
"$PYTHON" -c 'import venv, ensurepip' 2>/dev/null \
    || fail "Python's venv module is missing. Install it (Debian and Ubuntu: python3-venv)."
"$PYTHON" -c 'import PySide6.QtQml, PySide6.QtQuick, PySide6.QtWidgets' 2>/dev/null \
    || fail "PySide6 is missing. Install it first:
    $packages"
"$PYTHON" -c '
import pathlib, sys
from PySide6.QtCore import QLibraryInfo
imports = pathlib.Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.QmlImportsPath))
sys.exit(not (imports / "org" / "kde" / "layershell").is_dir())
' || fail "LayerShellQt is missing. Install it first:
    $packages"

case "${XDG_CURRENT_DESKTOP:-}" in
    *KDE*) ;;
    *) say "Note: Ring only works on KDE Plasma 6 (Wayland). This does not look like it." ;;
esac

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM

if [ -n "${RING_SOURCE:-}" ]; then
    source="$RING_SOURCE"
    [ -f "$source/pyproject.toml" ] || fail "$source is not a checkout of Ring."
else
    say "Downloading Ring ($REF) ..."
    command -v curl >/dev/null 2>&1 || fail "curl was not found."
    curl -fsSL "$REPOSITORY/archive/$REF.tar.gz" -o "$work/ring.tar.gz" \
        || fail "could not download $REPOSITORY/archive/$REF.tar.gz"
    mkdir "$work/source"
    tar -xzf "$work/ring.tar.gz" -C "$work/source" --strip-components=1
    source="$work/source"
fi

say "Installing into $VENV ..."
# The service must not run while its files are replaced.
if [ -f "$UNIT" ] && has_systemd; then
    systemctl --user stop ring.service >/dev/null 2>&1 || true
fi
rm -rf "$VENV"
mkdir -p "$DATA/ring"
"$PYTHON" -m venv --system-site-packages "$VENV"
# Built from a copy, so nothing is written into the source folder.
cp -R "$source" "$work/build"
"$VENV/bin/pip" install --quiet --disable-pip-version-check "$work/build" \
    || fail "pip could not install Ring."

mkdir -p "$BIN" "$(dirname "$LAUNCHER")" "$(dirname "$ICON")"
ln -sf "$VENV/bin/ring" "$BIN/ring"
cp "$source/assets/ring-icon.svg" "$ICON"
sed "s|^Exec=.*|Exec=$VENV/bin/ring settings|" "$source/packaging/ring-settings.desktop" >"$LAUNCHER"

if [ "$service" = yes ] && has_systemd; then
    mkdir -p "$(dirname "$UNIT")"
    sed "s|^ExecStart=.*|ExecStart=$VENV/bin/ring run|" "$source/packaging/ring.service" >"$UNIT"
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
say "$("$VENV/bin/ring" --version) is installed. $started"
say "Hold Right Ctrl to open the ring. 'Ring Settings' is in your application launcher."
case ":$PATH:" in
    *":$BIN:"*) say "'ring doctor' checks your session if something does not work." ;;
    *) say "'$BIN/ring doctor' checks your session if something does not work."
       say "($BIN is not on your PATH, so type the full path or add it.)" ;;
esac
