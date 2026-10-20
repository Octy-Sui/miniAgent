from pathlib import Path

import pytest

from miniagent.environments.docker_connection import (
    DockerConnectionError,
    FrozenDockerConnection,
    bind_docker_connection,
    docker_argv,
    harness_environment,
)


def test_bound_connection_ignores_ambient_context(tmp_path: Path) -> None:
    """TC-U13-10/11: persisted endpoints win over the next process environment."""
    certs = tmp_path / "certs"
    certs.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (certs / name).write_text("secret-resume-fixture")
    frozen_a = FrozenDockerConnection(
        source_context="a",
        endpoint="unix:///private/tmp/a.sock",
        cert_path=None,
        tls_verify=False,
        daemon_id="daemon-a",
    )
    frozen_b = FrozenDockerConnection(
        source_context="b",
        endpoint="tcp://b:2376",
        cert_path=certs,
        tls_verify=True,
        daemon_id="daemon-b",
    )
    environment = {
        "DOCKER_CONTEXT": "c",
        "DOCKER_HOST": "tcp://c:2375",
        "DOCKER_TLS_VERIFY": "1",
        "DOCKER_CERT_PATH": "/missing",
        "PROVIDER_KEY": "secret-resume-fixture",
        "PATH": "/bin",
    }
    bound_a = bind_docker_connection(frozen_a, environment)
    bound_b = bind_docker_connection(frozen_b, environment)
    assert docker_argv(Path("/bin/docker"), bound_a, ["info"])[1:3] == [
        "--host",
        "unix:///private/tmp/a.sock",
    ]
    assert harness_environment(environment, bound_b)["DOCKER_HOST"] == "tcp://b:2376"
    assert "DOCKER_CONTEXT" not in harness_environment(environment, bound_b)
    assert "DOCKER_TLS_VERIFY" not in harness_environment(environment, bound_a)
    assert dict(bound_b.client_environment) == {"PATH": "/bin"}
    assert "secret-resume-fixture" not in frozen_b.model_dump_json()
    assert (
        FrozenDockerConnection.model_validate_json(frozen_b.model_dump_json())
        == frozen_b
    )


def test_bound_tls_reference_must_still_exist(tmp_path: Path) -> None:
    frozen = FrozenDockerConnection(
        source_context=None,
        endpoint="tcp://a:2376",
        cert_path=tmp_path,
        tls_verify=True,
        daemon_id="daemon-a",
    )
    with pytest.raises(DockerConnectionError, match="ENVIRONMENT_REFERENCE_MISSING"):
        bind_docker_connection(frozen, {})
