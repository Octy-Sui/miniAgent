import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner


def test_cli_does_not_read_provider_settings(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'import sys; from miniagent.dashboard.cli import app; assert "miniagent.cli.bootstrap" not in sys.modules; assert "miniagent.cli.settings" not in sys.modules',
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    from miniagent.dashboard.cli import app

    help_result = CliRunner().invoke(app, ["--help"])
    assert help_result.exit_code == 0
    for option in ("--database", "--artifacts", "--port"):
        assert option in help_result.output
    for option in ("--host", "--workers", "--experiments", "--install-completion"):
        assert option not in help_result.output


def test_invalid_port_exits_without_creating_files(tmp_path: Path) -> None:
    from miniagent.dashboard.cli import app

    result = CliRunner().invoke(
        app, ["--database", str(tmp_path / "missing.sqlite3"), "--port", "0"]
    )
    assert result.exit_code != 0
    assert not list(tmp_path.iterdir())


def test_missing_database_does_not_initialize_storage(tmp_path: Path) -> None:
    from miniagent.dashboard.cli import app

    result = CliRunner().invoke(
        app,
        [
            "--database",
            str(tmp_path / "missing.sqlite3"),
            "--artifacts",
            str(tmp_path / "artifacts"),
        ],
    )
    assert result.exit_code == 1
    assert "数据库不可用" in result.output
    assert "Traceback" not in result.output
    assert not list(tmp_path.iterdir())
