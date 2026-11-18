"""toon login, toon logout and toon whoami."""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
import webbrowser

import httpx

from toon_cli.common import VERSION, IncompatibleServer, UsageError
from toon_cli.config import _host_entry, _hosts_path, _read_hosts
from toon_cli.hosts import _same_origin, _setup
from toon_cli.output import _clean, _print_problem_detail
from toon_cli.session import (
    _forget_session,
    _get_bearer_token,
    _note_legacy_env_file,
    _save_session,
    _session_tokens,
    _warn_plain_text,
)
from toon_cli.transport import _http, _problem_data, _request

AUTH_ERROR_MESSAGES = {
    "EXPIRED_TOKEN": "The login attempt expired. Try again with 'toon login'.",
    "ACCESS_DENIED": "Login refused in the browser.",
    "INVALID_GRANT": "Invalid or revoked login attempt. Try again with 'toon login'.",
}


def cmd_login(args: argparse.Namespace) -> int:
    host = _setup(args)

    print(f"Logging in at {host}", file=sys.stderr)
    client_name = f"toon-cli {VERSION} on {platform.system()}"
    response = _request(
        host,
        "POST",
        f"{host}/-/api/v1/cli/device-authorizations",
        json={"clientName": client_name},
        timeout=30.0,
    )
    if response is None:
        return 1
    if response.status_code not in (200, 201):
        _print_problem_detail(response)
        return 1

    start = _problem_data(response)
    device_code = start.get("deviceCode")
    user_code = start.get("userCode")
    raw_complete = start.get("verificationUriComplete")
    raw_uri = start.get("verificationUri")
    if not device_code or not user_code or not (raw_complete or raw_uri):
        print("Error: unexpected answer while starting the login", file=sys.stderr)
        return 1

    # Only a sign-in URL pointing at the same host is shown or opened: a
    # server (or someone imitating it) must not get an arbitrary URL
    # (file://, another domain) clicked through here.
    verification_uri = next(
        (
            candidate
            for candidate in (raw_complete, raw_uri)
            if isinstance(candidate, str) and candidate and _same_origin(candidate, host)
        ),
        None,
    )
    if verification_uri is None:
        print(
            f"Error: the server returned a login URL that does not belong to {host}; "
            "login aborted",
            file=sys.stderr,
        )
        return 1
    verification_uri = _clean(verification_uri)
    user_code = _clean(str(user_code))

    try:
        interval = float(start.get("interval") or 5)
        expires_in = float(start.get("expiresIn") or 900)
    except (TypeError, ValueError):
        interval, expires_in = 5.0, 900.0

    print(f"\nOpen this URL to log in:\n  {verification_uri}\n", file=sys.stderr)
    print(f"Code: {user_code}\n", file=sys.stderr)
    if not args.no_open and sys.stderr.isatty():
        if webbrowser.open(verification_uri):
            print("Browser opened. Finish logging in there.\n", file=sys.stderr)
        else:
            print("Could not open a browser; use the URL above.\n", file=sys.stderr)

    deadline = time.monotonic() + expires_in
    while True:
        if time.monotonic() > deadline:
            print("Error: login attempt expired before it was approved", file=sys.stderr)
            return 1
        time.sleep(interval)
        response = _request(
            host,
            "POST",
            f"{host}/-/api/v1/cli/tokens",
            json={"grantType": "device_code", "deviceCode": device_code},
            timeout=30.0,
        )
        if response is None:
            return 1
        if response.status_code == 200:
            break
        data = _problem_data(response)
        code = data.get("code")
        if code == "AUTHORIZATION_PENDING":
            continue
        if code == "SLOW_DOWN":
            interval += 5
            continue
        message = AUTH_ERROR_MESSAGES.get(
            code, data.get("detail") or data.get("title") or f"HTTP {response.status_code}"
        )
        print(f"Error: {_clean(str(message))}", file=sys.stderr)
        return 1

    payload = _problem_data(response)
    access_token = payload.get("accessToken")
    if not isinstance(access_token, str) or not access_token:
        print("Error: unexpected answer while fetching the token", file=sys.stderr)
        return 1
    refresh_token = payload.get("refreshToken") or ""
    try:
        expires_in_seconds = float(payload.get("expiresIn") or 0)
    except (TypeError, ValueError):
        expires_in_seconds = 0.0
    member = payload.get("member") if isinstance(payload.get("member"), dict) else {}
    email = _clean(member.get("email")) if isinstance(member.get("email"), str) else ""

    fallback_reason = _save_session(
        host,
        access_token,
        refresh_token,
        time.time() + expires_in_seconds,
        insecure=args.insecure_storage,
        make_default=True,
    )
    print(f"Logged in as {email}." if email else "Logged in.")
    if fallback_reason is None:
        print("Session stored in the system keyring.", file=sys.stderr)
    elif args.insecure_storage:
        print(f"Session stored in plain text in {_hosts_path()}.", file=sys.stderr)
    else:
        _warn_plain_text(fallback_reason)
    _note_legacy_env_file()
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    host = _setup(args)

    entry = _host_entry(_read_hosts(), host)
    try:
        stored_access, refresh_token = _session_tokens(host, entry)
    except UsageError as error:
        # Logging out still has to drop what is stored locally.
        print(f"Warning: {error}", file=sys.stderr)
        stored_access, refresh_token = None, None
    access_token = os.environ.get("TOON_ACCESS_TOKEN") or stored_access
    revoked = True
    incompatible = None
    if access_token or refresh_token:
        headers = {"Authorization": f"Bearer {access_token}"} if access_token else {}
        json_body = {"refreshToken": refresh_token} if refresh_token else None
        try:
            response = _http(
                "DELETE",
                f"{host}/-/api/v1/cli/session",
                headers=headers,
                json=json_body,
                timeout=30.0,
            )
            revoked = response.status_code in (200, 204)
        except httpx.HTTPError:
            revoked = False
        except IncompatibleServer as error:
            incompatible = error

    _forget_session(host)
    if incompatible:
        print("Logged out locally.")
        raise incompatible
    if (access_token or refresh_token) and not revoked:
        print(
            "Warning: could not revoke the session at the server; "
            "logged out locally.",
            file=sys.stderr,
        )
    print("Logged out.")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    host = _setup(args)
    token = _get_bearer_token(host)

    response = _request(
        host,
        "GET",
        f"{host}/-/api/v1/cli/whoami",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    if response is None:
        return 1

    if response.status_code == 200:
        data = _problem_data(response)
        member = data.get("member") if isinstance(data.get("member"), dict) else {}
        email = _clean(member["email"]) if isinstance(member.get("email"), str) else "unknown"
        expires_at = _clean(data["expiresAt"]) if isinstance(data.get("expiresAt"), str) else ""
        suffix = f" (expires {expires_at})" if expires_at else ""
        print(f"Logged in as {email} on {host}{suffix}.")
        return 0

    _print_problem_detail(response)
    return 1
