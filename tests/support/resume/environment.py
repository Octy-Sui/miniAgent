import asyncio
import json
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from time import get_clock_info, monotonic


def record_environment(output: Path) -> None:
    output.write_text(
        json.dumps(
            {
                "platform": platform.platform(),
                "system": platform.system(),
                "python": sys.version,
                "git": subprocess.check_output(["git", "--version"], text=True).strip(),
                "dependencies": {
                    name: version(name)
                    for name in ("pydantic", "psutil", "docker", "pytest")
                },
                "other_host_os": "DEFER: no second host OS was executed",
            },
            indent=2,
        )
    )


async def calibrate_timing(output: Path) -> dict:
    samples = []
    for _ in range(40):
        start = monotonic()
        await asyncio.sleep(0.002)
        samples.append(max(0, monotonic() - start - 0.002))
    epsilon = 0.25 + 10 * max(samples)
    evidence = {
        "calibrated_at_monotonic": monotonic(),
        "samples_seconds": samples,
        "clock_resolution": get_clock_info("monotonic").resolution,
        "epsilon_seconds": epsilon,
        "supervision_seconds": 35 + epsilon,
        "formula": "epsilon=0.25+10*max(40 scheduling overshoot samples); fixed before product measurement",
    }
    output.write_text(json.dumps(evidence, indent=2))
    return evidence


async def record_docker_environment(output: Path, connection, image: str) -> None:
    process = await asyncio.create_subprocess_exec(
        "docker",
        "--host",
        connection.endpoint,
        "version",
        "--format",
        "{{json .Server}}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(stderr.decode())
    output.write_text(
        json.dumps(
            {
                "host": platform.platform(),
                "daemon_id": connection.daemon_id,
                "endpoint": connection.endpoint,
                "server": json.loads(stdout),
                "sdk_version": version("docker"),
                "prepared_image": image,
                "pull_policy": "never",
                "second_daemon": "DEFER",
                "verified_tls": "DEFER",
            },
            indent=2,
        )
    )
