from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from miniagent.cli.run_definition import (
    ProviderReference,
)
from miniagent.cli.settings import RunLocationSettings
from miniagent.environments.attachment import LocalAttachment
from miniagent.environments.persistence import EnvironmentReference


def test_record_location_does_not_require_provider_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RUN_DATABASE_PATH", "relative/runs.sqlite3")
    assert (
        RunLocationSettings(_env_file=None).run_database_path
        == Path("relative/runs.sqlite3").resolve()
    )


def test_attachment_requires_absolute_original_paths(tmp_path):
    with pytest.raises(ValidationError, match="absolute"):
        LocalAttachment(
            run_id=uuid4(),
            workspace_id="workspace",
            base_revision="base",
            root=Path("relative"),
            workspace_dir=tmp_path,
            repository_root=tmp_path,
            marker_run_id=uuid4(),
            marker_workspace_id="workspace",
        )


@pytest.mark.parametrize(
    ("endpoint",),
    [
        ("https://user:secret-resume-fixture@example.test/v1",),
        ("https://example.test/v1?api_key=secret-resume-fixture",),
    ],
)
def test_provider_identity_cannot_persist_credentials(endpoint):
    with pytest.raises(ValidationError, match="endpoint_identity"):
        ProviderReference(
            provider="qwen",
            model="model",
            protocol_version="qwen-chat-completions-v1",
            endpoint_identity=endpoint,
            api_key_ref=EnvironmentReference(key="OPENAI_API_KEY"),
            endpoint_ref=EnvironmentReference(key="OPENAI_BASE_URL"),
        )
