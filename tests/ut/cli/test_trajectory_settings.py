import pytest

from miniagent.cli.settings import Settings


@pytest.mark.parametrize(
    ("value", "expected"), [(None, False), ("true", True), ("false", False)]
)
def test_export_settings(tmp_path, monkeypatch, value, expected):
    monkeypatch.delenv("EXPORT_TRAJECTORY", raising=False)
    if value is not None:
        monkeypatch.setenv("EXPORT_TRAJECTORY", value)
    assert (
        Settings(
            _env_file=None,
            openai_api_key="test",
            openai_base_url="https://example.test",
        ).export_trajectory
        is expected
    )


def test_dotenv_and_environment_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("EXPORT_TRAJECTORY=true\n")
    monkeypatch.delenv("EXPORT_TRAJECTORY", raising=False)
    assert (
        Settings(
            _env_file=env, openai_api_key="test", openai_base_url="https://example.test"
        ).export_trajectory
        is True
    )
    monkeypatch.setenv("EXPORT_TRAJECTORY", "false")
    assert (
        Settings(
            _env_file=env, openai_api_key="test", openai_base_url="https://example.test"
        ).export_trajectory
        is False
    )
