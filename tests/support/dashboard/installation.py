import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]


def command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@dataclass(frozen=True)
class Installations:
    root: Path
    wheel: Path
    minimal: Path
    dashboard: Path


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Installations:
    root = tmp_path_factory.mktemp("dashboard-wheel")
    result = command(["uv", "build", "--wheel", "--out-dir", str(root)], cwd=REPOSITORY)
    assert result.returncode == 0, result.stderr
    (wheel,) = root.glob("*.whl")
    environments = []
    for name, extra in [("minimal", ""), ("dashboard", "[dashboard]")]:
        environment = root / name
        result = command(
            ["uv", "venv", "--python", sys.executable, str(environment)], cwd=root
        )
        assert result.returncode == 0, result.stderr
        result = command(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(environment / "bin/python"),
                str(wheel) + extra,
            ],
            cwd=root,
        )
        assert result.returncode == 0, result.stderr
        if version := os.environ.get("MINIAGENT_COVERAGE_VERSION"):
            result = command(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    str(environment / "bin/python"),
                    f"coverage=={version}",
                ],
                cwd=root,
            )
            assert result.returncode == 0, result.stderr
        environments.append(environment)
    return Installations(root, wheel, *environments)
