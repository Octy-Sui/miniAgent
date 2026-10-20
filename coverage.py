"""Run layered coverage: uv run python coverage.py run --all (or --level ut)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

# This requested filename also names the installed library. Forward library imports
# so `python -m pytest` and pytest-cov remain usable from the repository root.
if __name__ == "coverage":
    search = [
        p for p in sys.path if Path(p).resolve() != Path(__file__).parent.resolve()
    ]
    spec = importlib.machinery.PathFinder.find_spec("coverage", search)
    if spec is None or spec.loader is None:
        raise ImportError("Install development dependencies with uv sync --group dev")
    library = importlib.util.module_from_spec(spec)
    sys.modules["coverage"] = library
    spec.loader.exec_module(library)

import configparser
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from enum import StrEnum
from importlib.metadata import version
from typing import Annotated, Any
from uuid import uuid4

import typer

Coverage = importlib.import_module("coverage").Coverage
COVERAGE_VERSION = version("coverage")

ROOT = Path(__file__).resolve().parent
app = typer.Typer(no_args_is_help=True, help=__doc__)


class Level(StrEnum):
    ut = "ut"
    it = "it"
    sit = "sit"
    e2e = "e2e"


def write_config(path: Path) -> None:
    config = configparser.ConfigParser(interpolation=None)
    config["run"] = {
        "branch": "true",
        "parallel": "true",
        "concurrency": "multiprocessing",
        "patch": "subprocess",
        "sigterm": "true",
        "include": "\n".join(
            [
                str(ROOT / "src/miniagent/*"),
                "*/site-packages/miniagent/*",
                str(ROOT / "trigger"),
            ]
        ),
    }
    config["paths"] = {
        "miniagent": "\n".join(
            [str(ROOT / "src/miniagent"), "*/site-packages/miniagent"]
        )
    }
    with path.open("w") as stream:
        config.write(stream)


def metrics(counts: dict[str, Any]) -> dict[str, int | float | None]:
    lines = counts["num_statements"]
    branches = counts["num_branches"]
    return {
        "covered_lines": counts["covered_lines"],
        "num_statements": lines,
        "line_percent": 100 * counts["covered_lines"] / lines if lines else None,
        "covered_branches": counts["covered_branches"],
        "num_branches": branches,
        "branch_percent": 100 * counts["covered_branches"] / branches
        if branches
        else None,
    }


def report(
    data: Path, config: Path, sources: list[Path], output: Path
) -> dict[str, Any]:
    collector = Coverage(
        data_file=str(data), config_file=str(config), data_suffix=False
    )
    collector.load()
    # Paths were normalized during combine; remapping empty data can discard branch mode.
    collector.set_option("paths", {})
    # Include never-imported modules and the extensionless production trigger script.
    collector.get_data().add_arcs({})
    collector.get_data().touch_files([str(path) for path in sources])
    collector.save()
    collector.json_report(outfile=str(output / "coverage.json"))
    collector.xml_report(outfile=str(output / "coverage.xml"))
    collector.html_report(directory=str(output / "html"))
    result = json.loads((output / "coverage.json").read_text())
    modules: dict[str, dict[str, int]] = {}
    for filename, item in result["files"].items():
        path = Path(filename)
        if not path.is_absolute():
            path = Path.cwd() / path
        if path == ROOT / "trigger":
            name = "trigger"
        else:
            relative = path.relative_to(ROOT / "src/miniagent")
            name = relative.parts[0].removesuffix(".py")
        counts = modules.setdefault(
            name,
            {
                "covered_lines": 0,
                "num_statements": 0,
                "covered_branches": 0,
                "num_branches": 0,
            },
        )
        for key in counts:
            counts[key] += item["summary"][key]
    return {
        "totals": metrics(result["totals"]),
        "modules": {name: metrics(counts) for name, counts in sorted(modules.items())},
    }


def test_counts(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    suites = ET.parse(path).getroot().iter("testsuite")
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in counts:
            counts[key] += int(suite.get(key, "0"))
    counts["passed"] = counts["tests"] - sum(
        counts[key] for key in ("failures", "errors", "skipped")
    )
    return counts


def merge_report(
    directory: Path, config: Path, sources: list[Path], name: str, levels: list[str]
) -> dict[str, Any]:
    output = directory / name
    output.mkdir()
    data = output / ".coverage"
    collector = Coverage(
        data_file=str(data), config_file=str(config), data_suffix=False
    )
    collector.combine(
        data_paths=[str(directory / level / ".coverage") for level in levels],
        keep=True,
        strict=True,
    )
    collector.save()
    return {**report(data, config, sources, output), "source_levels": levels}


def write_summary(directory: Path, summary: dict[str, Any]) -> None:
    (directory / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n"
    )
    rows = [
        f"# Coverage {directory.name}",
        "",
        f"Selected levels: {', '.join(summary['selected_levels'])}",
        "",
        "ALL is the union of selected levels; percentages are not averaged.",
        "",
        "SYSTEM is the union of SIT and E2E only; UT and IT are excluded."
        if "system" in summary["levels"]
        else "SYSTEM unavailable: requires completed SIT and E2E runs.",
        "",
        "## Reports",
        "",
        "[Summary JSON](summary.json)",
        "",
        "HTML shows source lines and missing branches; JSON/XML provide machine-readable details.",
        "",
        "| Level | HTML | JSON | XML | Raw data | Test results |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for level in summary["levels"]:
        links = [
            f"[{label}]({level}/{filename})"
            if (directory / level / filename).is_file()
            else "—"
            for label, filename in [
                ("index.html", "html/index.html"),
                ("coverage.json", "coverage.json"),
                ("coverage.xml", "coverage.xml"),
                (".coverage", ".coverage"),
                ("junit.xml", "junit.xml"),
            ]
        ]
        rows.append(f"| {level} | {' | '.join(links)} |")
    rows.extend(
        [
            "",
            "## Coverage by module",
            "",
            "| Level | Module | Lines | Line % | Branches | Branch % |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for level, result in summary["levels"].items():
        for module, counts in [("TOTAL", result["totals"]), *result["modules"].items()]:
            line = counts["line_percent"]
            branch = counts["branch_percent"]
            rows.append(
                f"| {level} | {module} | {counts['covered_lines']}/{counts['num_statements']} | "
                f"{f'{line:.2f}' if line is not None else 'N/A'} | "
                f"{counts['covered_branches']}/{counts['num_branches']} | "
                f"{f'{branch:.2f}' if branch is not None else 'N/A'} |"
            )
    rows.extend(["", "## Execution", ""])
    for level, result in summary["levels"].items():
        if "pytest_exit_code" in result:
            rows.append(
                f"- {level}: exit={result['pytest_exit_code']}; tests={result['tests']}"
            )
    rows.extend(["", "## Measurement limitations", ""])
    rows.extend(f"- {item}" for item in summary["limitations"])
    (directory / "summary.md").write_text("\n".join(rows) + "\n")


@app.command()
def run(
    level: Annotated[
        list[Level] | None, typer.Option(help="Repeat to select levels.")
    ] = None,
    all_levels: Annotated[
        bool, typer.Option("--all", help="Run all four levels.")
    ] = False,
    run_id: Annotated[
        str | None, typer.Option(help="Unique output directory name.")
    ] = None,
) -> None:
    """Run selected suites and write per-level and union HTML/JSON/XML reports."""
    if all_levels and level:
        raise typer.BadParameter("Use --all or --level, not both")
    selected = list(dict.fromkeys(level or list(Level)))
    identifier = run_id or (
        datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identifier):
        raise typer.BadParameter("run-id must contain 1–80 letters, digits, '_' or '-'")
    for item in selected:
        if not (ROOT / "tests" / item).is_dir():
            raise typer.BadParameter(f"Missing tests/{item}")
    sources = sorted((ROOT / "src/miniagent").rglob("*.py"))
    if not sources:
        raise typer.BadParameter("No production sources in src/miniagent")
    if (ROOT / "trigger").is_file():
        sources.append(ROOT / "trigger")
    directory = ROOT / "coverage" / identifier
    if directory.exists():
        raise typer.BadParameter(f"Run already exists: {directory}")
    directory.mkdir(parents=True, exist_ok=False)
    config = directory / ".coveragerc"
    write_config(config)
    digest = hashlib.sha256()
    for source in sources:
        digest.update(str(source.relative_to(ROOT)).encode() + b"\0")
        digest.update(source.read_bytes() + b"\0")
    summary: dict[str, Any] = {
        "run_id": identifier,
        "selected_levels": selected,
        "source_sha256": digest.hexdigest(),
        "python": sys.version,
        "coverage_version": COVERAGE_VERSION,
        "scope": ["src/miniagent/**/*.py", "trigger"],
        "levels": {},
        "limitations": [
            "Coverage includes setup, test execution and teardown; it does not measure assertion quality.",
            "SIGKILL and abrupt process exits can lose coverage data.",
            "Container-side Python and frontend JavaScript are not instrumented by this runner.",
            "Skipped tests contribute no execution evidence; ALL covers only the selected levels.",
        ],
    }
    exit_code = 0
    for item in selected:
        output = directory / item
        output.mkdir()
        data = output / ".coverage"
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("COVERAGE_", "COV_CORE_", "MINIAGENT_COVERAGE_"))
        }
        environment.update(
            COVERAGE_FILE=str(data),
            COVERAGE_RCFILE=str(config),
            MINIAGENT_COVERAGE_CONFIG=str(config),
            MINIAGENT_COVERAGE_VERSION=COVERAGE_VERSION,
        )
        typer.echo(f"[{item}] reports: {output}")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "pytest_cov",
                str(ROOT / "tests" / item),
                "--import-mode=importlib",
                "--cov",
                f"--cov-config={config}",
                "--cov-report=",
                f"--junitxml={output / 'junit.xml'}",
                "-q",
            ],
            cwd=ROOT,
            env=environment,
            check=False,
        )
        exit_code = exit_code or completed.returncode
        collector = Coverage(
            data_file=str(data), config_file=str(config), data_suffix=False
        )
        collector.load()
        collector.combine(data_paths=[str(output)], keep=True, strict=False)
        collector.save()
        result = report(data, config, sources, output)
        result.update(
            pytest_exit_code=completed.returncode,
            tests=test_counts(output / "junit.xml"),
        )
        summary["levels"][item] = result
        write_summary(directory, summary)
    if Level.sit in selected and Level.e2e in selected:
        summary["levels"]["system"] = merge_report(
            directory, config, sources, "system", ["sit", "e2e"]
        )
    summary["levels"]["all"] = merge_report(
        directory, config, sources, "all", [item.value for item in selected]
    )
    write_summary(directory, summary)
    typer.echo(f"Summary: {directory / 'summary.md'}")
    raise typer.Exit(exit_code if exit_code >= 0 else 128 - exit_code)


@app.callback()
def main() -> None:
    """Measure test layers independently and combine their executed lines and branches."""


if __name__ == "__main__":
    app()
