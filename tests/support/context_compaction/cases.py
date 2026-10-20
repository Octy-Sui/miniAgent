from tests.support.context_compaction.protocol import AcceptanceManifest, TaskOracle


def manifest(reviewer=None):
    return AcceptanceManifest(
        protocol_id="test-protocol",
        implementer="developer",
        reviewer=reviewer,
        model={"provider": "qwen", "model": "test"},
        runtime_config={"context_window_tokens": 12000},
        max_output_tokens=976,
        task_oracles=[
            TaskOracle(
                task_id="task",
                version="v1",
                group="cost",
                task="write result",
                source_dir="fixture",
                initial_workspace_revision="fixed",
                expected_files={"result.txt": "blue"},
                allowed_files=["result.txt"],
            )
        ],
        repetitions_per_arm=1,
        planned_runs=[
            {
                "run_id": "baseline-1",
                "task_id": "task",
                "arm": "baseline",
                "repetition": 1,
            },
            {
                "run_id": "compacted-1",
                "task_id": "task",
                "arm": "compacted",
                "repetition": 1,
            },
        ],
        pairing_order=["baseline-1", "compacted-1"],
    )
