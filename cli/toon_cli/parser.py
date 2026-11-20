"""The argparse parser of the toon command."""

from __future__ import annotations

import argparse

from toon_cli.access import ACCESS_BASES
from toon_cli.auth import cmd_login, cmd_logout, cmd_whoami
from toon_cli.common import VERSION
from toon_cli.feedback import (
    FEEDBACK_STATUS_CHOICES,
    cmd_feedback_list,
    cmd_feedback_reopen,
    cmd_feedback_reply,
    cmd_feedback_resolve,
    cmd_feedback_seen,
    cmd_feedback_show,
)
from toon_cli.hosts import HOST_HELP
from toon_cli.publish import cmd_preview_remove, cmd_publish
from toon_cli.sites import (
    cmd_group_create,
    cmd_site_create,
    cmd_site_link,
    cmd_site_shielding,
)

SITE_ID_HELP = (
    "Fixed id of the site, from its Deploy tab in Toon; defaults to TOON_SITE_ID. "
    "In CI the ID token is then valid for this site only"
)


def _add_access_flags(parser: argparse.ArgumentParser, defaults: tuple[str, str, str]) -> None:
    """--access, --secret-links and --invitees, with what leaving each out means."""
    base_default, keys_default, invitees_default = defaults
    parser.add_argument(
        "--access",
        choices=ACCESS_BASES,
        default=None,
        help=(
            "Who may see the content: public (anyone), sso (anyone who signs in "
            "with SSO Rijk), site_team (members of the site and its group) or "
            f"nobody (only through the exceptions below). Left out: {base_default}"
        ),
    )
    parser.add_argument(
        "--secret-links",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=f"Whether a valid secret link lets anyone in, without signing in. Left out: {keys_default}",
    )
    parser.add_argument(
        "--invitees",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=f"Whether invitees get in after signing in with SSO Rijk. Left out: {invitees_default}",
    )
    parser.add_argument(
        "--host", default=None, help=HOST_HELP
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toon",
        description="Publish static sites to Toon and manage previews.",
    )
    parser.add_argument("--version", action="version", version=f"toon-cli {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser(
        "login", help="Log in and store the session for your user account."
    )
    login.add_argument("--host", default=None, help=HOST_HELP)
    login.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the login URL in the browser automatically",
    )
    login.add_argument(
        "--insecure-storage",
        action="store_true",
        help="Store the session in plain text in the config directory instead of the system keyring",
    )
    login.set_defaults(func=cmd_login)

    logout = subparsers.add_parser("logout", help="Log out and wipe the session.")
    logout.add_argument("--host", default=None, help=HOST_HELP)
    logout.set_defaults(func=cmd_logout)

    whoami = subparsers.add_parser("whoami", help="Show who is logged in.")
    whoami.add_argument("--host", default=None, help=HOST_HELP)
    whoami.set_defaults(func=cmd_whoami)

    publish = subparsers.add_parser(
        "publish",
        help="Publish a dist folder or file as a live or preview version.",
    )
    publish.add_argument(
        "dist_path", help="Path to the dist folder or the file to publish"
    )
    publish.add_argument("--host", default=None, help=HOST_HELP)
    publish.add_argument("--site", required=True, help="group/site")
    publish.add_argument("--site-id", default=None, help=SITE_ID_HELP)
    publish.add_argument(
        "--preview",
        help="Preview ref, for instance pr-42; without this field it is a live deploy",
    )
    publish.add_argument(
        "--base-path",
        default=None,
        help=(
            "Folder inside the archive that becomes the root of the site, for "
            "instance dist. Use this when the bundle holds more than the site "
            "itself; without this flag Toon determines the root"
        ),
    )
    publish.add_argument(
        "--output-file",
        default=None,
        help=(
            "Write 'version-id=<id>' to this file (for instance $GITHUB_OUTPUT) "
            "instead of printing the id on stdout; used by the action"
        ),
    )
    publish.set_defaults(func=cmd_publish)

    remove = subparsers.add_parser(
        "preview-remove", help="Remove a preview (idempotent)."
    )
    remove.add_argument("ref", help="Preview ref to remove")
    remove.add_argument("--host", default=None, help=HOST_HELP)
    remove.add_argument("--site", required=True, help="group/site")
    remove.add_argument("--site-id", default=None, help=SITE_ID_HELP)
    remove.set_defaults(func=cmd_preview_remove)

    group = subparsers.add_parser("group", help="Manage groups.")
    group_commands = group.add_subparsers(dest="group_command", required=True)
    group_create = group_commands.add_parser(
        "create",
        help="Create a group; you become its admin.",
        description=(
            "Create a group and become its admin. The access flags set the "
            "default access that new sites in the group start with."
        ),
    )
    group_create.add_argument("group", help="Slug of the group, for instance team-aurora")
    group_create.add_argument("--name", required=True, help="Display name of the group")
    _add_access_flags(group_create, ("site_team", "off", "off"))
    group_create.set_defaults(func=cmd_group_create)

    site = subparsers.add_parser("site", help="Manage sites.")
    site_commands = site.add_subparsers(dest="site_command", required=True)
    site_create = site_commands.add_parser(
        "create",
        help="Create a site in a group; you become its admin.",
        description=(
            "Create a site in a group where you are editor or admin, and become "
            "admin of the site. Access flags you leave out follow the group's "
            "default access."
        ),
    )
    site_create.add_argument("site", help="group/site, for instance team-aurora/docs")
    site_create.add_argument("--title", required=True, help="Display name of the site")
    _add_access_flags(site_create, ("the group's default",) * 3)
    site_create.set_defaults(func=cmd_site_create)

    site_link = site_commands.add_parser(
        "link",
        help="Link the repository that may publish to a site from CI.",
        description=(
            "Link a GitHub or Forgejo repository to a site where you are admin, so "
            "its workflows may publish with an OIDC ID token, without a secret. "
            "Without a repository argument it takes the remote 'origin' of the "
            "git checkout you are in. For a GitHub repository the IDs come from "
            "your own 'gh' login, so a private repository links too."
        ),
    )
    site_link.add_argument("site", help="group/site, for instance team-aurora/docs")
    site_link.add_argument(
        "repository",
        nargs="?",
        default=None,
        help=(
            "owner/repo (on GitHub) or the repository URL, for instance "
            "https://code.overheid.nl/minbzk/website. Left out: the remote 'origin' here"
        ),
    )
    site_link.add_argument(
        "--live-branch",
        default=None,
        help=(
            "The only branch that may publish live, for instance main. Left out: every "
            "branch may, still only from a push, a manual run or a schedule"
        ),
    )
    site_link.add_argument(
        "--repository-id",
        type=int,
        default=None,
        help="Numeric ID of the repository, for one Toon and gh cannot look up (with --owner-id)",
    )
    site_link.add_argument(
        "--owner-id", type=int, default=None, help="Numeric ID of the repository's owner (with --repository-id)"
    )
    site_link.add_argument(
        "--no-gh",
        dest="gh",
        action="store_false",
        help="Do not ask gh for the IDs; Toon looks the repository up itself, which finds a public one only",
    )
    site_link.add_argument("--host", default=None, help=HOST_HELP)
    site_link.set_defaults(func=cmd_site_link)

    site_shielding = site_commands.add_parser(
        "shielding",
        help="Turn a site's shielding from other sites on or off.",
        description=(
            "Turn a site's shielding from other sites on or off. It is on by default: "
            "the site's pages get an opaque origin, so they cannot read the other sites "
            "at the shared web address, nor store anything in the visitor's browser. "
            "You must be admin of the site. Turn it off for a site that needs module "
            "scripts, web fonts or browser storage, or whose viewers' browsers block "
            "third-party cookies. The site then shares its web address with the other "
            "sites without shielding, so do that only for content you trust. In a "
            "preview environment only a platform administrator can turn it off."
        ),
    )
    site_shielding.add_argument("site", help="group/site, for instance team-aurora/docs")
    shielding_state = site_shielding.add_mutually_exclusive_group(required=True)
    shielding_state.add_argument(
        "--on",
        dest="shielding",
        action="store_const",
        const=True,
        help="Turn the shielding on (the default for a new site)",
    )
    shielding_state.add_argument(
        "--off",
        dest="shielding",
        action="store_const",
        const=False,
        help="Turn the shielding off; only for a site whose content you trust",
    )
    site_shielding.add_argument("--host", default=None, help=HOST_HELP)
    site_shielding.set_defaults(func=cmd_site_shielding)

    feedback = subparsers.add_parser(
        "feedback", help="Read and handle the feedback visitors left on a site."
    )
    feedback_commands = feedback.add_subparsers(dest="feedback_command", required=True)

    def feedback_command(name: str, help_text: str) -> argparse.ArgumentParser:
        command = feedback_commands.add_parser(name, help=help_text)
        command.add_argument(
            "--site", required=True, help="group/site, for instance team-aurora/website"
        )
        command.add_argument("--host", default=None, help=HOST_HELP)
        return command

    def add_output_flags(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--json",
            action="store_true",
            help="Print JSON: trusted values at the top, what visitors wrote under 'untrusted'",
        )
        command.add_argument(
            "--with-contact",
            action="store_true",
            help="Include the names and email addresses of the people who gave feedback or replied, and who changed the status",
        )

    feedback_list = feedback_command("list", "List the feedback of a site.")
    feedback_list.add_argument(
        "--status",
        choices=FEEDBACK_STATUS_CHOICES,
        default="open",
        help="Which feedback; default open (new and seen)",
    )
    feedback_target = feedback_list.add_mutually_exclusive_group()
    feedback_target.add_argument(
        "--preview", default=None, metavar="REF", help="Only feedback on this preview"
    )
    feedback_target.add_argument(
        "--live", action="store_true", help="Only feedback on the live site"
    )
    add_output_flags(feedback_list)
    feedback_list.set_defaults(func=cmd_feedback_list)

    feedback_show = feedback_command("show", "Show one piece of feedback.")
    feedback_show.add_argument("id", help="ID of the feedback")
    feedback_show.add_argument(
        "--screenshot",
        default=None,
        metavar="PATH",
        help="Save the screenshot to this new file and print its path",
    )
    add_output_flags(feedback_show)
    feedback_show.set_defaults(func=cmd_feedback_show)

    feedback_seen = feedback_command(
        "seen", "Mark new feedback as seen, and leave feedback in another status as it is."
    )
    feedback_seen.add_argument("ids", nargs="+", metavar="ID", help="ID of the feedback")
    feedback_seen.set_defaults(func=cmd_feedback_seen)

    message_file_help = (
        "Read the reply from this UTF-8 file instead, so the text never passes through a "
        "shell. A leading byte order mark and the trailing newlines are dropped, and CRLF "
        "and CR line ends become LF"
    )

    feedback_resolve = feedback_command("resolve", "Mark feedback as resolved.")
    feedback_resolve.add_argument("ids", nargs="+", metavar="ID", help="ID of the feedback")
    resolve_message = feedback_resolve.add_mutually_exclusive_group()
    resolve_message.add_argument(
        "--message",
        default=None,
        help="Reply to post before resolving; the submitter can read it, with your name",
    )
    resolve_message.add_argument(
        "--message-file", default=None, metavar="PATH", help=message_file_help
    )
    feedback_resolve.set_defaults(func=cmd_feedback_resolve)

    feedback_reopen = feedback_command("reopen", "Set feedback back to new.")
    feedback_reopen.add_argument("ids", nargs="+", metavar="ID", help="ID of the feedback")
    feedback_reopen.set_defaults(func=cmd_feedback_reopen)

    feedback_reply = feedback_command("reply", "Reply to one piece of feedback.")
    feedback_reply.add_argument("id", help="ID of the feedback")
    reply_message = feedback_reply.add_mutually_exclusive_group(required=True)
    reply_message.add_argument(
        "--message", default=None, help="The reply; the submitter can read it, with your name"
    )
    reply_message.add_argument(
        "--message-file", default=None, metavar="PATH", help=message_file_help
    )
    feedback_reply.set_defaults(func=cmd_feedback_reply)

    return parser
