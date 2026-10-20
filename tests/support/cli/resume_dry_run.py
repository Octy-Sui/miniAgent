import asyncio
import sys


async def invoke(database, run_id, *args, environment=None, cwd=None):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "miniagent.cli.main",
        "resume",
        "--run-id",
        str(run_id),
        "--database",
        str(database),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=environment,
        cwd=cwd,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, (stdout + stderr).decode()
