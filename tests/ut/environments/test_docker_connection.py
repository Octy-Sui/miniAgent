from pathlib import Path

import pytest
from pydantic import ValidationError

from miniagent.environments.docker_connection import (
    FrozenDockerConnection,
)


@pytest.mark.parametrize(
    ("endpoint", "tls", "cert_path"),
    [
        ("ssh://host", False, None),
        ("unix:///tmp/a.sock", True, Path("/certs")),
        ("tcp://a:2376", True, None),
        ("tcp://a:2376", True, Path("relative")),
        ("tcp://a", False, None),
    ],
)
def test_invalid_frozen_connection_is_rejected(endpoint, tls, cert_path):
    with pytest.raises(ValidationError, match="connection|cert_path|endpoint"):
        FrozenDockerConnection(
            source_context=None,
            endpoint=endpoint,
            cert_path=cert_path,
            tls_verify=tls,
            daemon_id="daemon-a",
        )
