"""The provenance check on the frontend dependencies (scripts/provenance.py).

`npm audit signatures` passes a package that has no provenance at all, which is
exactly what a version published with a stolen token looks like. Every way a
required package can fall short is a refusal of its own here.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "provenance.py"

NAME = "@nldd/design-system"
REPOSITORY = "https://github.com/NederlandseDigitaleDienst/design-system"
WORKFLOW = ".github/workflows/publish-npm.yml"
REQUIRED = {NAME: (REPOSITORY, WORKFLOW)}


def _module():
    spec = importlib.util.spec_from_file_location("provenance", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["provenance"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _module()


def _bundle(predicate_type: str, predicate: dict) -> dict:
    payload = {"predicateType": predicate_type, "predicate": predicate}
    return {
        "predicateType": predicate_type,
        "bundle": {"dsseEnvelope": {"payload": base64.b64encode(json.dumps(payload).encode()).decode()}},
    }


def _publish() -> dict:
    """The npm publish attestation, which names no workflow."""
    return _bundle(
        "https://github.com/npm/attestation/tree/main/specs/publish/v0.1",
        {"name": NAME, "version": "0.21.1", "registry": "https://registry.npmjs.org"},
    )


def _slsa(repository: str = REPOSITORY, path: str = WORKFLOW) -> dict:
    workflow = {"ref": "refs/heads/main", "repository": repository, "path": path}
    predicate = {"buildDefinition": {"externalParameters": {"workflow": workflow}}}
    return _bundle("https://slsa.dev/provenance/v1", predicate)


def _entry(*bundles: dict, name: str = NAME, version: str = "0.21.1") -> dict:
    return {"name": name, "version": version, "attestationBundles": list(bundles)}


def _report(*verified: dict, invalid: list | None = None, missing: list | None = None) -> dict:
    return {"invalid": invalid or [], "missing": missing or [], "verified": list(verified)}


class TestTheRequiredList:
    def test_the_design_system_must_come_from_its_own_publish_workflow(self, script) -> None:
        assert script.REQUIRED == REQUIRED


class TestProblems:
    def test_provenance_from_the_named_workflow_passes(self, script) -> None:
        report = _report(_entry(_publish(), _slsa()), _entry(name="lit"))

        assert script.problems(report, REQUIRED) == []

    def test_a_version_without_provenance_is_refused(self, script) -> None:
        """What a publish with a stolen token looks like: signed by the
        registry, so npm passes it, but absent from `verified`."""
        report = _report(_entry(_slsa(), name="lit"))

        assert script.problems(report, REQUIRED) == [f"{NAME}: installed without verified provenance"]

    def test_only_a_publish_attestation_is_refused(self, script) -> None:
        """npm verifies the publish attestation too, but that one names no
        build; the package is in `verified` without saying where it was built."""
        report = _report(_entry(_publish()))

        assert script.problems(report, REQUIRED) == [
            f"{NAME}@0.21.1: provenance does not name {REPOSITORY} {WORKFLOW}"
        ]

    def test_provenance_from_another_repository_is_refused(self, script) -> None:
        report = _report(_entry(_slsa(repository="https://github.com/someone/design-system")))

        assert script.problems(report, REQUIRED) == [
            f"{NAME}@0.21.1: provenance does not name {REPOSITORY} {WORKFLOW}"
        ]

    def test_provenance_from_another_workflow_in_the_repository_is_refused(self, script) -> None:
        report = _report(_entry(_slsa(path=".github/workflows/manual.yml")))

        assert script.problems(report, REQUIRED) == [
            f"{NAME}@0.21.1: provenance does not name {REPOSITORY} {WORKFLOW}"
        ]

    def test_every_installed_copy_is_held_to_it(self, script) -> None:
        report = _report(_entry(_slsa()), _entry(_publish(), version="0.20.0"))

        assert script.problems(report, REQUIRED) == [
            f"{NAME}@0.20.0: provenance does not name {REPOSITORY} {WORKFLOW}"
        ]

    def test_an_invalid_signature_is_refused(self, script) -> None:
        report = _report(_entry(_slsa()), invalid=[{"name": "lit", "version": "3.3.1"}])

        assert script.problems(report, REQUIRED) == ["invalid: lit@3.3.1"]

    def test_a_missing_registry_signature_is_refused(self, script) -> None:
        report = _report(_entry(_slsa()), missing=[{"name": "lit", "version": "3.3.1"}])

        assert script.problems(report, REQUIRED) == ["missing: lit@3.3.1"]

    def test_a_report_without_packages_is_refused(self, script) -> None:
        """npm writes an error object when node_modules is absent; that may
        not pass as a report without findings."""
        report = {"error": {"code": "ENOAUDIT", "summary": "found no installed dependencies to audit"}}

        assert script.problems(report, REQUIRED) == [
            "the report has no verified packages: did npm audit signatures run after npm ci?"
        ]


class TestMain:
    def test_a_clean_report_exits_zero(self, script, tmp_path: Path, capsys) -> None:
        path = tmp_path / "report.json"
        path.write_text(json.dumps(_report(_entry(_publish(), _slsa()))), encoding="utf-8")

        assert script.main([str(path)]) == 0
        assert f"provenance of {NAME} as required" in capsys.readouterr().out

    def test_a_refusal_exits_one_and_says_why_on_stderr(self, script, tmp_path: Path, capsys) -> None:
        path = tmp_path / "report.json"
        path.write_text(json.dumps(_report()), encoding="utf-8")

        assert script.main([str(path)]) == 1
        assert capsys.readouterr().err == f"npm signatures: {NAME}: installed without verified provenance\n"
