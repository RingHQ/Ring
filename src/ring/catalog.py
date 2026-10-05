"""The plugin catalog: plugins that can be installed from the Ring organisation.

The catalog is the repository <https://github.com/RingHQ/Plugins>. Each
plugin is a folder there, named like the plugin, with a `README.md` (its name
and description) and a `plugin.py`. Installing one copies that folder into
the user's plugin directory; it still has to be enabled before it is loaded.
"""

import io
import shutil
import tarfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ring.plugins import ABOUT_FILE, FOLDER_MODULES, PLUGIN_NAME, read_about

REPOSITORY = "https://github.com/RingHQ/Plugins"
ARCHIVE_URL = "https://codeload.github.com/RingHQ/Plugins/tar.gz/refs/heads/main"
_TIMEOUT = 20
# Nothing in the catalog comes close; anything larger is not the catalog.
_MAX_ARCHIVE = 20 * 1024 * 1024
_MAX_FILE = 2 * 1024 * 1024


class CatalogError(RuntimeError):
    """The catalog or one of its plugins is not usable; the message is for the user."""


@dataclass(frozen=True, slots=True)
class CatalogPlugin:
    """A plugin in the catalog, with the contents of its folder."""

    name: str
    title: str
    description: str
    # Path inside the plugin folder -> contents.
    files: dict[str, bytes]


def _collect(files: dict[str, dict[str, bytes]]) -> dict[str, CatalogPlugin]:
    """Turn folders of files into plugins, dropping folders that are not one."""
    plugins = {}
    for name, contents in sorted(files.items()):
        if not PLUGIN_NAME.fullmatch(name) or not any(m in contents for m in FOLDER_MODULES):
            continue
        about = contents.get(ABOUT_FILE, b"").decode("utf-8", "replace")
        title, description = read_about(about)
        plugins[name] = CatalogPlugin(name, title or name, description, contents)
    return plugins


def _from_directory(directory: Path) -> dict[str, CatalogPlugin]:
    files: dict[str, dict[str, bytes]] = {}
    try:
        for folder in directory.iterdir():
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            for path in folder.rglob("*"):
                relative = path.relative_to(folder)
                hidden = any(
                    part.startswith(".") or part == "__pycache__" for part in relative.parts
                )
                if path.is_file() and not path.is_symlink() and not hidden:
                    files.setdefault(folder.name, {})[relative.as_posix()] = path.read_bytes()
    except OSError as error:
        raise CatalogError(f"Cannot read the plugins in {directory}: {error}") from error
    return _collect(files)


def from_archive(data: bytes) -> dict[str, CatalogPlugin]:
    """Read the plugins out of a .tar.gz of the repository."""
    files: dict[str, dict[str, bytes]] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive:
                # <repository>-<branch>/<plugin>/<file...>; regular files only.
                parts = PurePosixPath(member.name).parts
                if not member.isfile() or len(parts) < 3 or member.size > _MAX_FILE:
                    continue
                if any(part in ("..", "__pycache__") or part.startswith(".") for part in parts):
                    continue
                handle = archive.extractfile(member)
                if handle is not None:
                    files.setdefault(parts[1], {})["/".join(parts[2:])] = handle.read()
    except (tarfile.TarError, OSError, EOFError) as error:
        raise CatalogError(f"The plugin catalog could not be read: {error}") from error
    return _collect(files)


def read_catalog(source: str | None = None) -> dict[str, CatalogPlugin]:
    """Return the plugins of the catalog by name.

    `source` is a folder laid out like the repository, for plugins that are
    not published; without it the repository is downloaded.
    """
    if source is not None:
        return _from_directory(Path(source).expanduser())
    try:
        with urllib.request.urlopen(ARCHIVE_URL, timeout=_TIMEOUT) as response:
            data = response.read(_MAX_ARCHIVE + 1)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise CatalogError(
            f"Cannot download the plugin catalog from {REPOSITORY}: {error}"
        ) from error
    if len(data) > _MAX_ARCHIVE:
        raise CatalogError("The plugin catalog is larger than expected; not reading it.")
    return from_archive(data)


def install(plugin: CatalogPlugin, directory: Path, *, replace: bool = False) -> Path:
    """Copy a catalog plugin into the plugin directory and return its folder."""
    target = directory / plugin.name
    taken = [path for path in (target, directory / f"{plugin.name}.py") if path.exists()]
    if taken and not replace:
        raise CatalogError(
            f"A plugin called '{plugin.name}' is already installed ({taken[0]}). "
            f"`ring plugins update {plugin.name}` replaces it."
        )
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Written next to the target first, so a failure leaves the old one.
        staging = directory / f".{plugin.name}.new"
        shutil.rmtree(staging, ignore_errors=True)
        for relative, contents in plugin.files.items():
            path = staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        remove(plugin.name, directory)
        staging.rename(target)
    except OSError as error:
        raise CatalogError(f"Cannot install '{plugin.name}' into {directory}: {error}") from error
    return target


def remove(name: str, directory: Path) -> bool:
    """Delete an installed plugin; return whether there was one."""
    if not PLUGIN_NAME.fullmatch(name):
        return False
    folder, file = directory / name, directory / f"{name}.py"
    found = False
    if folder.is_dir() and not folder.is_symlink():
        shutil.rmtree(folder)
        found = True
    if file.is_file():
        file.unlink()
        found = True
    return found
