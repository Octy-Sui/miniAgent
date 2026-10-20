import argparse
from pathlib import Path

import uvicorn

from miniagent.dashboard.app import create_app
from miniagent.dashboard.config import DashboardSettings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(
        create_app(
            DashboardSettings(
                database=args.database, artifacts=args.artifacts, port=args.port
            )
        ),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="error",
    )


if __name__ == "__main__":
    main()
