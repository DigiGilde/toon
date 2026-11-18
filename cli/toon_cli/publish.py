"""toon publish and toon preview-remove."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import IO

from toon_cli.access import _parse_access, format_access
from toon_cli.common import SITE_ID_RE, UsageError, _split_site, _valid_slug
from toon_cli.hosts import _setup
from toon_cli.output import _print_problem_detail
from toon_cli.session import _get_bearer_token
from toon_cli.transport import RETRY_ANY_TRANSPORT, _request

ALLOWED_ARCHIVE_EXTENSIONS = {".html", ".zip", ".tar.gz", ".tgz"}

VERSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
# The URL characters of RFC 3986 without ' ( ) [ ]: the URL goes into
# GITHUB_OUTPUT, and from there into Markdown in the job summary and the
# pull request comment.
DEPLOY_URL_RE = re.compile(r"^https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+$")

CONTENT_TYPES = {
    ".html": "text/html",
    ".zip": "application/zip",
    ".tar.gz": "application/gzip",
    ".tgz": "application/gzip",
}


def _site_id(args: argparse.Namespace) -> str | None:
    """The site id from --site-id, else TOON_SITE_ID (empty counts as unset),
    lowercase; None without one. Checked before any token is asked for."""
    if args.site_id is not None:
        value, source = args.site_id, "--site-id"
    else:
        value, source = os.environ.get("TOON_SITE_ID") or None, "TOON_SITE_ID"
    if value is None:
        return None
    if not SITE_ID_RE.fullmatch(value):
        raise UsageError(
            f"{source} must be a site id (a UUID, from the Deploy tab of the site), got: {value!r}"
        )
    return value.lower()


def _valid_base_path(value: str) -> str:
    """Directory inside the archive that becomes the root of the site. The
    same shape the server demands: relative, without '..' and without odd
    characters."""
    if "\x00" in value or "\\" in value or value.startswith("/"):
        raise UsageError(
            f"--base-path must be a relative path inside the archive, got: {value!r}"
        )
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise UsageError(
            f"--base-path must be a relative path inside the archive, got: {value!r}"
        )
    return "/".join(parts)


def _file_extension(path: Path) -> str:
    name = path.name.lower()
    for ext in (".tar.gz", ".tgz", ".zip", ".html"):
        if name.endswith(ext):
            return ext
    return path.suffix.lower()


def _pack_folder(dist_folder: Path) -> IO[bytes]:
    files = []
    for path in sorted(dist_folder.rglob("*")):
        relative = path.relative_to(dist_folder)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            raise UsageError(f"Symlink not allowed in the dist folder: {relative}")
        if path.is_file():
            files.append((path, relative))
    if not files:
        raise UsageError(f"Folder is empty, nothing to publish: {dist_folder}")
    # A file on disk, not a buffer in memory: httpx reads it in chunks while
    # it uploads, so a large dist folder is never held whole.
    archive = tempfile.TemporaryFile()  # noqa: SIM115 - the caller closes it
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        for file_path, relative_path in files:
            tar.add(file_path, arcname=str(relative_path))
    archive.seek(0)
    return archive


def _determine_upload(dist_path: Path) -> tuple[IO[bytes], str, str]:
    """Returns (open file, filename, content type) for the upload; the caller
    closes the file.

    A directory is always packed into dist.tar.gz with paths relative to the
    directory itself; a single file with an allowed extension goes along
    unchanged.
    """
    if dist_path.is_dir():
        content = _pack_folder(dist_path)
        return content, "dist.tar.gz", CONTENT_TYPES[".tar.gz"]

    if not dist_path.is_file():
        raise UsageError(f"Path does not exist: {dist_path}")

    extension = _file_extension(dist_path)
    if extension not in ALLOWED_ARCHIVE_EXTENSIONS:
        raise UsageError(
            "Unknown file type, expected a folder or a "
            f".html/.zip/.tar.gz/.tgz file: {dist_path}"
        )
    content = dist_path.open("rb")
    content_type = CONTENT_TYPES.get(extension, "application/octet-stream")
    return content, dist_path.name, content_type


def cmd_publish(args: argparse.Namespace) -> int:
    dist_path = Path(args.dist_path)
    host = _setup(args)
    site_id = _site_id(args)
    token = _get_bearer_token(host, site_id)
    group, site = _split_site(args.site)
    if args.preview:
        _valid_slug(args.preview, "preview-ref")
    base_path = (
        _valid_base_path(args.base_path) if args.base_path is not None else None
    )
    content, file_name, content_type = _determine_upload(dist_path)

    url = f"{host}/-/api/v1/sites/{group}/{site}/deploys"

    form_fields: dict[str, str] = {}
    if args.preview:
        form_fields["preview"] = args.preview
    if base_path is not None:
        form_fields["basePath"] = base_path

    with content:
        response = _request(
            host,
            "POST",
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (file_name, content, content_type)},
            data=form_fields,
            timeout=120.0,
        )
    if response is None:
        return 1

    if response.status_code == 201:
        try:
            version_id = response.json()["versionId"]
        except (ValueError, KeyError):
            print("Error: unexpected answer without versionId", file=sys.stderr)
            return 1
        # Validate the format: a malicious server must not be able to inject
        # newlines or extra keys into the CI through GITHUB_OUTPUT.
        if not isinstance(version_id, str) or not VERSION_ID_RE.match(version_id):
            print("Error: unexpected versionId format", file=sys.stderr)
            return 1
        # A server from before the url field answers without it.
        deploy_url = response.json().get("url")
        if deploy_url is not None and (
            not isinstance(deploy_url, str) or not DEPLOY_URL_RE.match(deploy_url)
        ):
            print("Error: unexpected url format", file=sys.stderr)
            return 1
        # Only a hint for the report, so an access this CLI does not know (a
        # newer base, say) is left out rather than failing a deploy that went
        # through. A server from before the access field answers without it.
        raw_access = response.json().get("access")
        deploy_access = _parse_access(raw_access)
        if raw_access is not None and deploy_access is None:
            print("Warning: leaving out an access this CLI does not know", file=sys.stderr)
        if args.output_file:
            # Straight to the output file ($GITHUB_OUTPUT, say): in CI stdout
            # also carries the '::add-mask::' command, and a shell capturing
            # stdout for the version id would take that command along.
            try:
                with open(args.output_file, "a", encoding="utf-8") as handle:
                    handle.write(f"version-id={version_id}\n")
                    if deploy_url is not None:
                        handle.write(f"url={deploy_url}\n")
                    if deploy_access is not None:
                        handle.write(f"access={format_access(deploy_access)}\n")
            except OSError as error:
                print(
                    f"Error: publish succeeded (version {version_id}) but could not "
                    f"write to {args.output_file}: {error}",
                    file=sys.stderr,
                )
                return 1
        else:
            print(version_id)
        if deploy_url is None:
            print(f"Published version {version_id}", file=sys.stderr)
        else:
            print(f"Published: {deploy_url} (version {version_id})", file=sys.stderr)
        return 0

    _print_problem_detail(response)
    return 1


def cmd_preview_remove(args: argparse.Namespace) -> int:
    host = _setup(args)
    site_id = _site_id(args)
    token = _get_bearer_token(host, site_id)
    group, site = _split_site(args.site)
    _valid_slug(args.ref, "preview-ref")

    url = f"{host}/-/api/v1/sites/{group}/{site}/previews/{args.ref}"

    # The server accepts a CI ID token with a `jti` once, and the first try
    # may have reached it, so the retry asks for the token again: in CI that
    # is a fresh one.
    response = _request(
        host,
        "DELETE",
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
        retry_on=RETRY_ANY_TRANSPORT,
        retry_headers=lambda: {"Authorization": f"Bearer {_get_bearer_token(host, site_id)}"},
    )
    if response is None:
        return 1

    if response.status_code in (200, 204):
        print(f"Preview '{args.ref}' removed (or was already gone).", file=sys.stderr)
        return 0

    _print_problem_detail(response)
    return 1
