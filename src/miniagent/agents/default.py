# File   : default.py
# Created: 2026-09-09 17:51:53
# Author : octy Sui
import asyncio
import traceback
from pathlib import Path

from miniagent.config import Settings
from miniagent.models.model import Message, ModelRequest
from miniagent.models.tool import ToolError, ToolResult, ToolStatus
from miniagent.providers.qwen import QwenProvider
from miniagent.tools.read_file import ReadFileTool
from miniagent.tools.shell import ShellTool

SYSTEM_PROMPT = "You are a helpful assistant that can interact with a computer shell to solve programming tasks.\n"


async def run():
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

    provider = QwenProvider(api_key=api_key, base_url=base_url)
    while True:
        response = await provider.generate(
            ModelRequest(
                model=model,
                messages=messages,
                tools=[tool.definition for tool in tools.values()],
            )
        )
        # print(response)

        messages.append(response.message)

        if not response.message.tool_calls:
            print(response.message.content)
            return response.message.content

        for call in response.message.tool_calls:
            tool = tools.get(call.name)

            if tool is None:
                result = ToolResult(
                    tool_call_id=call.id,
                    status=ToolStatus.ERROR,
                    content=(f"Unknown tool: {call.name}"),
                    error=ToolError(
                        code="UNKNOWN_TOOL",
                        message=call.name,
                    ),
                )
            else:
                result = await tool.execute(
                    call.arguments,
                    tool_call_id=call.id,
                )

            messages.append(
                Message(
                    role="tool",
                    tool_call_id=call.id,
                    content=result.content,
                )
            )


async def main() -> None:
    await run()


if __name__ == "__main__":
    # main()
    setting = Settings()

    api_key = setting.openai_api_key
    base_url = setting.openai_base_url
    model = "qwen3.8-flash"
    task = "model qwen的providers实现路径是？"
    path = Path.cwd()
    tools = {"read_file": ReadFileTool(path), "shell": ShellTool(path)}
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("KeyboardInterrupt")
    except Exception as e:
        print(f"How exceptional! {e}")
        traceback.print_exc()
