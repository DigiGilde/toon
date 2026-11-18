"""The access of a group or a site: the flags sent, the answer read back
and printed."""

from __future__ import annotations

import argparse

ACCESS_BASES = ("public", "sso", "site_team", "nobody")

# Who a base lets in, for the terminal. Mirrors the server's ACCESS_BASE_HINT.
ACCESS_BASE_WHO = {
    "public": "anyone",
    "sso": "anyone who signs in with SSO Rijk",
    "site_team": "members of the site and its group",
    "nobody": "nobody by default",
}


def _access_body(args: argparse.Namespace) -> dict[str, object] | None:
    """Only the flags that were given: the server fills in the rest from the
    default (for a site: the group's default access)."""
    body: dict[str, object] = {}
    if args.access is not None:
        body["base"] = args.access
    if args.secret_links is not None:
        body["keys"] = args.secret_links
    if args.invitees is not None:
        body["invitees"] = args.invitees
    return body or None


def _parse_access(data: object) -> tuple[str, bool, bool] | None:
    if not isinstance(data, dict):
        return None
    base, keys, invitees = data.get("base"), data.get("keys"), data.get("invitees")
    if base not in ACCESS_BASE_WHO or not isinstance(keys, bool) or not isinstance(invitees, bool):
        return None
    return base, keys, invitees


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def format_access(access: tuple[str, bool, bool]) -> str:
    """The base, then the extras that are on: `site_team,invitees`."""
    base, keys, invitees = access
    return ",".join([base] + ["keys"] * keys + ["invitees"] * invitees)


def _print_access(access: tuple[str, bool, bool], label: str, change_url: str) -> None:
    base, keys, invitees = access
    print(
        f"{label}: {base} ({ACCESS_BASE_WHO[base]}), "
        f"secret links {_on_off(keys)}, invitees {_on_off(invitees)}."
    )
    if base == "public":
        return
    who = [] if base == "nobody" else [ACCESS_BASE_WHO[base]]
    if keys:
        who.append("anyone with a secret link")
    if invitees:
        who.append("invitees, once signed in")
    print(f"Who can see it: {', '.join(who) if who else 'nobody yet'}.")
    print(f"Change it at: {change_url}")
