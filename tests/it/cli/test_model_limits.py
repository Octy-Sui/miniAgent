from pathlib import Path

import pytest

from miniagent.cli.model_limits import ModelLimits, load_model_limits
from tests.support.cli.agent import DEFAULT_AGENT


def test_default_qwen_limits() -> None:
    assert load_model_limits(
        Path("config/model_limits.toml"), "qwen", "qwen3.8-flash"
    ) == ModelLimits(context_window_tokens=32768, max_output_tokens=4096)


def test_selected_model_and_duplicate_or_missing_entries(tmp_path: Path) -> None:
    path = tmp_path / "limits.toml"
    row = '[[models]]\nprovider="qwen"\nmodel="custom"\ncontext_window_tokens=12000\nmax_output_tokens=976\n'
    path.write_text(row)
    assert load_model_limits(path, "qwen", "custom").max_output_tokens == 976
    with pytest.raises(ValueError, match="missing"):
        load_model_limits(path, "qwen", "absent")
    path.write_text(row + row)
    with pytest.raises(ValueError, match="duplicate"):
        load_model_limits(path, "qwen", "custom")


async def test_invalid_limits_fail_before_workspace_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from miniagent.cli.bootstrap import BootstrapConfig, bootstrap

    path = tmp_path / "limits.toml"
    path.write_text(
        '[[models]]\nprovider="qwen"\nmodel="qwen3.8-flash"\ncontext_window_tokens=12000\nmax_output_tokens=10976\n'
    )
    monkeypatch.setenv("MODEL_LIMITS_FILE", str(path))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com")
    monkeypatch.setenv("LOCAL_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    with pytest.raises(ValueError, match="business input budget"):
        await bootstrap(
            BootstrapConfig(
                agent=DEFAULT_AGENT, task="task", source_dir=tmp_path / "absent"
            )
        )
    assert not (tmp_path / "workspaces").exists()
