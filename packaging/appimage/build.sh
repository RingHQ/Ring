#!/bin/sh
# Build the AppImage: Ring-<architecture>.AppImage in dist/, or in the folder
# given as the first argument.
#
#   sh packaging/appimage/build.sh
#
# Needs python3 with pip, and curl to fetch appimagetool unless APPIMAGETOOL
# names one. ARCH picks the architecture (default: this machine's), PYTHON
# the interpreter that runs pip.
#
# Ring and its dependencies are pure Python, so what is packed is the same
# on every architecture; only the AppImage runtime in front of it differs.
set -eu

TOOL_VERSION=1.9.1
TOOL_SHA256_x86_64=ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0
TOOL_SHA256_aarch64=f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LINUX="$ROOT/Linux"
ARCH="${ARCH:-$(uname -m)}"
PYTHON="${PYTHON:-python3}"
OUT="${1:-$ROOT/dist}"

fail() { printf 'build.sh: %s\n' "$*" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT INT TERM
appdir="$work/Ring.AppDir"

# Built from a copy, so nothing is written into the source folder.
mkdir "$work/source"
cp -R "$LINUX/pyproject.toml" "$ROOT/LICENSE" "$LINUX/src" "$work/source/"
find "$work/source" -name __pycache__ -prune -exec rm -rf {} +

mkdir -p "$appdir/usr/lib/ring" "$appdir/usr/share/ring/Linux/assets" \
    "$appdir/usr/share/ring/packaging"
"$PYTHON" -m pip install --quiet --disable-pip-version-check --no-compile \
    --target "$appdir/usr/lib/ring" "$work/source" \
    || fail "pip could not install Ring."
# The `ring` script pip writes points at the interpreter that built it.
rm -rf "$appdir/usr/lib/ring/bin"
if find "$appdir/usr/lib/ring" -name '*.so' | grep -q .; then
    fail "a dependency is no longer pure Python; the AppImage would only fit one Python version."
fi

# What `Ring.AppImage install` needs: the installer and the files it copies,
# laid out as in the repository.
cp "$ROOT/install.sh" "$appdir/usr/share/ring/"
cp "$LINUX/assets/ring-icon.svg" "$appdir/usr/share/ring/Linux/assets/"
cp "$ROOT/packaging/ring.service" "$ROOT/packaging/ring-settings.desktop" \
    "$appdir/usr/share/ring/packaging/"

cp "$ROOT/packaging/appimage/AppRun" "$ROOT/packaging/appimage/ring.desktop" "$appdir/"
cp "$LINUX/assets/ring-icon.svg" "$appdir/ring.svg"
ln -s ring.svg "$appdir/.DirIcon"
chmod 755 "$appdir/AppRun"
find "$appdir" -type d -exec chmod 755 {} +
find "$appdir" -type f ! -name AppRun -exec chmod 644 {} +

tool="${APPIMAGETOOL:-}"
if [ -z "$tool" ]; then
    host="$(uname -m)"
    case "$host" in
        x86_64) sum="$TOOL_SHA256_x86_64" ;;
        aarch64) sum="$TOOL_SHA256_aarch64" ;;
        *) fail "no appimagetool is known for $host; set APPIMAGETOOL." ;;
    esac
    tool="$work/appimagetool"
    curl -fsSL -o "$tool" \
        "https://github.com/AppImage/appimagetool/releases/download/$TOOL_VERSION/appimagetool-$host.AppImage" \
        || fail "could not download appimagetool."
    echo "$sum  $tool" | sha256sum -c --quiet - || fail "appimagetool has an unexpected checksum."
    chmod +x "$tool"
fi

mkdir -p "$OUT"
# Unpacked instead of mounted: build machines often have no FUSE.
ARCH="$ARCH" "$tool" --appimage-extract-and-run --no-appstream \
    "$appdir" "$OUT/Ring-$ARCH.AppImage" \
    || fail "appimagetool could not build the AppImage."
echo "Built $OUT/Ring-$ARCH.AppImage"
