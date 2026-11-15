"""The container's entrypoint, pinned down here rather than in a rollout.

ZAD has no Job object and no `command` field on a component, so the image
itself carries "migrate before serving" (docs/deploying-on-zad.md §7). That
makes four properties of this one shell script load-bearing, and none of
them fails loudly: a missing `exec` only shows up as a rollout that hangs
for its full grace period, and a missing `set -e` only as an app serving
against a half-migrated schema.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

CONTAINER = Path(__file__).resolve().parents[2] / "containers" / "toon"


@pytest.fixture(scope="module")
def entrypoint() -> str:
    return (CONTAINER / "entrypoint.sh").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def containerfile() -> str:
    return (CONTAINER / "Containerfile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerignore() -> str:
    return (CONTAINER.parents[1] / ".dockerignore").read_text(encoding="utf-8")


def test_the_image_starts_the_entrypoint(containerfile: str) -> None:
    assert 'CMD ["./entrypoint.sh"]' in containerfile


def test_the_entrypoint_is_copied_into_the_image(containerfile: str) -> None:
    assert "containers/toon/entrypoint.sh ./entrypoint.sh" in containerfile


def test_a_failing_step_stops_the_script(entrypoint: str) -> None:
    # Without `set -e` a failed migration is followed by a server that
    # answers requests against whatever schema it found.
    assert "set -eu" in entrypoint


def test_the_migration_runs_before_the_server(entrypoint: str) -> None:
    migration = entrypoint.index("alembic upgrade head")
    server = entrypoint.index("exec uvicorn")
    assert migration < server


def test_the_server_replaces_the_shell(entrypoint: str) -> None:
    # Without exec, uvicorn is a child of this shell and the SIGTERM that
    # ends a rollout reaches the shell, which ignores it; the pod then waits
    # out its termination grace period on every deploy.
    assert "exec uvicorn" in entrypoint


def test_the_access_log_stays_off(entrypoint: str) -> None:
    # uvicorn's access log writes path plus querystring, so every secret
    # link (?key=...) would land in the log stack (docs/security.md).
    assert "--no-access-log" in entrypoint


def test_waiting_for_the_database_is_bounded(entrypoint: str) -> None:
    # An unbounded wait turns an unreachable database into a pod that looks
    # alive and never serves.
    assert "-ge 30" in entrypoint
    assert "exit 1" in entrypoint


def test_uvicorn_does_not_derive_the_client_itself(entrypoint: str) -> None:
    # With proxy headers on, uvicorn rewrites request.client from
    # X-Forwarded-For as soon as the peer is in --forwarded-allow-ips, whose
    # default reads the FORWARDED_ALLOW_IPS environment variable. net.py would
    # then walk the header starting from an address that already came out of
    # it, and `vouched` would stop meaning anything.
    assert "--no-proxy-headers" in entrypoint


def _stages(containerfile: str) -> dict[str, str]:
    """Every build stage by name, with its instructions."""
    stages: dict[str, str] = {}
    for chunk in containerfile.split("\nFROM ")[1:]:
        header, _, body = chunk.partition("\n")
        stages[header.split(" AS ")[-1].strip()] = body
    return stages


def test_only_the_preview_target_carries_the_demo_seed(containerfile: str) -> None:
    """The demo seed is no part of the production image: a preview is its
    own target, the runtime stage plus the seed and nothing else."""
    stages = _stages(containerfile)
    assert list(stages) == ["spa", "build", "runtime", "preview"]
    assert "\nFROM runtime AS preview\n" in containerfile
    assert "demo_seed" not in stages["runtime"]
    instructions = [line for line in stages["preview"].splitlines() if line and not line.startswith("#")]
    assert instructions == ["COPY --chown=toon:toon --chmod=444 dev/seed.py ./demo_seed.py"]


def test_the_demo_seed_survives_the_build_context(dockerignore: str) -> None:
    # dev/ as a whole stays out of the build context: only the seed comes back.
    lines = dockerignore.splitlines()
    assert lines.index("!dev/seed.py") > lines.index("dev")


def test_the_demo_seed_runs_after_the_migration_and_before_the_server(entrypoint: str) -> None:
    seed = entrypoint.index("python /app/demo_seed.py")
    assert entrypoint.index("alembic upgrade head") < seed < entrypoint.index("exec uvicorn")


class TestThePreviewSeedAtStartup:
    """The entrypoint as written, with alembic, python and uvicorn faked on
    PATH and the seed's path pointed at a temporary file."""

    @pytest.fixture
    def run(self, entrypoint: str, tmp_path: Path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        calls = tmp_path / "calls"
        for name in ("alembic", "python", "uvicorn"):
            fake = bin_dir / name
            fake.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\n', encoding="utf-8")
            fake.chmod(0o755)
        seed = tmp_path / "demo_seed.py"
        script = tmp_path / "entrypoint.sh"
        script.write_text(entrypoint.replace("/app/demo_seed.py", str(seed)), encoding="utf-8")

        def run(environment: str | None, *, with_seed: bool) -> tuple[subprocess.CompletedProcess, list[str]]:
            if with_seed:
                seed.write_text("", encoding="utf-8")
            env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
            if environment is not None:
                env["TOON_ENVIRONMENT"] = environment
            result = subprocess.run(  # noqa: S603
                ["sh", str(script)],  # noqa: S607
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            made = calls.read_text(encoding="utf-8").splitlines()
            return result, [call.split(" ")[0] + (" seed" if str(seed) in call else "") for call in made]

        return run

    def test_a_preview_image_seeds_and_then_serves(self, run) -> None:
        result, calls = run("preview", with_seed=True)
        assert result.returncode == 0, result.stderr
        assert calls == ["alembic", "alembic", "python seed", "uvicorn"]

    def test_a_production_image_run_as_a_preview_stops_before_serving(self, run) -> None:
        result, calls = run("preview", with_seed=False)
        assert result.returncode == 1
        assert result.stderr == "entrypoint: this image has no demo seed: build it with --target preview\n"
        assert calls == ["alembic", "alembic"]

    @pytest.mark.parametrize("environment", ["productie", "dev", None])
    def test_outside_a_preview_the_seed_is_not_looked_for(self, run, environment) -> None:
        result, calls = run(environment, with_seed=False)
        assert result.returncode == 0, result.stderr
        assert calls == ["alembic", "alembic", "uvicorn"]
