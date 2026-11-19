"""The token a command sends: TOON_ACCESS_TOKEN, a CI OIDC token, or the
session stored in the system keyring or hosts.json, refreshed when it
expires."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
import urllib.parse
from pathlib import Path

import httpx
import keyring
import keyring.core
import keyring.errors

from toon_cli.common import UsageError
from toon_cli.config import (
    _host_entries,
    _host_entry,
    _hosts_path,
    _read_hosts,
    _write_hosts,
)
from toon_cli.output import _clean
from toon_cli.transport import RETRY_ANY_TRANSPORT, RETRY_NOT_SENT, _http, _problem_data

KEYRING_SERVICE_PREFIX = "toon:"
KEYRING_USERNAME = "session"
LEGACY_ENV_FILENAME = ".env.plak"


def _keyring_service(host: str) -> str:
    return KEYRING_SERVICE_PREFIX + host


def _save_session(
    host: str,
    access_token: str,
    refresh_token: str,
    expires_at: float,
    *,
    insecure: bool,
    make_default: bool,
) -> str | None:
    """Stores the session for host: the tokens in the system keyring, or in
    hosts.json when insecure is set or the keyring fails. Returns None when
    the keyring took them, else why they went into the file."""
    config = _read_hosts()
    previous = _host_entry(config, host)
    entry: dict[str, object] = {"access_expires_at": expires_at}
    fallback_reason: str | None = "--insecure-storage"
    if not insecure and not keyring.core.recommended(keyring.get_keyring()):
        # PYTHON_KEYRING_BACKEND or keyringrc.cfg can select the null backend,
        # which drops the secret without an error, or a plain-text one such as
        # keyrings.alt: neither is a system keyring.
        fallback_reason = "no system keyring found"
    elif not insecure:
        secret = json.dumps({"access_token": access_token, "refresh_token": refresh_token})
        try:
            keyring.set_password(_keyring_service(host), KEYRING_USERNAME, secret)
            fallback_reason = None
        except keyring.errors.NoKeyringError:
            # Its message recommends installing keyrings.alt, which only
            # stores the same plain text somewhere else.
            fallback_reason = "no system keyring found"
        except keyring.errors.KeyringError as error:
            fallback_reason = f"the system keyring refused: {error}"
    if fallback_reason is None:
        entry["storage"] = "keyring"
    else:
        entry |= {"storage": "file", "access_token": access_token, "refresh_token": refresh_token}
        if insecure:
            entry["insecure_storage"] = True
        # Otherwise the old keyring entry outlives the next logout, which
        # only looks where the entry says the tokens are.
        if previous.get("storage") == "keyring":
            _delete_keyring_entry(host)
    hosts = _host_entries(config)
    hosts[host] = entry
    config["hosts"] = hosts
    if make_default:
        config["default_host"] = host
    _write_hosts(config)
    return fallback_reason


def _session_tokens(host: str, entry: dict) -> tuple[str | None, str | None]:
    """The access and refresh token of a stored session, from wherever the
    entry says they live."""
    if entry.get("storage") == "keyring":
        try:
            secret = keyring.get_password(_keyring_service(host), KEYRING_USERNAME)
        except keyring.errors.KeyringError as error:
            raise UsageError(
                f"Could not read the session from the system keyring: {error}"
            ) from error
        try:
            data = json.loads(secret) if secret else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
    else:
        data = entry
    access = data.get("access_token")
    refresh = data.get("refresh_token")
    return (
        access if isinstance(access, str) and access else None,
        refresh if isinstance(refresh, str) and refresh else None,
    )


def _delete_keyring_entry(host: str) -> keyring.errors.KeyringError | None:
    """Removes the keyring entry for host. Returns the error if that failed;
    an entry that is already gone is no failure."""
    try:
        keyring.delete_password(_keyring_service(host), KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError as error:
        # The macOS backend raises this for a refused delete too, not only
        # for a missing entry: only a read that finds nothing tells them apart.
        try:
            gone = keyring.get_password(_keyring_service(host), KEYRING_USERNAME) is None
        except keyring.errors.KeyringError:
            gone = False
        return None if gone else error
    except keyring.errors.KeyringError as error:
        return error
    return None


def _warn_plain_text(reason: str) -> None:
    print(
        f"Warning: {_clean(reason)}; the session is stored in plain text in {_hosts_path()}.",
        file=sys.stderr,
    )


def _forget_session(host: str) -> None:
    config = _read_hosts()
    entry = _host_entry(config, host)
    if entry.get("storage") == "keyring":
        error = _delete_keyring_entry(host)
        if error is not None:
            print(
                f"Warning: could not remove the session from the system keyring: {error}",
                file=sys.stderr,
            )
    hosts = _host_entries(config)
    if host not in hosts:
        return
    # The default host stays, so a later 'toon login' needs no --host.
    del hosts[host]
    config["hosts"] = hosts
    _write_hosts(config)


def _note_legacy_env_file() -> None:
    if (Path.cwd() / LEGACY_ENV_FILENAME).exists():
        print(
            f"Note: toon no longer reads {LEGACY_ENV_FILENAME} in this directory; "
            "the session now belongs to your user account. Log in again with "
            f"'toon login' and delete {LEGACY_ENV_FILENAME}.",
            file=sys.stderr,
        )


def _refresh_token(host: str, refresh_token: str, entry: dict) -> str:
    try:
        response = _http(
            "POST",
            f"{host}/-/api/v1/cli/tokens",
            json={"grantType": "refresh_token", "refreshToken": refresh_token},
            timeout=30.0,
            retry_on=RETRY_NOT_SENT,
        )
    except httpx.HTTPError as error:
        raise UsageError(f"Could not refresh the session: {error}") from error
    if response.status_code != 200:
        raise UsageError("Session expired or revoked: log in again with 'toon login'")
    payload = _problem_data(response)
    access_token = payload.get("accessToken")
    if not isinstance(access_token, str) or not access_token:
        raise UsageError("Unexpected answer while refreshing the session")
    refresh_out = payload.get("refreshToken") or refresh_token
    expires_at = time.time() + float(payload.get("expiresIn") or 0)
    # Only a choice for --insecure-storage keeps the file; a session that fell
    # back to it tries the keyring again, so a locked keychain once does not
    # leave it in plain text for good.
    fallback_reason = _save_session(
        host,
        access_token,
        refresh_out,
        expires_at,
        insecure=entry.get("insecure_storage") is True,
        make_default=False,
    )
    # The server has rotated the refresh token, so the new pair has to be kept
    # somewhere; a session that just left the keyring must not do so silently.
    if fallback_reason is not None and entry.get("storage") == "keyring":
        _warn_plain_text(fallback_reason)
    return access_token


def _stored_token(host: str) -> str | None:
    """Stored session for this host, refreshed if need be. None if there is none."""
    entry = _host_entry(_read_hosts(), host)
    if not entry:
        return None
    token, refresh = _session_tokens(host, entry)
    if not token:
        return None
    expires_at = entry.get("access_expires_at")
    # 60 seconds of slack: a token that expires while the request is on its
    # way is as useless as one that has already expired. An expiry that is
    # not a number (bool is an int in Python) counts as none.
    if (
        isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
        and expires_at - 60 <= time.time()
    ):
        if not refresh:
            raise UsageError("Session expired: log in again with 'toon login'")
        token = _refresh_token(host, refresh, entry)
    return token


def _fetch_oidc_token(host: str, site_id: str | None) -> str | None:
    """CI mode: exchange the runner OIDC token for an ID token with this host
    as audience, or with this host and the site id, which makes the token
    valid for that site only. None if the environment offers no OIDC request
    (so not CI, or 'id-token: write'/'enable-openid-connect' is missing)."""
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        return None
    audience = host if site_id is None else f"{host}/-/sites/{site_id}"
    separator = "&" if "?" in request_url else "?"
    url = f"{request_url}{separator}audience={urllib.parse.quote(audience, safe='')}"
    try:
        response = _http(
            "GET",
            url,
            headers={"Authorization": f"bearer {request_token}"},
            timeout=30.0,
            retry_on=RETRY_ANY_TRANSPORT,
        )
    except httpx.HTTPError as error:
        raise UsageError(f"Could not fetch an OIDC token: {error}") from error
    if response.status_code != 200:
        raise UsageError(
            f"Could not fetch an OIDC token (HTTP {response.status_code}). Set "
            "'permissions: id-token: write' in the workflow (GitHub Actions) or "
            "'enable-openid-connect: true' on the runner (Forgejo Actions)."
        )
    data = _problem_data(response)
    token = data.get("value")
    if not isinstance(token, str) or not token:
        raise UsageError("Unexpected answer while fetching the OIDC token")
    _mask_in_ci_log(token)
    return token


def _mask_in_ci_log(secret: str) -> None:
    """Asks the runner to mask `secret` in the rest of its log.

    GitHub Actions and Forgejo Actions read these workflow commands from
    stdout, so there is no way to send this anywhere else. That makes the
    command only worth writing when stdout really is the runner's log: a
    regular file means someone redirected it (`> log.txt`), the runner never
    sees the command, and printing it would write the token into that file for
    nothing. A pipe is indistinguishable from the runner's own pipe, so
    `$(...)` and `| tee` still capture it; see docs/publishing.md.
    """
    try:
        redirected = stat.S_ISREG(os.fstat(sys.stdout.fileno()).st_mode)
    except (OSError, ValueError, AttributeError):
        # No real file descriptor to judge by. Keep the mask: a runner that
        # does read this is the case worth protecting, and nothing else in
        # this CLI puts the token on stdout.
        redirected = False
    if redirected:
        return
    # Escaped as @actions/core escapes command data: a line break would end
    # the command early and leave the rest of the secret readable in the log.
    # Flushed at once, so the runner masks it before anything else shows it.
    escaped = secret.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    sys.stdout.write(f"::add-mask::{escaped}\n")
    sys.stdout.flush()


def _warn_site_id_unused(site_id: str | None) -> None:
    if site_id is not None:
        print(
            "Warning: --site-id (or TOON_SITE_ID) only binds the CI ID token the CLI asks for; "
            "ignored with a session or TOON_ACCESS_TOKEN.",
            file=sys.stderr,
        )


def _get_bearer_token(host: str, site_id: str | None = None) -> str:
    """The token for the deploy endpoints and /cli/whoami, in order: an
    explicit TOON_ACCESS_TOKEN, a CI OIDC token, then the stored session.
    Only the OIDC token carries the site id."""
    env_token = os.environ.get("TOON_ACCESS_TOKEN")
    if env_token:
        _warn_site_id_unused(site_id)
        return env_token
    oidc_token = _fetch_oidc_token(host, site_id)
    if oidc_token:
        return oidc_token
    stored = _stored_token(host)
    if stored:
        _warn_site_id_unused(site_id)
        return stored
    _note_legacy_env_file()
    raise UsageError(
        "No token: log in with 'toon login --host <host>', or set "
        "TOON_ACCESS_TOKEN (for instance an OIDC token in CI, see README)"
    )


def _get_member_token(host: str) -> str:
    """The token for the commands that act as a member (creating a group or a
    site, linking a repository and the feedback commands): an explicit
    TOON_ACCESS_TOKEN, then the stored session. No CI OIDC token: the server
    takes only a member's CLI token there."""
    token = os.environ.get("TOON_ACCESS_TOKEN") or _stored_token(host)
    if token:
        return token
    _note_legacy_env_file()
    raise UsageError(
        "No session: log in with 'toon login --host <host>', or set TOON_ACCESS_TOKEN"
    )
