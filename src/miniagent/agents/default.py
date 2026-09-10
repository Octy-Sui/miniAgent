# File   : default.py
# Created: 2026-09-09 17:51:53
# Author : octy Sui
import asyncio
import traceback
from pathlib import Path

from miniagent.config import Settings
from miniagent.models.model import Message
from miniagent.providers.qwen import QwenProvider
from miniagent.runtime import AgentRuntime, RunResult, RunStatus
from miniagent.tools.read_file import ReadFileTool
from miniagent.tools.shell import ShellTool

SYSTEM_PROMPT = (
    "You are a helpful assistant that can interact with a computer shell "
    "to solve programming tasks.\n"
)
SAMPLE_TASK = "model qwen的providers实现路径是？"


async def run(task: str) -> RunResult:
    setting = Settings()
    workspace = Path.cwd()
    tools = {
        "read_file": ReadFileTool(workspace),
        "shell": ShellTool(workspace),
    }
    messages = [
        Message(
            role="system",
            content=SYSTEM_PROMPT,
        ),
        Message(
            role="user",
            content=task,
        ),
    ]

    runtime = AgentRuntime(
        provider=QwenProvider(
            api_key=setting.openai_api_key,
            base_url=setting.openai_base_url,
        ),
        model="qwen3.8-flash",
        tools=tools,
    )
    return await runtime.run(messages)


async def main() -> None:
    result = await run(SAMPLE_TASK)
    if result.status is RunStatus.COMPLETED:
        print(result.output)
    else:
        print(f"Agent stopped: {result.reason}")
        if result.error is not None:
            print(result.error.message)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("KeyboardInterrupt")
    except Exception as e:
        print(f"How exceptional! {e}")
        traceback.print_exc()
        raise
