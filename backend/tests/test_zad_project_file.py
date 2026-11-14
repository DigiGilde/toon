"""Shape checks on `deploy/toon-project.example.yaml`, the ZAD project file.

The ZAD schema (`operations-manager/python/opi/schemas/project_v2.json`,
RijksICTGilde/RIG-Cluster) does not live in this repo, so what is pinned here
is a reading of schema 2.17 as last checked. The platform checks a file
against the schema of the `schema-version` it names. What matters for us:

* a service entry of the project or of a component is a plain string or a
  record with `name` and `config`, the form the platform writes back;
* a deployment's own service is a record with `reference` (or `name`) and
  `config`: the single-key form a project entry may still use is refused there;
* the domain approval block `domains` sits in the config of the project's
  `publish-on-web` service, not at the project root;
* the web address fields (`domain-format`, `subdomain`, `base-domain`,
  `issuer`, `root-component`) sit in the config of the deployment's
  `publish-on-web` service, not on the deployment;
* there is no `domain-mode` any more: the platform's v2.8 migration turned
  `nice-url` into `domain-format: component.subdomain` and dropped the key;
* a component has no `probe`;
* `backup.enabled` in the root only sets the PVC label; scheduling happens per
  deployment with an RRULE.

When ZAD's own `project_v2.json` of the version the file names is present on
disk (a sibling checkout of RijksICTGilde/RIG-Cluster),
`test_validates_against_zad_schema` validates the file against it directly
instead of relying on this reading.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from toon.config import normalise_operator_email

PROJECT_FILE = Path(__file__).resolve().parents[2] / "deploy" / "toon-project.example.yaml"
PROJECT: dict[str, Any] = yaml.safe_load(PROJECT_FILE.read_text(encoding="utf-8"))

#: A checkout of RijksICTGilde/RIG-Cluster next to this repo, if there is one.
ZAD_SCHEMA_FILE = (
    Path.home()
    / "Projects"
    / "zad"
    / "zad-platform"
    / "operations-manager"
    / "python"
    / "opi"
    / "schemas"
    / "project_v2.json"
)

#: The domain formats that yield a root address alongside the component address:
#: dot-separated and with a {component} prefix that may be left off.
ROOT_ADDRESS_FORMATS = frozenset(
    {"component.deployment.project", "component.deployment.subdomain", "component.subdomain"}
)

#: The fields that make up a deployment's web address.
WEB_ADDRESS_FIELDS = frozenset({"domain-format", "subdomain", "base-domain", "issuer", "root-component"})


def _service_config(services: list[Any] | None, name: str) -> Any:
    """The config of service `name` from a list of service records, or None."""
    for item in services or []:
        if isinstance(item, dict) and name in (item.get("name"), item.get("reference")):
            return item.get("config")
    return None


def _web_address(deployment: dict[str, Any]) -> dict[str, Any]:
    """The deployment's `publish-on-web` config, where its web address sits."""
    return _service_config(deployment.get("services"), "publish-on-web") or {}


def test_declares_the_operations_manager_latest_schema_version() -> None:
    """The platform checks a file against the schema of the version it names,
    so the example names the latest, LATEST_SCHEMA_VERSION of the Operations
    Manager. A string, because unquoted 2.10 would read as 2.1."""
    assert PROJECT["schema-version"] == "2.17"


def test_service_entries_are_a_string_or_a_name_and_config_record() -> None:
    """`service-entry` takes a string, a `{name|reference, config}` record or
    a legacy single-key object. The example keeps to the record, the form the
    platform writes back, so a reader copies one shape."""
    for services in (PROJECT["services"], *(c["services"] for c in PROJECT["components"])):
        for item in services:
            if isinstance(item, dict):
                assert "name" in item, f"service entry without a name: {item!r}"
                assert set(item) <= {"name", "config"}, f"service entry with another key: {item!r}"


def test_deployment_services_are_records() -> None:
    """`deployment-service` allows `name`, `reference`, `schema-version` and
    `config` and nothing else, so a single-key entry such as
    `- publish-on-web: {config: ...}` is refused on a deployment."""
    for deployment in PROJECT["deployments"]:
        for item in deployment["services"]:
            assert {"name", "reference"} & set(item), f"service without a name or reference: {item!r}"
            assert set(item) <= {"name", "reference", "schema-version", "config"}, f"unknown key: {item!r}"


def test_approval_block_is_in_the_project_publish_on_web_config() -> None:
    assert "domains" not in PROJECT
    assert "domains" in (_service_config(PROJECT["services"], "publish-on-web") or {})


def test_requested_domain_and_subdomain_belong_on_the_deployment() -> None:
    domains = _service_config(PROJECT["services"], "publish-on-web")["domains"]
    web = _web_address(PROJECT["deployments"][0])

    assert [d["domain"] for d in domains["allowed-domains"]] == [web["base-domain"]]
    subdomains = [s["name"] for d in domains["allowed-subdomains"] for s in d["subdomains"]]
    assert web["subdomain"] in subdomains


def test_no_probe_on_a_component() -> None:
    for component in PROJECT["components"]:
        assert "probe" not in component


def test_url_fields_sit_in_the_deployment_publish_on_web_config() -> None:
    """Since schema 2.7 the deployment itself has none of these fields, and
    the latest schema refuses them there."""
    for deployment in PROJECT["deployments"]:
        assert not WEB_ADDRESS_FIELDS & set(deployment), "the web address belongs in publish-on-web"
        web = _web_address(deployment)
        assert web["domain-format"]
        assert web["subdomain"]
        assert web["base-domain"]
        assert web["root-component"]


def test_root_address_needs_a_root_format_and_a_root_component() -> None:
    """The root address (toon.<domain>) needs domain-format, subdomain,
    base-domain and root-component all four right. Drop one and there is
    silently no root address, and then the TOON_*_BASE_URLs point at a host
    that never exists. `domain-mode` is gone since the platform's v2.8
    migration, so the example does not carry it."""
    for deployment in PROJECT["deployments"]:
        web = _web_address(deployment)
        assert web["domain-format"] in ROOT_ADDRESS_FORMATS
        assert "domain-mode" not in web and "domain-mode" not in deployment
        components = [c["reference"] for c in deployment["components"]]
        assert web["root-component"] in components


def test_backup_becomes_per_deployment_scheduled() -> None:
    """`backup.enabled` in the root only sets the label that makes the PVC
    eligible; without a schedule on the deployment the platform schedules
    nothing and only the manual backup exists."""
    assert PROJECT["backup"]["enabled"] is True
    for deployment in PROJECT["deployments"]:
        schedule = deployment["backup"]["schedule"]
        assert schedule.startswith("FREQ=")
        assert "pvc" in deployment["backup"]["resource_types"]


def test_the_10gi_volume_runs_with_the_default_site_quota() -> None:
    """10Gi is a grant above the catalogue's 1Gi; on it the 500 MiB default
    fits some nineteen sites at their maximum, so ZAD sets no quota of its own."""
    (storage,) = _service_config(PROJECT["components"][0]["services"], "persistent-storage")
    assert storage["size"] == "10Gi"
    env = PROJECT["deployments"][0]["components"][0]["env-vars"]
    assert "TOON_BASE_URL" in env, "the file no longer uses the names this test looks for"
    assert "TOON_SITE_MAX_BYTES" not in env


def test_urls_come_up_with_the_composed_hosts() -> None:
    """The app derives host separation, HSTS and every shareable URL from
    these two, so they must be literally the hosts the domain format
    produces."""
    deployment = PROJECT["deployments"][0]
    web = _web_address(deployment)
    admin_host = f"{web['root-component']}.{web['subdomain']}.{web['base-domain']}"
    content_host_name = f"{web['subdomain']}.{web['base-domain']}"

    env = deployment["components"][0]["env-vars"]
    assert env["TOON_BASE_URL"] == f"https://{admin_host}"
    assert env["TOON_CONTENT_BASE_URL"] == f"https://{content_host_name}"

    keycloak = _service_config(PROJECT["services"], "keycloak")
    assert f"https://{content_host_name}/-/oauth2/callback" in keycloak["additional_redirect_uris"]


def test_the_operator_is_named_with_an_address_the_app_accepts() -> None:
    """The app refuses to start on an address that is not plain, so the
    example must hold one it takes as it stands."""
    env = PROJECT["deployments"][0]["components"][0]["env-vars"]
    assert env["TOON_OPERATOR_NAME"].strip()
    assert normalise_operator_email(env["TOON_OPERATOR_EMAIL"]) == env["TOON_OPERATOR_EMAIL"]


def test_security_txt_lists_ncsc_for_this_central_government_installation() -> None:
    """The Digi Gilde runs inside the Dutch central government, where NCSC-NL
    is the central CVD reporting point. The setting is off by default, so
    without this line the NCSC-NL routes leave the live security.txt."""
    env = PROJECT["deployments"][0]["components"][0]["env-vars"]
    assert env["TOON_SECURITY_TXT_NCSC"] == "true"


@pytest.mark.skipif(
    not ZAD_SCHEMA_FILE.exists(), reason="no local RIG-Cluster checkout with opi/schemas/project_v2.json"
)
def test_validates_against_zad_schema() -> None:
    import json

    from jsonschema import Draft202012Validator

    schema = json.loads(ZAD_SCHEMA_FILE.read_text(encoding="utf-8"))
    if schema.get("x-zad-schema-version") != PROJECT["schema-version"]:
        pytest.skip("the local RIG-Cluster checkout holds another schema version than the file names")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    errors = sorted(validator.iter_errors(PROJECT), key=lambda e: list(e.absolute_path))
    assert not errors, "\n".join(
        f"{'/'.join(str(p) for p in e.absolute_path) or '(root)'}: {e.message}" for e in errors
    )
