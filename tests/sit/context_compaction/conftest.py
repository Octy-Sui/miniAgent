import subprocess

import pytest


@pytest.fixture
def prepared_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    (source / "source.txt").write_text("source\n")
    subprocess.run(["git", "-C", str(source), "add", "source.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
    )
    initial = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    return source, initial, revision
