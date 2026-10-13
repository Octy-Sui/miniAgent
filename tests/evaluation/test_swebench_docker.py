import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from miniagent.evaluation.swebench_data import EvaluationPreparationError
from miniagent.evaluation.swebench_docker import (
    check_daemon,
    docker_argv,
    harness_environment,
    inspect_image,
    resolve_docker_connection,
    verify_harness_version,
)


def change_scenario(executable, **updates):
    path = executable.parent / "docker-scenario.json"
    scenario = json.loads(path.read_bytes())
    scenario.update(updates)
    path.write_text(json.dumps(scenario))


async def test_context_wins_and_binding_survives_environment_change(docker_cli):
    environment = {
        "DOCKER_CONTEXT": "a",
        "DOCKER_HOST": "tcp://b:2375",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/other",
    }
    connection = await resolve_docker_connection(docker_cli, environment)
    environment["DOCKER_CONTEXT"] = "b"
    assert connection.endpoint == "unix:///private/tmp/daemon-a.sock"
    frozen_env = harness_environment(environment, connection)
    assert frozen_env == {"DOCKER_HOST": "unix:///private/tmp/daemon-a.sock"}
    assert docker_argv(docker_cli, connection, ["info"])[1:3] == [
        "--host",
        connection.endpoint,
    ]
    await check_daemon(docker_cli, connection)
    calls = [
        json.loads(line)
        for line in (docker_cli.parent / "docker-calls.jsonl").read_text().splitlines()
    ]
    assert calls[-1]["argv"][1] == connection.endpoint
    assert "DOCKER_CONTEXT" not in calls[-1]["environment"]
    assert environment["DOCKER_CONTEXT"] == "b"


async def test_host_wins_over_current_context(docker_cli):
    connection = await resolve_docker_connection(
        docker_cli, {"DOCKER_HOST": "tcp://a:2375"}
    )
    assert connection.endpoint == "tcp://a:2375"
    assert connection.source_context is None


async def test_current_context_is_resolved_when_no_host(docker_cli):
    connection = await resolve_docker_connection(docker_cli, {})
    assert connection.source_context == "a"
    assert connection.daemon_id == "daemon-a"


async def test_verified_tls_uses_matching_cli_and_sdk_parameters(docker_cli, tmp_path):
    certs = tmp_path / "certs"
    certs.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (certs / name).write_text("synthetic material")
    environment = {
        "DOCKER_HOST": "tcp://a:2376",
        "DOCKER_CERT_PATH": str(certs),
        "DOCKER_TLS_VERIFY": "1",
    }
    before = environment.copy()
    connection = await resolve_docker_connection(docker_cli, environment)
    assert connection.tls_verify is True
    args = docker_argv(docker_cli, connection, ["info"])
    assert "--tlsverify" in args
    assert args[args.index("--tlskey") + 1] == str(certs / "key.pem")
    assert harness_environment(environment, connection)["DOCKER_CERT_PATH"] == str(
        certs
    )
    assert environment == before


async def test_context_tls_material_is_bound_without_environment_override(
    docker_cli, tmp_path
):
    certs = tmp_path / "context-tls" / "docker"
    certs.mkdir(parents=True)
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (certs / name).write_text("synthetic")
    change_scenario(
        docker_cli,
        contexts={
            "a": {
                "Endpoints": {
                    "docker": {"Host": "tcp://a:2376", "SkipTLSVerify": False}
                },
                "TLSMaterial": {"docker": ["ca.pem", "cert.pem", "key.pem"]},
                "Storage": {"TLSPath": str(certs.parent)},
            }
        },
    )
    connection = await resolve_docker_connection(
        docker_cli, {"DOCKER_CONTEXT": "a", "DOCKER_HOST": "tcp://b:2375"}
    )
    assert connection.cert_path == certs
    assert connection.endpoint == "tcp://a:2376"


@pytest.mark.parametrize(
    ("environment", "code"),
    [
        ({"DOCKER_HOST": "ssh://host"}, "DOCKER_CONNECTION_UNSUPPORTED"),
        ({"DOCKER_HOST": "npipe://host"}, "DOCKER_CONNECTION_UNSUPPORTED"),
        (
            {"DOCKER_HOST": "tcp://a:2376", "DOCKER_TLS": "1"},
            "DOCKER_CONNECTION_UNSUPPORTED",
        ),
        (
            {
                "DOCKER_HOST": "tcp://a:2376",
                "DOCKER_TLS_VERIFY": "1",
                "DOCKER_CERT_PATH": "/missing",
            },
            "DOCKER_CONNECTION_INVALID",
        ),
        (
            {"DOCKER_HOST": "tcp://a:2376", "DOCKER_CERT_PATH": "/missing"},
            "DOCKER_CONNECTION_UNSUPPORTED",
        ),
    ],
)
async def test_unsupported_or_invalid_connection_never_falls_back(
    docker_cli, environment, code
):
    with pytest.raises(EvaluationPreparationError, match=code):
        await resolve_docker_connection(docker_cli, environment)
    assert not (docker_cli.parent / "docker-calls.jsonl").exists()


async def test_skip_tls_verification_is_rejected(docker_cli):
    change_scenario(
        docker_cli,
        contexts={
            "a": {
                "Endpoints": {
                    "docker": {"Host": "tcp://a:2376", "SkipTLSVerify": True}
                },
                "TLSMaterial": {},
                "Storage": {"TLSPath": ""},
            }
        },
    )
    with pytest.raises(
        EvaluationPreparationError, match="DOCKER_CONNECTION_UNSUPPORTED"
    ):
        await resolve_docker_connection(docker_cli, {"DOCKER_CONTEXT": "a"})


async def test_daemon_identity_change_is_detected(docker_cli):
    connection = await resolve_docker_connection(docker_cli, {})
    change_scenario(docker_cli, daemon_id="daemon-b")
    with pytest.raises(EvaluationPreparationError, match="DOCKER_DAEMON_CHANGED"):
        await check_daemon(docker_cli, connection)


async def test_missing_daemon_id_is_invalid(docker_cli):
    change_scenario(docker_cli, daemon_id="")
    with pytest.raises(EvaluationPreparationError, match="DOCKER_CONNECTION_INVALID"):
        await resolve_docker_connection(docker_cli, {})


async def test_missing_image_is_distinct_from_connection_failure(docker_cli):
    connection = await resolve_docker_connection(docker_cli, {})
    assert await inspect_image(docker_cli, connection, "cached") == "sha256:synthetic"
    change_scenario(docker_cli, image_missing=True)
    assert await inspect_image(docker_cli, connection, "missing") is None


async def test_docker_timeout_is_bounded_and_process_is_reaped(docker_cli):
    connection = await resolve_docker_connection(docker_cli, {})
    change_scenario(docker_cli, hang=True)
    with pytest.raises(EvaluationPreparationError, match="DOCKER_CONNECTION_INVALID"):
        await check_daemon(docker_cli, connection, timeout_seconds=0.3)
    pid = int((docker_cli.parent / "docker-started").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize(
    ("version", "valid"), [("swebench 5.0.2", True), ("swebench 5.0.3", False)]
)
async def test_harness_version_is_checked(tmp_path, version, valid):
    executable = tmp_path / "swebench"
    executable.write_text(f"#!{sys.executable}\nprint({version!r})\n")
    executable.chmod(0o755)
    if valid:
        assert await verify_harness_version(executable) == "5.0.2"
    else:
        with pytest.raises(
            EvaluationPreparationError, match="HARNESS_VERSION_MISMATCH"
        ):
            await verify_harness_version(executable)


async def test_uv_tool_without_version_option_uses_bound_distribution_metadata(
    tmp_path,
):
    environment = tmp_path / "tool-env"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "venv", "--without-pip", str(environment)
    )
    assert await process.wait() == 0
    python = environment / "bin/python"
    query = await asyncio.create_subprocess_exec(
        str(python),
        "-c",
        "import sysconfig; print(sysconfig.get_path('purelib'))",
        stdout=asyncio.subprocess.PIPE,
    )
    output, _ = await query.communicate()
    assert query.returncode == 0
    distribution = Path(output.decode().strip()) / "swebench-5.0.2.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: swebench\nVersion: 5.0.2\n"
    )
    executable = environment / "bin/swebench"
    executable.write_text(
        f"#!{python}\nimport sys\nprint('No such option: --version', file=sys.stderr)\nsys.exit(2)\n"
    )
    executable.chmod(0o755)
    assert await verify_harness_version(executable) == "5.0.2"
