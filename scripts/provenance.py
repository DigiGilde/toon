# /// script
# requires-python = ">=3.12"
# ///
"""Checks a report from `npm audit signatures --json --include-attestations`.

npm fails on a signature or attestation that does not verify, but not on one
that is absent: a version published with a stolen token carries a valid
registry signature and simply no provenance. So the packages in REQUIRED must
come with SLSA provenance from their own repository and publish workflow.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

SLSA_PROVENANCE = "https://slsa.dev/provenance/v1"

#: Package name -> the repository and workflow its provenance has to name.
REQUIRED: dict[str, tuple[str, str]] = {
    "@nldd/design-system": (
        "https://github.com/NederlandseDigitaleDienst/design-system",
        ".github/workflows/publish-npm.yml",
    ),
}


def built_by(entry: dict[str, Any]) -> set[tuple[str, str]]:
    """The (repository, workflow path) pairs the SLSA provenance of one
    verified package names. npm has checked the bundle before it lands in
    `verified`, so its payload can be read as is."""
    found: set[tuple[str, str]] = set()
    for attestation in entry.get("attestationBundles") or []:
        if attestation.get("predicateType") != SLSA_PROVENANCE:
            continue
        payload = json.loads(base64.b64decode(attestation["bundle"]["dsseEnvelope"]["payload"]))
        workflow = payload["predicate"]["buildDefinition"]["externalParameters"]["workflow"]
        found.add((workflow["repository"], workflow["path"]))
    return found


def problems(report: dict[str, Any], required: dict[str, tuple[str, str]]) -> list[str]:
    # npm writes {"error": ...} instead when it has nothing to audit.
    if "verified" not in report:
        return ["the report has no verified packages: did npm audit signatures run after npm ci?"]

    found = [
        f"{kind}: {entry.get('name')}@{entry.get('version')}"
        for kind in ("invalid", "missing")
        for entry in report.get(kind) or []
    ]

    verified: dict[str, list[dict[str, Any]]] = {}
    for entry in report["verified"]:
        verified.setdefault(entry["name"], []).append(entry)

    for name, (repository, workflow) in required.items():
        entries = verified.get(name)
        if not entries:
            found.append(f"{name}: installed without verified provenance")
            continue
        # One package can sit at several locations, each its own version.
        for entry in entries:
            if (repository, workflow) not in built_by(entry):
                found.append(f"{name}@{entry['version']}: provenance does not name {repository} {workflow}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check npm signatures and required provenance")
    parser.add_argument("report", type=Path, help="output of npm audit signatures --json --include-attestations")
    args = parser.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    found = problems(report, REQUIRED)
    if not found:
        print(
            f"npm signatures: {len(report['verified'])} packages with verified attestations,"
            f" provenance of {', '.join(REQUIRED)} as required."
        )
        return 0
    for problem in found:
        print(f"npm signatures: {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover - the script entry; main() is tested directly
    raise SystemExit(main())
