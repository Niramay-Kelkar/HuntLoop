"""Static safety checks on scripts/run_orchestrator_cron.sh's Docker cleanup.

The nightly wrapper rebuilds the `app` image every run (`--build`), which
accumulates untagged images and build cache - the host disk hit 98% full and
Docker Desktop crashed on 2026-11-24 because of it. A small best-effort
`docker_housekeeping` step was added 2026-11-26 to bound that growth.

These tests read the script AS TEXT and never execute it, never invoke
docker, and never touch a volume, container, image or database. Their whole
purpose is to make it loud if someone later widens that cleanup into
something destructive: the script may only ever run `docker image prune -f`
(dangling images only) and `docker builder prune -f --filter until=<N>h`
with N >= 48, and must keep `--build` on the nightly compose run line.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_orchestrator_cron.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    return SCRIPT_PATH.read_text()


@pytest.fixture(scope="module")
def code_lines(script_text: str) -> list[str]:
    """Executable, non-`echo` lines.

    Comments explain the forbidden commands by name, and the housekeeping
    function's own failure messages quote the allowed prune commands - so
    both comments and `echo` lines are excluded to leave only real commands.
    """
    lines = []
    for raw in script_text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("echo "):
            continue
        lines.append(raw)
    return lines


# Command fragments that must never appear as executable code in this script.
FORBIDDEN_PATTERNS = [
    r"docker\s+volume\s+prune",
    r"docker\s+volume\s+rm",
    r"docker\s+system\s+prune",
    r"docker\s+container\s+prune",
    r"docker\s+rm\b(?!\s*-f\s+db\s+seed)",  # `docker compose rm -f db seed` is pre-existing
    r"docker\s+rmi\b",
    r"docker\s+image\s+prune\s+(-a|--all)",
    r"docker\s+image\s+prune\s+-f\s+(-a|--all)",
    r"docker\s+builder\s+prune\s+.*--all",
    r"--volumes",
    r"docker\s+compose\s+down",
]


def test_no_forbidden_cleanup_commands(code_lines: list[str]) -> None:
    offenders = []
    for line in code_lines:
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, line):
                offenders.append((pattern, line.strip()))
    assert offenders == [], f"forbidden command(s) found in the cron script: {offenders}"


def test_only_the_two_allowed_prune_commands(code_lines: list[str]) -> None:
    prune_lines = [line.strip() for line in code_lines if re.search(r"\bprune\b", line)]
    # Filter down to lines that actually invoke docker (not a log message).
    docker_prunes = [line for line in prune_lines if re.search(r"\bdocker\s+\w+\s+prune\b", line)]
    assert len(docker_prunes) == 2, f"expected exactly 2 docker prune invocations, got {docker_prunes}"

    image_prune = [line for line in docker_prunes if "image prune" in line]
    builder_prune = [line for line in docker_prunes if "builder prune" in line]
    assert len(image_prune) == 1, docker_prunes
    assert len(builder_prune) == 1, docker_prunes

    assert "docker image prune -f" in image_prune[0]
    assert re.search(r"docker builder prune -f --filter \"until=\$\{prune_hours\}h\"", builder_prune[0])

    for line in docker_prunes:
        assert "--all" not in line, line
        assert not re.search(r"\s-a\b", line), line
        assert "--volumes" not in line, line


def test_retention_window_can_never_drop_below_48_hours(script_text: str) -> None:
    # The default, and the floor applied to any override.
    assert 'HUNTLOOP_DOCKER_PRUNE_HOURS:-72' in script_text
    assert re.search(r'if \[ "\$prune_hours" -lt 48 \]; then\s*\n\s*prune_hours=48', script_text)
    # No hard-coded sub-48h retention anywhere.
    for value in re.findall(r"until=(\d+)h", script_text):
        assert int(value) >= 48, f"retention window {value}h is below the 48h floor"


def test_prune_is_opt_outable_and_never_prints_its_env_values(script_text: str) -> None:
    assert 'HUNTLOOP_DOCKER_PRUNE:-1' in script_text
    # Neither variable may appear inside an echo'd string.
    for line in script_text.splitlines():
        if line.strip().startswith("echo"):
            assert "HUNTLOOP_DOCKER_PRUNE" not in line, line


def test_nightly_compose_run_still_builds(code_lines: list[str]) -> None:
    run_lines = [line for line in code_lines if "docker compose run" in line]
    assert run_lines, "the nightly `docker compose run` line is gone"
    for line in run_lines:
        assert "--build" in line, f"--build missing from: {line.strip()}"
        assert "main.py" in line


def test_housekeeping_runs_after_the_pipeline_stages(script_text: str) -> None:
    call = script_text.index("\n  docker_housekeeping\n")
    for stage_marker in (
        "stage 1/3: scraper orchestrator",
        "backfill_skills_matching.py",
        "backfill_department_category.py",
    ):
        assert script_text.index(stage_marker) < call, f"{stage_marker} must come before the cleanup"

    # And after the run's exit status has been decided, but before the exit.
    assert script_text.index("status=$dept_cat_status") < call
    assert call < script_text.index('exit "$status"')


def test_housekeeping_cannot_change_the_scripts_exit_status(script_text: str) -> None:
    start = script_text.index("docker_housekeeping() {")
    end = script_text.index("\n}\n", start)
    body = script_text[start:end]
    # No `exit` or `set -e` inside the function, and both prune calls are
    # wrapped so a failure is logged rather than propagated.
    assert "exit" not in body
    assert "set -e" not in body
    assert body.count("if ! docker ") == 2
    assert body.rstrip().endswith("return 0")
