"""toon group create, toon site create, toon site link and toon site shielding."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse

from toon_cli.access import _access_body, _parse_access, _print_access
from toon_cli.common import SITE_ID_RE, UsageError, _split_site, _valid_slug
from toon_cli.hosts import _setup
from toon_cli.output import _clean, _print_problem_detail
from toon_cli.session import _get_member_token
from toon_cli.transport import _problem_data, _request


def _create(host: str, token: str, path: str, body: dict[str, object]) -> dict | None:
    """POSTs to a creation route; the answer on 201, None after an error has
    been printed."""
    response = _request(
        host,
        "POST",
        f"{host}{path}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=30.0,
    )
    if response is None:
        return None
    if response.status_code != 201:
        _print_problem_detail(response)
        return None
    return _problem_data(response)


def cmd_group_create(args: argparse.Namespace) -> int:
    host = _setup(args)
    token = _get_member_token(host)
    group = _valid_slug(args.group, "group")

    body: dict[str, object] = {"slug": group, "name": args.name}
    access = _access_body(args)
    if access is not None:
        body["defaultAccess"] = access
    data = _create(host, token, "/-/api/v1/groups", body)
    if data is None:
        return 1
    default_access = _parse_access(data.get("defaultAccess"))
    if default_access is None:
        print("Error: unexpected answer without the group's access", file=sys.stderr)
        return 1
    name = _clean(data["name"]) if isinstance(data.get("name"), str) else group
    print(f"Created group '{group}' ({name}). You are its admin.")
    _print_access(default_access, "Default access for new sites", f"{host}/{group}/-/settings")
    return 0


def cmd_site_create(args: argparse.Namespace) -> int:
    host = _setup(args)
    token = _get_member_token(host)
    group, site = _split_site(args.site, "The site")

    body: dict[str, object] = {"slug": site, "title": args.title}
    access = _access_body(args)
    if access is not None:
        body["access"] = access
    data = _create(host, token, f"/-/api/v1/groups/{group}/sites", body)
    if data is None:
        return 1
    site_access = _parse_access(data.get("access"))
    if site_access is None:
        print("Error: unexpected answer without the site's access", file=sys.stderr)
        return 1
    title = _clean(data["title"]) if isinstance(data.get("title"), str) else site
    print(f"Created site '{group}/{site}' ({title}). You are its admin.")
    _print_access(site_access, "Access", f"{host}/{group}/{site}/access")
    print(f"Publish to it with: toon publish <dist> --host {host} --site {group}/{site}")
    return 0


# GitHub and Forgejo owner and repository names, as the server accepts them.
REPOSITORY_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
GITHUB_HOSTNAME = "github.com"
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$")
MAX_PROVIDER_ID = 2**63 - 1
SUBPROCESS_TIMEOUT_S = 30
# A refusal of a repository says where it came from instead of quoting it:
# the URL of the git remote 'origin' can carry a token.
REPOSITORY_ARGUMENT = "The repository argument"
GIT_ORIGIN = "The git remote 'origin'"
VALID_REPOSITORY = "pass 'owner/repo' or a URL like https://github.com/owner/repo"


def _repository_name(value: str) -> str | None:
    return value if REPOSITORY_NAME_RE.match(value) and value not in (".", "..") else None


def _parse_repository(reference: str, source: str) -> tuple[str, str, str]:
    """`owner/repo`, an https or ssh URL, or a git@ address into (hostname,
    owner, repo); a bare `owner/repo` is on github.com. A refusal starts with
    source, REPOSITORY_ARGUMENT or GIT_ORIGIN."""
    value = reference.strip()
    hostname = GITHUB_HOSTNAME
    scp = re.match(r"^[^@/\s]+@([^:/\s]+):(.+)$", value)
    if scp:
        hostname, path = scp.group(1), scp.group(2)
    elif "://" in value:
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            # Not urllib's message, which can quote the URL.
            raise UsageError(f"{source} is not a valid URL: {VALID_REPOSITORY}") from None
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if parsed.scheme not in ("https", "ssh", "git") or not parsed.hostname or port == -1:
            raise UsageError(f"{source} is not a repository URL: {VALID_REPOSITORY}")
        hostname, path = parsed.hostname, parsed.path
        # An ssh port says nothing about where the web host listens.
        if parsed.scheme == "https" and port is not None:
            hostname = f"{hostname}:{port}"
    else:
        path = value
    if not HOSTNAME_RE.match(hostname.lower()):
        raise UsageError(f"{source} names no valid host: {VALID_REPOSITORY}")
    parts = [part for part in path.split("/") if part]
    if "://" not in value and not scp and len(parts) != 2:
        raise UsageError(f"{source} is neither 'owner/repo' nor a URL: {VALID_REPOSITORY}")
    if len(parts) < 2:
        raise UsageError(f"{source} names no owner and repository: {VALID_REPOSITORY}")
    owner, repo = _repository_name(parts[0]), _repository_name(parts[1].removesuffix(".git"))
    if owner is None or repo is None:
        raise UsageError(f"{source} names no valid owner or repository name: {VALID_REPOSITORY}")
    return hostname.lower(), owner, repo


def _git_origin() -> str:
    """The URL of the remote 'origin' of the git checkout we are in."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is None or result.returncode != 0 or not result.stdout.strip():
        raise UsageError(
            "No repository given and no git remote 'origin' here: pass 'owner/repo' or the repository URL"
        )
    return result.stdout.strip()


class NoIdsFromGh(Exception):
    """gh gave no IDs; the message says why and what to do about it."""


IDS_BY_HAND = "pass --repository-id and --owner-id"


def _github_repository(owner: str, repo: str) -> tuple[str, str, int, int]:
    """(owner, repo, repository id, owner id) as GitHub has them, asked with
    your own gh login, so a private repository resolves too."""
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", GITHUB_HOSTNAME, f"repos/{owner}/{repo}"],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        raise NoIdsFromGh(f"gh is not installed: install it and run 'gh auth login', or {IDS_BY_HAND}.") from None
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NoIdsFromGh(f"gh gave no IDs ({_clean(str(error))}): {IDS_BY_HAND}.") from None
    # gh exits with 4 when it has no login at all; a token it has but GitHub
    # no longer accepts comes back as a 401.
    if result.returncode == 4 or "(HTTP 401)" in result.stderr:
        raise NoIdsFromGh("gh is not logged in: run 'gh auth login' (or set GH_TOKEN) and run this again.")
    if "(HTTP 404)" in result.stderr:
        raise NoIdsFromGh(f"gh cannot see {owner}/{repo} with your login: check the name, or ask for access.")
    if result.returncode != 0:
        lines = [line for line in result.stderr.splitlines() if line.strip()]
        why = _clean(lines[-1]) if lines else f"exit code {result.returncode}"
        raise NoIdsFromGh(f"gh gave no IDs ({why}): {IDS_BY_HAND}.")
    unusable = NoIdsFromGh(f"gh gave no usable IDs for {owner}/{repo}: {IDS_BY_HAND}.")
    try:
        data = json.loads(result.stdout)
    except ValueError:
        raise unusable from None
    owner_data = data.get("owner") if isinstance(data, dict) else None
    if not isinstance(owner_data, dict):
        raise unusable
    ids = (data.get("id"), owner_data.get("id"))
    names = (owner_data.get("login"), data.get("name"))
    if not all(type(value) is int and 0 < value <= MAX_PROVIDER_ID for value in ids):
        raise unusable
    if not all(isinstance(value, str) and _repository_name(value) for value in names):
        raise unusable
    return names[0], names[1], ids[0], ids[1]


def cmd_site_link(args: argparse.Namespace) -> int:
    host = _setup(args)
    group, site = _split_site(args.site, "The site")
    if (args.repository_id is None) != (args.owner_id is None):
        raise UsageError("Pass --repository-id and --owner-id together, or neither")
    for value in (args.repository_id, args.owner_id):
        if value is not None and not 0 < value <= MAX_PROVIDER_ID:
            raise UsageError("--repository-id and --owner-id must be positive whole numbers")
    if args.repository:
        hostname, owner, repo = _parse_repository(args.repository, REPOSITORY_ARGUMENT)
    else:
        hostname, owner, repo = _parse_repository(_git_origin(), GIT_ORIGIN)
    token = _get_member_token(host)

    body: dict[str, object] = {"owner": owner, "repo": repo, "liveBranch": args.live_branch}
    if hostname == GITHUB_HOSTNAME:
        body["provider"] = "github"
    else:
        body["provider"] = "forgejo"
        body["host"] = f"https://{hostname}"
    ids_from = None
    # Why no IDs went along: printed when Toon's own anonymous lookup fails.
    no_ids = None
    if args.repository_id is not None:
        body["repositoryId"], body["ownerId"] = args.repository_id, args.owner_id
        ids_from = "as given"
    elif hostname != GITHUB_HOSTNAME:
        no_ids = f"To link it without Toon's lookup, {IDS_BY_HAND}."
    elif not args.gh:
        no_ids = f"gh was not asked (--no-gh): run this again without --no-gh, or {IDS_BY_HAND}."
    else:
        try:
            body["owner"], body["repo"], body["repositoryId"], body["ownerId"] = _github_repository(owner, repo)
            ids_from = "from gh"
        except NoIdsFromGh as error:
            no_ids = str(error)

    response = _request(
        host,
        "PUT",
        f"{host}/-/api/v1/sites/{group}/{site}/repository",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=30.0,
    )
    if response is None:
        return 1
    if response.status_code != 200:
        code = _problem_data(response).get("code")
        if code == "REPOSITORY_NOT_FOUND":
            # The server's detail also tells the admin form what to fill in;
            # here the line below says that.
            print(f"Error: Repository {owner}/{repo} not found on {hostname}, or not public.", file=sys.stderr)
        else:
            _print_problem_detail(response)
        if code in ("REPOSITORY_NOT_FOUND", "CI_PROVIDER_RATE_LIMITED") and no_ids is not None:
            print(no_ids, file=sys.stderr)
        return 1

    data = _problem_data(response)
    linked = f"{hostname}/{body['owner']}/{body['repo']}"
    if isinstance(data.get("owner"), str) and isinstance(data.get("repo"), str):
        linked = f"{hostname}/{_clean(data['owner'])}/{_clean(data['repo'])}"
    print(f"Linked {linked} to {group}/{site}.")
    if args.live_branch is None:
        print("Live: from any branch, on a push, a manual run or a schedule. Previews: from any branch.")
    else:
        print(f"Live: only from '{args.live_branch}', on a push, a manual run or a schedule. "
              "Previews: from any branch.")
    if ids_from is not None:
        print(f"IDs {ids_from}: repository {body['repositoryId']}, owner {body['ownerId']}.")
    # From the server, so only what is a site id reaches the terminal; a server
    # from before the site id sends none.
    site_id = data.get("siteId")
    if isinstance(site_id, str) and SITE_ID_RE.fullmatch(site_id):
        site_id = site_id.lower()
        print(f"Site id: {site_id}")
        print(
            f"In the workflow, beside 'site: {group}/{site}': 'site-id: {site_id}' (action) or "
            f"'--site-id {site_id}' (CLI)."
        )
    print(f"Set up the workflow: {host}/{group}/{site}/deploy")
    return 0


def cmd_site_shielding(args: argparse.Namespace) -> int:
    host = _setup(args)
    group, site = _split_site(args.site, "The site")
    token = _get_member_token(host)

    response = _request(
        host,
        "PUT",
        f"{host}/-/api/v1/sites/{group}/{site}/sandbox",
        headers={"Authorization": f"Bearer {token}"},
        json={"sandbox": args.shielding},
        timeout=30.0,
    )
    if response is None:
        return 1
    if response.status_code != 200:
        _print_problem_detail(response)
        return 1
    state = _problem_data(response).get("sandbox")
    if type(state) is not bool:
        print("Error: unexpected answer without the site's shielding", file=sys.stderr)
        return 1
    if state:
        print(f"Shielding from other sites is on for {group}/{site}.")
    else:
        print(f"Shielding from other sites is off for {group}/{site}.")
        print(
            "Its pages now share their web address with every other site without shielding: "
            "keep it off only for content you trust."
        )
        print(f"Turn it back on with: toon site shielding {group}/{site} --on --host {host}")
    return 0
