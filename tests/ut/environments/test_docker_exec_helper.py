from time import monotonic

import pytest

from miniagent.environments.contracts import Command
from miniagent.environments.docker_connection import DockerConnection
from miniagent.environments.docker_exec import run_docker_exec
from miniagent.environments.managed_execution import (
    ManagedExecutionError,
)
from tests.support.environments.docker_exec_helper import Observer


async def test_expired_command_budget_issues_no_permission():
    observer = Observer(None)
    connection = DockerConnection(None, "tcp://127.0.0.1:1", None, False, "daemon-a")
    with pytest.raises(ManagedExecutionError, match="deadline"):
        await run_docker_exec(
            Command(argv=["true"]),
            connection=connection,
            container_id="a" * 64,
            observer=observer,
            attempt_id=observer.attempt_id,
            deadline=monotonic() - 1,
        )
    assert observer.facts == []
