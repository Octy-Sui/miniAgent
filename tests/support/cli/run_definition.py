from pathlib import Path
from uuid import uuid4

from miniagent.agents.default_agent import DEFAULT_AGENT
from miniagent.cli.model_limits import ModelLimits
from miniagent.cli.run_definition import (
    CLIApplicationDefinition,
    ProviderReference,
    SWEbenchDefinition,
)
from miniagent.environments.persistence import EnvironmentReference
from miniagent.metering.pricing import ModelPrice, PriceConfig
from miniagent.policy import digest_json
from miniagent.runtime.config import RuntimeConfig
from miniagent.runtime.state import create_run_state


def definition():
    return create_run_state(
        run_id=uuid4(), task="task", agent_spec=DEFAULT_AGENT, config=RuntimeConfig()
    ).definition


def frozen_application(tmp_path: Path, evaluator):
    target = DEFAULT_AGENT.model
    pricing = PriceConfig(
        models=[
            ModelPrice(
                provider=target.provider,
                model=target.model,
                input_per_token="0.000001",
                output_per_token="0.000002",
            )
        ]
    )
    return CLIApplicationDefinition(
        pricing=pricing,
        pricing_sha256=digest_json(pricing.model_dump(mode="json")),
        tool_contract_sha256=digest_json({"definitions": [], "metadata": []}),
        model_limits=ModelLimits(context_window_tokens=32768, max_output_tokens=4096),
        provider=ProviderReference(
            provider=target.provider,
            model=target.model,
            protocol_version="qwen-chat-completions-v1",
            endpoint_identity="https://example.test/v1",
            api_key_ref=EnvironmentReference(key="OPENAI_API_KEY"),
            endpoint_ref=EnvironmentReference(key="OPENAI_BASE_URL"),
        ),
        artifact_root=tmp_path / "artifacts",
        model_limits_file=tmp_path / "limits.toml",
        workspace_connection=evaluator.evaluation_connection
        if isinstance(evaluator, SWEbenchDefinition)
        else None,
        evaluator=evaluator,
        candidate_submission_timeout_seconds=60,
        patch_export_cleanup_timeout_seconds=5,
        artifact_collection_timeout_seconds=30,
    )
