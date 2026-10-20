from uuid import uuid4

from miniagent.environments import (
    DockerOptions,
    DockerWorkspaceSpec,
)
from miniagent.environments.persistence import (
    persist_workspace_spec,
)


def test_persisted_workspace_reuses_normalized_runtime_options() -> None:
    options = DockerOptions(image="task-image", workdir="/testbed/.")
    runtime_spec = DockerWorkspaceSpec(run_id=uuid4(), options=options)

    persisted = persist_workspace_spec(runtime_spec)

    assert runtime_spec.options.workdir == "/testbed"
    assert persisted.options == runtime_spec.options
