from miniagent.cli.bootstrap import BootstrapConfig, bootstrap
from miniagent.models.messages import SystemMessage, UserMessage
from miniagent.runtime.execution import RunExecution
from miniagent.runtime.journal import RunJournal
from miniagent.runtime.recording import RunRecorder
from miniagent.runtime.state import create_run_state
from tests.support.cli.agent import DEFAULT_AGENT
from tests.support.runtime.runtime import QueueProvider, final_response
from tests.support.trajectory.fixtures import request_for


async def retained(configured_local, monkeypatch, *, old_contract=False):
    from miniagent.cli import bootstrap as module
    from miniagent.models.protocol import ModelUsage

    provider = QueueProvider(
        [
            final_response("done").model_copy(
                update={
                    "usage": ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2)
                }
            )
        ]
    )
    monkeypatch.setattr(module, "build_provider", lambda agent, settings: provider)
    application = await bootstrap(
        BootstrapConfig(agent=DEFAULT_AGENT, task="task", source_dir=configured_local)
    )
    deps = application.dependencies
    state = create_run_state(
        run_id=deps.run_id,
        task=deps.task,
        agent_spec=deps.agent,
        config=deps.config,
        messages=(
            SystemMessage(content=deps.agent.system_prompt),
            UserMessage(content=deps.task),
        ),
    )
    state = state.model_copy(
        update={
            "definition": state.definition.model_copy(
                update={
                    "tool_definitions": tuple(
                        deps.tools.definitions(deps.agent.allowed_tools)
                    ),
                    "tool_contract_metadata": tuple(
                        deps.tools.contract_metadata(name)
                        for name in deps.agent.allowed_tools
                    ),
                }
            )
        }
    )
    context = deps.recovery_context
    if old_contract:
        assert context.application is not None
        from miniagent.cli.run_definition import freeze_application_definition
        from miniagent.policy import digest_json

        metadata = tuple(
            dict(item, version="older")
            for item in state.definition.tool_contract_metadata
        )
        definition = state.definition.model_copy(
            update={"tool_contract_metadata": metadata}
        )
        state = state.model_copy(update={"definition": definition})
        from miniagent.cli.run_definition import CLIApplicationDefinition

        application_definition = CLIApplicationDefinition.model_validate(
            context.application.payload
        )
        application_definition = application_definition.model_copy(
            update={
                "tool_contract_sha256": digest_json(
                    {
                        "definitions": [
                            item.model_dump(mode="json")
                            for item in definition.tool_definitions
                        ],
                        "metadata": list(metadata),
                    }
                )
            }
        )
        context = context.model_copy(
            update={
                "application": freeze_application_definition(
                    application_definition, definition
                )
            }
        )
    execution = await RunExecution.start(
        state,
        RunJournal(
            RunRecorder(
                run_id=deps.run_id,
                store=deps.run_store,
                recovery_context=context,
            )
        ),
        artifact_store=deps.artifact_store,
    )
    await execution.begin_step(
        provider=deps.agent.model.provider,
        model=deps.agent.model.model,
        request=request_for(execution.state),
    )
    return application, execution, provider
