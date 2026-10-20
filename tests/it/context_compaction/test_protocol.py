from pathlib import Path

import pytest

from tests.support.context_compaction.cases import manifest
from tests.support.context_compaction.protocol import (
    AcceptanceManifest,
    TaskOracle,
    freeze_manifest,
    load_frozen_manifest,
    score_workspace,
    write_acceptance_report,
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
    source = Path("tests/support/context_compaction/fixtures/source.txt").read_text()
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


def test_empty_raw_evidence_cannot_be_accepted_with_signed_pass(tmp_path) -> None:
    from miniagent.models.protocol import ModelUsage
    from tests.support.context_compaction.protocol import (
        REQUIRED_EVIDENCE,
        IndependentReview,
        RunEvidence,
        TaskResult,
    )

    data = manifest("independent").model_dump()
    data["summary_rubric"] = "frozen-quality-rubric-v1"
    for group in ("capacity", "safety"):
        oracle = data["task_oracles"][0].copy()
        oracle.update(task_id=group, group=group)
        data["task_oracles"].append(oracle)
        for arm in ("baseline", "compacted"):
            name = f"{group}-{arm}"
            data["planned_runs"].append(
                {"run_id": name, "task_id": group, "arm": arm, "repetition": 1}
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
