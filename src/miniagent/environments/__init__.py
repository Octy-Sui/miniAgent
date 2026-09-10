# File   : __init__.py
# Created: 2026-09-09 17:45:51
# Author : octy Sui
class Workspace:
    async def execute(
        self,
        command: str,
        timeout: float,
    ) -> CommandResult: ...
