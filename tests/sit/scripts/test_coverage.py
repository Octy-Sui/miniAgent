"""Exercise the coverage runner against real, small Python projects."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    script = REPOSITORY / "coverage.py"
    assert script.is_file(), "coverage runner is not implemented"
    shutil.copy(script, tmp_path / "coverage.py")
    package = tmp_path / "src/miniagent"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "choice.py").write_text(
        "def choose(flag):\n    if flag:\n        return 1\n    return 2\n"
    )
    (package / "unused.py").write_text("def unused():\n    return 3\n")
    (tmp_path / "trigger").write_text("answer = 42\n")
    for level in ("ut", "it", "sit", "e2e"):
        directory = tmp_path / "tests" / level
        directory.mkdir(parents=True)
        (directory / f"test_{level}.py").write_text("def test_ok():\n    assert True\n")
    (tmp_path / "tests/ut/test_ut.py").write_text(
        "from miniagent.choice import choose\n"
        "def test_true():\n    assert choose(True) == 1\n"
    )
    (tmp_path / "tests/it/test_it.py").write_text(
        "import subprocess, sys\n"
        "def test_child():\n"
        "    subprocess.run([sys.executable, '-c', "
        "'from miniagent.choice import choose; assert choose(False) == 2'], check=True)\n"
    )
    return tmp_path


def run(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_", "MINIAGENT_COVERAGE_"))
    }
    environment.update(
        PYTHONPATH=str(project / "src"), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"
    )
    return subprocess.run(
        [sys.executable, str(project / "coverage.py"), "run", *args],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_layers_union_subprocess_and_unexecuted_sources(project: Path) -> None:
    result = run(project, "--all", "--run-id", "union")
    assert result.returncode == 0, result.stdout + result.stderr
    report = project / "coverage/union"
    summary = json.loads((report / "summary.json").read_text())
    assert summary["levels"]["ut"]["totals"]["covered_lines"] == 3
    assert summary["levels"]["it"]["totals"]["covered_lines"] == 3
    assert summary["levels"]["ut"]["totals"]["branch_percent"] == 50
    assert summary["levels"]["it"]["totals"]["branch_percent"] == 50
    assert summary["levels"]["all"]["totals"] == {
        "covered_lines": 4,
        "num_statements": 7,
        "line_percent": 100 * 4 / 7,
        "covered_branches": 2,
        "num_branches": 2,
        "branch_percent": 100,
    }
    assert summary["levels"]["all"]["modules"]["unused"]["line_percent"] == 0
    assert summary["levels"]["all"]["modules"]["unused"]["branch_percent"] is None
    for level in ("ut", "it", "sit", "e2e", "all"):
        assert (report / level / "html/index.html").is_file()
        assert (report / level / "coverage.xml").is_file()
        assert (report / level / "coverage.json").is_file()
    assert (report / "summary.md").is_file()
    again = run(project, "--all", "--run-id", "union")
    assert again.returncode != 0
    assert json.loads((report / "summary.json").read_text()) == summary


def test_failed_tests_still_report_and_preserve_exit_status(project: Path) -> None:
    (project / "tests/ut/test_ut.py").write_text(
        "from miniagent.choice import choose\n"
        "def test_failure():\n    assert choose(True) == 99\n"
    )
    result = run(project, "--level", "ut", "--run-id", "failure")
    assert result.returncode == 1, result.stdout + result.stderr
    summary = json.loads((project / "coverage/failure/summary.json").read_text())
    assert summary["selected_levels"] == ["ut"]
    assert summary["levels"]["ut"]["pytest_exit_code"] == 1
    assert summary["levels"]["ut"]["tests"]["failures"] == 1
    assert summary["levels"]["all"]["totals"]["covered_lines"] == 3


def test_root_script_does_not_shadow_coverage_package(project: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", "from coverage import Coverage; import coverage.data"],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_installed_paths_merge_into_source_module(project: Path) -> None:
    site = project / "installed/lib/python3.13/site-packages"
    shutil.copytree(project / "src/miniagent", site / "miniagent")
    (project / "tests/it/test_it.py").write_text(
        "import os, subprocess, sys\n"
        "def test_installed():\n"
        "    subprocess.run([sys.executable, '-c', "
        "'from miniagent.choice import choose; assert choose(False) == 2'], "
        f"env={{**os.environ, 'PYTHONPATH': {str(site)!r}}}, check=True)\n"
    )
    result = run(project, "--level", "ut", "--level", "it", "--run-id", "installed")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((project / "coverage/installed/summary.json").read_text())
    assert report["levels"]["all"]["totals"]["num_statements"] == 7
    assert report["levels"]["all"]["totals"]["covered_lines"] == 4
    assert report["levels"]["it"]["modules"]["choice"]["branch_percent"] == 50


@pytest.mark.parametrize(("identifier",), [("../escape",), ("/tmp/escape",)])
def test_run_id_cannot_escape_output_directory(project: Path, identifier: str) -> None:
    result = run(project, "--level", "ut", "--run-id", identifier)
    assert result.returncode == 2
    assert not (project / "coverage").exists()


def test_system_coverage_unions_sit_e2e_without_ut_it(project: Path) -> None:
    (project / "tests/ut/test_ut.py").write_text(
        "from miniagent.unused import unused\n"
        "def test_unit_only():\n    assert unused() == 3\n"
    )
    for level, flag, expected in [("sit", True, 1), ("e2e", False, 2)]:
        (project / f"tests/{level}/test_{level}.py").write_text(
            "from miniagent.choice import choose\n"
            f"def test_path():\n    assert choose({flag}) == {expected}\n"
        )
    result = run(project, "--all", "--run-id", "system")
    assert result.returncode == 0, result.stdout + result.stderr
    directory = project / "coverage/system"
    summary = json.loads((directory / "summary.json").read_text())
    system = summary["levels"]["system"]
    assert system["source_levels"] == ["sit", "e2e"]
    assert system["totals"]["covered_lines"] == 4
    assert system["totals"]["num_statements"] == 7
    assert system["totals"]["covered_branches"] == 2
    assert system["totals"]["branch_percent"] == 100
    assert system["modules"]["unused"]["covered_lines"] == 0
    assert summary["levels"]["all"]["totals"]["covered_lines"] == 6
    for filename in ["coverage.json", "coverage.xml", "html/index.html", ".coverage"]:
        assert (directory / "system" / filename).is_file()
    assert "| system | TOTAL |" in (directory / "summary.md").read_text()


@pytest.mark.parametrize(("level",), [("ut",), ("sit",)])
def test_partial_selection_does_not_claim_system_coverage(
    project: Path, level: str
) -> None:
    result = run(project, "--level", level, "--run-id", "partial")
    assert result.returncode == 0, result.stdout + result.stderr
    directory = project / "coverage/partial"
    summary = json.loads((directory / "summary.json").read_text())
    assert "system" not in summary["levels"]
    assert "SYSTEM unavailable" in (directory / "summary.md").read_text()
