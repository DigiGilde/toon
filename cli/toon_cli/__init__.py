"""Toon CLI: publish static sites and clean up previews.

Install with `uv tool install`, or run it from a checkout without
installing with `uv run --project cli toon ...`; see README.md.

Usage:
    toon login [--host <host>] [--no-open] [--insecure-storage]
    toon logout [--host <host>]
    toon whoami [--host <host>]
    toon publish <dist-dir-or-file> --site <group/site> [--site-id <id>] \
        [--host <host>] [--preview <ref>] [--base-path <dir>] \
        [--output-file <path>]
    toon preview-remove <ref> --site <group/site> [--site-id <id>] \
        [--host <host>]
    toon group create <group> --name <name> [--access <base>] \
        [--secret-links | --no-secret-links] [--invitees | --no-invitees] \
        [--host <host>]
    toon site create <group/site> --title <title> [--access <base>] \
        [--secret-links | --no-secret-links] [--invitees | --no-invitees] \
        [--host <host>]
    toon site link <group/site> [<repository>] [--live-branch <branch>] \
        [--repository-id <id> --owner-id <id>] [--no-gh] [--host <host>]
    toon site shielding <group/site> (--on | --off) [--host <host>]
    toon feedback list --site <group/site> \
        [--status open|new|seen|resolved|all] [--preview <ref> | --live] \
        [--json] [--with-contact] [--host <host>]
    toon feedback show <id> --site <group/site> [--json] [--with-contact] \
        [--screenshot <path>] [--host <host>]
    toon feedback seen <id> [<id> ...] --site <group/site> [--host <host>]
    toon feedback resolve <id> [<id> ...] --site <group/site> \
        [--message <text> | --message-file <path>] [--host <host>]
    toon feedback reopen <id> [<id> ...] --site <group/site> [--host <host>]
    toon feedback reply <id> --site <group/site> \
        (--message <text> | --message-file <path>) [--host <host>]

Signing in happens with 'toon login': the session belongs to your user
account, for every directory. The tokens go into the system keyring (macOS
Keychain, Secret Service on Linux, Windows Credential Manager); without a
usable keyring, or with --insecure-storage, they go into hosts.json in the
config directory ($TOON_CONFIG_DIR, else $XDG_CONFIG_HOME/toon, else
%APPDATA%\\toon on Windows and ~/.config/toon elsewhere), with mode 0600
outside Windows. That file also remembers the host you last logged in
to. In CI an OIDC token is used automatically (GitHub Actions with
'id-token: write', Forgejo Actions with 'enable-openid-connect: true'), or
supply a token yourself through the environment variable
TOON_ACCESS_TOKEN. With --site-id (or TOON_SITE_ID) the OIDC token is
valid for that one site only.

Every command finds its host in this order: --host, TOON_HOST in the
environment, the host you last logged in to, then DEFAULT_HOST, the
DigiGilde instance. An old host of that instance is translated to its new
one, with a warning.

Exit codes:
    0 - success
    1 - error from the server (a network or API error, say); detail on
        stderr
    2 - wrong usage (a missing or invalid argument, an unknown path, no
        session)
"""

from __future__ import annotations

import sys

from toon_cli.access import ACCESS_BASE_WHO
from toon_cli.common import VERSION, IncompatibleServer, UsageError
from toon_cli.config import _host_entry, _hosts_path, _read_hosts, _write_hosts
from toon_cli.feedback import (
    MESSAGE_FILE_MAX_BYTES,
    SCREENSHOT_MAX_BYTES,
    SERVER_SHAPES,
    UNTRUSTED_BEGIN,
    UNTRUSTED_END,
    UNTRUSTED_NOTE,
    _url_line,
)
from toon_cli.hosts import HOST_HELP, _require_https, _require_valid_url, _resolve_host
from toon_cli.output import MAX_URL_LENGTH, _clean, _clean_block
from toon_cli.parser import _build_parser
from toon_cli.session import _mask_in_ci_log, _save_session, _session_tokens
from toon_cli.sites import REPOSITORY_ARGUMENT, _parse_repository
from toon_cli.transport import RETRY_DELAY_S, SUPPORTED_API_MAJOR

# `main` is the console script, github.py imports ACCESS_BASE_WHO and VERSION,
# and the tests read the rest. A name a test patches is not re-exported: the
# patch has to land in the module that looks it up.
__all__ = [
    "ACCESS_BASE_WHO",
    "HOST_HELP",
    "MAX_URL_LENGTH",
    "MESSAGE_FILE_MAX_BYTES",
    "REPOSITORY_ARGUMENT",
    "RETRY_DELAY_S",
    "SCREENSHOT_MAX_BYTES",
    "SERVER_SHAPES",
    "SUPPORTED_API_MAJOR",
    "UNTRUSTED_BEGIN",
    "UNTRUSTED_END",
    "UNTRUSTED_NOTE",
    "VERSION",
    "IncompatibleServer",
    "UsageError",
    "_clean",
    "_clean_block",
    "_host_entry",
    "_hosts_path",
    "_mask_in_ci_log",
    "_parse_repository",
    "_read_hosts",
    "_require_https",
    "_require_valid_url",
    "_resolve_host",
    "_save_session",
    "_session_tokens",
    "_url_line",
    "_write_hosts",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    # Visitor text goes to stdout, which on Windows before Python 3.15 encodes
    # a pipe or file in the ANSI code page: one emoji would end the output
    # halfway. An escape holds no newline, so it cannot forge a marker line.
    # pythonw has no stdout at all.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(errors="backslashreplace")
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 2
        return code
    try:
        return args.func(args)
    except UsageError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except IncompatibleServer as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
