import subprocess
from pathlib import Path

import pytest

from miniagent.models.messages import AssistantMessage
from miniagent.models.protocol import (
    FinishReason,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


def create_source_repository(path: Path) -> Path:
    path.mkdir()
    subprocess.run(
        ["git", "-C", str(path), "init", "--quiet"],
        check=True,
        capture_output=True,
    )
    (path / "source.txt").write_text("source\n", encoding="utf-8")
    return path


class StaticProvider(ModelProvider):
    def __init__(self, finish_reason: FinishReason) -> None:
        self.finish_reason = finish_reason

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            message=AssistantMessage(content="finished"),
            finish_reason=self.finish_reason,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def configure_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com")
    monkeypatch.setenv("LOCAL_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("RUN_DATABASE_PATH", str(tmp_path / "runs.sqlite3"))
    monkeypatch.setenv(
        "MODEL_PRICING_FILE",
        str(Path.cwd() / "config" / "model_pricing.toml"),
    )
