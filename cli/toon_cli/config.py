"""hosts.json in the config directory: the default host and the stored
sessions, read only when nobody else could have written it."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

HOSTS_FILENAME = "hosts.json"
# Its own name, so a test can run the Windows path on any platform.
WINDOWS = sys.platform == "win32"


def _config_dir() -> Path:
    explicit = os.environ.get("TOON_CONFIG_DIR")
    if explicit:
        return Path(explicit)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "toon"
    if WINDOWS:
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "toon"
    return Path.home() / ".config" / "toon"


def _hosts_path() -> Path:
    return _config_dir() / HOSTS_FILENAME


def _read_hosts() -> dict:
    """hosts.json, or an empty config if there is none. A file that anyone
    other than this user could have written is ignored as a whole: its
    default host decides where a token goes, and its plain-text sessions
    could be someone else's.

    On Windows there are no mode bits or uid to check: the file is trusted on
    its location. %APPDATA% sits in the user profile, whose default ACL lets
    in only the user, SYSTEM and the Administrators; a directory named in
    TOON_CONFIG_DIR or XDG_CONFIG_HOME is protected as well as its own ACL
    protects it."""
    path = _hosts_path()
    if WINDOWS:
        refusal = (
            f"Warning: ignoring {path}: it must be a regular file you can read. "
            "Log in again to rewrite it."
        )
        # No O_NOFOLLOW on Windows. Planting a link here takes write access
        # to the directory, which the profile ACL keeps to this user.
        flags = os.O_RDONLY
    else:
        refusal = (
            f"Warning: ignoring {path}: it must be a file of yours with mode 0600, in a "
            "directory only you can write to. Log in again to rewrite it."
        )
        # No symlink, and the checks below run on the very file that is read:
        # a path checked first and opened later can be swapped in between.
        flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return {}
    except OSError:
        print(refusal, file=sys.stderr)
        return {}
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or (
            not WINDOWS and not _private_to_this_user(info, path.parent)
        ):
            print(refusal, file=sys.stderr)
            return {}
        try:
            data = json.loads(b"".join(iter(lambda: os.read(fd, 65536), b"")))
        except (OSError, ValueError):
            print(f"Warning: ignoring {path}: it is not readable JSON.", file=sys.stderr)
            return {}
    finally:
        os.close(fd)
    return data if isinstance(data, dict) else {}


def _private_to_this_user(info: os.stat_result, directory: Path) -> bool:
    """POSIX only: the file is this user's with mode 0600, in a directory of
    this user's that nobody else can write to."""
    uid = os.getuid()
    directory_info = directory.stat()
    return (
        info.st_uid == uid
        and stat.S_IMODE(info.st_mode) == 0o600
        and directory_info.st_uid == uid
        and not directory_info.st_mode & 0o022
    )


def _write_hosts(config: dict) -> None:
    path = _hosts_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Can hold tokens: first a 0600 file next to it (mkstemp), then an atomic
    # replace. Writing and chmodding afterwards leaves the file readable to
    # others for a moment, and an existing 0644 file even with fresh tokens.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{HOSTS_FILENAME}.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(config, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _host_entries(config: dict) -> dict:
    hosts = config.get("hosts")
    return hosts if isinstance(hosts, dict) else {}


def _host_entry(config: dict, host: str) -> dict:
    entry = _host_entries(config).get(host)
    return entry if isinstance(entry, dict) else {}


def _stored_default_host() -> str | None:
    host = _read_hosts().get("default_host")
    return host if isinstance(host, str) and host else None
