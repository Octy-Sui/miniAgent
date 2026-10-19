import os

import pytest

from miniagent.cli.settings import load_provider_credentials


@pytest.mark.parametrize(("process_override",), [(False,), (True,)])
def test_provider_references_resolve_dotenv_with_process_precedence(
    tmp_path, monkeypatch, process_override
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CUSTOM_PROVIDER_KEY", raising=False)
    monkeypatch.delenv("CUSTOM_PROVIDER_ENDPOINT", raising=False)
    (tmp_path / ".env").write_text(
        'CUSTOM_PROVIDER_KEY="dotenv-secret"\n'
        "CUSTOM_PROVIDER_ENDPOINT=https://dotenv.example/v1\n"
        "CANDIDATE_SUBMISSION_TIMEOUT_SECONDS=invalid\n"
    )
    if process_override:
        monkeypatch.setenv("CUSTOM_PROVIDER_KEY", "process-secret")
        monkeypatch.setenv("CUSTOM_PROVIDER_ENDPOINT", "https://process.example/v1")
    before = dict(os.environ)
    credentials = load_provider_credentials(
        "CUSTOM_PROVIDER_KEY", "CUSTOM_PROVIDER_ENDPOINT"
    )
    assert credentials.api_key == (
        "process-secret" if process_override else "dotenv-secret"
    )
    assert credentials.endpoint == (
        "https://process.example/v1"
        if process_override
        else "https://dotenv.example/v1"
    )
    assert dict(os.environ) == before
