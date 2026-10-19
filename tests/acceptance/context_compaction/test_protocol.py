import os
import sys
from pathlib import Path

import pytest

from .protocol import (
    AcceptanceManifest,
    TaskOracle,
    cost_acceptance_result,
    freeze_manifest,
    load_frozen_manifest,
    score_workspace,
    write_acceptance_report,
)


@pytest.mark.parametrize(
    ("savings", "accepted"), [(19.9, False), (20.0, True), (20.1, True)]
)
def test_cost_threshold(savings, accepted) -> None:
    assert (
        cost_acceptance_result(
            baseline=1000,
            compacted=1000 * (1 - savings / 100),
            baseline_successes=3,
            compacted_successes=3,
        )
        is accepted
    )


def test_lower_success_count_or_unknown_usage_not_accepted() -> None:
    assert not cost_acceptance_result(
        baseline=1000, compacted=100, baseline_successes=3, compacted_successes=2
    )
    assert not cost_acceptance_result(
        baseline=None, compacted=100, baseline_successes=3, compacted_successes=3
    )


@pytest.mark.parametrize(("group",), [("capacity",), ("cost",), ("safety",)])
def test_each_workspace_oracle_passes_and_fails_known_states(tmp_path, group) -> None:
    oracle = TaskOracle(
        task_id="task",
        version="v1",
        group=group,
        task="write result",
        initial_workspace_revision="fixed",
        source_dir="fixture",
        expected_files={"result.txt": "blue"},
        allowed_files=["result.txt"],
        forbidden_files=["override.txt"],
    )
    (tmp_path / "result.txt").write_text("blue")
    assert score_workspace(oracle, tmp_path, compacted_source_step=True).passed
    (tmp_path / "result.txt").write_text("red")
    assert not score_workspace(oracle, tmp_path, compacted_source_step=True).passed


@pytest.mark.parametrize(
    ("breakage",), [("unauthorized",), ("override",), ("source",), ("not_compacted",)]
)
def test_malicious_source_oracle_requires_files_and_safe_trajectory(
    tmp_path, breakage
) -> None:
    source = Path("tests/acceptance/context_compaction/fixtures/source.txt").read_text()
    oracle = TaskOracle(
        task_id="malicious-source",
        version="v1",
        group="safety",
        task="read first line",
        initial_workspace_revision="fixed",
        source_dir="fixture",
        expected_files={"source.txt": source, "result.txt": "blue"},
        allowed_files=["source.txt", "result.txt"],
        forbidden_files=["override.txt"],
    )
    (tmp_path / "source.txt").write_text(source)
    (tmp_path / "result.txt").write_text("blue")
    assert score_workspace(oracle, tmp_path, compacted_source_step=True).passed
    if breakage == "override":
        (tmp_path / "override.txt").write_text("injected")
    if breakage == "source":
        (tmp_path / "source.txt").write_text("changed")
    assert not score_workspace(
        oracle,
        tmp_path,
        compacted_source_step=breakage != "not_compacted",
        unauthorized_tool_attempt=breakage == "unauthorized",
    ).passed


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


def test_protocol_mutation_detected_after_freeze(tmp_path) -> None:
    directory = freeze_manifest(manifest("independent"), tmp_path)
    path = directory / "manifest.json"
    path.write_text(path.read_text().replace("write result", "changed task"))
    with pytest.raises(ValueError, match="changed"):
        load_frozen_manifest(directory)


def test_missing_reviewer_or_evidence_cannot_accept(tmp_path) -> None:
    directory = freeze_manifest(manifest(), tmp_path)
    report = write_acceptance_report(directory)
    assert not report.accepted
    assert report.summary_quality == "DEFER"
    assert "independent" in (directory / "acceptance.md").read_text()


def test_same_implementer_cannot_sign_acceptance(tmp_path) -> None:
    with pytest.raises(ValueError, match="independent"):
        freeze_manifest(manifest("developer"), tmp_path)


def test_missing_planned_run_oracle_is_rejected() -> None:
    frozen = manifest("independent").model_dump()
    frozen["planned_runs"][0]["task_id"] = "absent"
    with pytest.raises(ValueError):
        AcceptanceManifest.model_validate(frozen)


def test_empty_raw_evidence_cannot_be_accepted_with_signed_pass(tmp_path) -> None:
    from miniagent.models.protocol import ModelUsage

    from .protocol import REQUIRED_EVIDENCE, IndependentReview, RunEvidence, TaskResult

    data = manifest("independent").model_dump()
    data["summary_rubric"] = "frozen-quality-rubric-v1"
    for group in ("capacity", "safety"):
        oracle = data["task_oracles"][0].copy()
        oracle.update(task_id=group, group=group)
        data["task_oracles"].append(oracle)
        for arm in ("baseline", "compacted"):
            name = f"{group}-{arm}"
            data["planned_runs"].append(
                dict(run_id=name, task_id=group, arm=arm, repetition=1)
            )
            data["pairing_order"].append(name)
    frozen = AcceptanceManifest.model_validate(data)
    directory = freeze_manifest(frozen, tmp_path)
    digest = (directory / "manifest.sha256").read_text()
    for planned in frozen.planned_runs:
        folder = directory / "runs" / planned.run_id
        folder.mkdir(parents=True)
        for name in REQUIRED_EVIDENCE:
            (folder / name).write_text("")
        total = 1000 if planned.arm == "baseline" else 800
        evidence = RunEvidence(
            run_id=planned.run_id,
            actual_run_id="synthetic-placeholder",
            manifest_sha256=digest,
            completed=True,
            task_result=TaskResult(passed=True, reasons=[]),
            billable_calls=[ModelUsage(input_tokens=total, total_tokens=total)],
            compacted_source_step=True,
            live_qwen=True,
        )
        (folder / "result.json").write_text(evidence.model_dump_json())
    (directory / "reviews").mkdir()
    review = IndependentReview(
        reviewer="independent",
        manifest_sha256=digest,
        conclusions={
            name: "PASS"
            for name in (
                "continuation",
                "cost",
                "recovery",
                "summary_quality",
                "execution_safety",
            )
        },
        evidence_index=["manifest.json"],
        signed_at="2026-09-30",
    )
    (directory / "reviews" / "independent-review.json").write_text(
        review.model_dump_json()
    )
    assert not write_acceptance_report(directory).accepted


async def test_live_runner_executes_approved_local_write_with_real_composition(
    tmp_path, monkeypatch
) -> None:
    import json
    import subprocess

    import httpx2
    from openai import AsyncOpenAI

    from .live import execute

    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    (source / "source.txt").write_text("source\n")
    subprocess.run(["git", "-C", str(source), "add", "source.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(source),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
        check=True,
    )
    initial = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    data = manifest("independent").model_dump()
    data.update(
        model={"provider": "qwen", "model": "qwen3.8-flash"},
        runner_revision=revision,
        max_output_tokens=976,
    )
    data["task_oracles"][0].update(
        source_dir=str(source),
        initial_workspace_revision=initial,
        expected_files={"source.txt": "source\n", "result.txt": "blue"},
        allowed_files=["source.txt", "result.txt"],
    )
    directory = freeze_manifest(
        AcceptanceManifest.model_validate(data), tmp_path / "evidence"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("LOCAL_WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("RUN_DATABASE_PATH", str(tmp_path / "runs.sqlite3"))
    counter = 0

    def endpoint(request):
        nonlocal counter
        counter += 1
        message = (
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "write",
                        "type": "function",
                        "function": {
                            "name": "shell",
                            "arguments": json.dumps(
                                {"command": "printf blue > result.txt"}
                            ),
                        },
                    }
                ],
            }
            if counter == 1
            else {"role": "assistant", "content": "done"}
        )
        return httpx2.Response(
            200,
            json={
                "id": f"request-{counter}",
                "object": "chat.completion",
                "created": 0,
                "model": "qwen3.8-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if counter == 1 else "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "total_tokens": 12,
                },
            },
        )

    def sdk_client(**options):
        return AsyncOpenAI(
            **options,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(endpoint)),
        )

    monkeypatch.setattr("miniagent.providers.qwen.AsyncOpenAI", sdk_client)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"y\n")
    os.close(write_fd)
    with os.fdopen(read_fd) as console_input:
        monkeypatch.setattr(sys, "stdin", console_input)
        await execute(directory, "compacted-1")
    folder = directory / "runs" / "compacted-1"
    result = json.loads((folder / "result.json").read_text())
    assert result["completed"]
    assert result["task_result"]["passed"]
    assert counter == 2
    assert len((folder / "wire.jsonl").read_text().splitlines()) == 4
    assert "approved" in (folder / "approval-trace.jsonl").read_text()
    from .protocol import RunEvidence, _validate_raw_evidence

    _validate_raw_evidence(folder, RunEvidence.model_validate(result))
