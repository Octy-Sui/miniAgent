import json

import pytest


async def test_complete_evidence_is_required_for_success(evaluation_case):
    evaluator, request, store = evaluation_case
    result = await evaluator.evaluate(request)
    assert result.passed is True
    assert result.error is None
    assert {
        "workspace_patch",
        "evaluation_predictions",
        "evaluation_config",
        "evaluation_summary",
        "evaluation_report",
        "evaluation_stdout",
        "evaluation_stderr",
        "evaluation_test_output",
        "evaluation_instance_log",
    } <= {ref.type for ref in result.artifacts}
    for ref in result.artifacts:
        assert isinstance(await store.read(ref), bytes)
    config = json.loads(
        await store.read(
            next(ref for ref in result.artifacts if ref.type == "evaluation_config")
        )
    )
    assert config["daemon_id"] == "daemon-a"
    assert config["actual_image_id"] == "sha256:actual"
    assert config["image_id_source"] == "container_inspect"
    call = json.loads((evaluator.executable.parent / "harness-call.json").read_bytes())
    assert call["host"] == "unix:///private/tmp/daemon-a.sock"
    assert call["argv"][call["argv"].index("--workers") + 1] == "1"
    assert "--image" not in call["argv"]


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("unresolved", "TASK_UNRESOLVED"),
        ("missing_report", "REPORT_MISSING"),
        ("bad_report", "REPORT_INVALID"),
        ("string_bool", "REPORT_INVALID"),
        ("missing_tests", "REPORT_INVALID"),
        ("wrong_task", "REPORT_MISMATCH"),
        ("wrong_patch", "REPORT_MISMATCH"),
        ("test_timeout", "EVALUATION_TEST_TIMEOUT"),
        ("fake_timeout", "REPORT_MISSING"),
    ],
)
async def test_execution_verdict_is_strict(evaluation_case, mode, code):
    evaluator, request, _ = evaluation_case
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"mode": mode})
    )
    result = await evaluator.evaluate(request)
    assert result.passed is False
    assert result.error is not None
    assert result.error.code == code
    assert result.error.retryable is False


async def test_nonzero_exit_cannot_be_overridden_by_report(evaluation_case):
    evaluator, request, _ = evaluation_case
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"exit_code": 2})
    )
    result = await evaluator.evaluate(request)
    assert result.error is not None
    assert result.error.code == "HARNESS_PROCESS_FAILED"
    assert "evaluation_report" in {ref.type for ref in result.artifacts}


@pytest.mark.parametrize(("name",), [("test_output.txt",), ("run_instance.log",)])
async def test_missing_success_evidence_prevents_success(evaluation_case, name):
    evaluator, request, _ = evaluation_case
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"omit": name})
    )
    result = await evaluator.evaluate(request)
    assert result.error is not None
    assert result.error.code == "EVALUATION_ARTIFACT_FAILED"


@pytest.mark.parametrize(
    ("mode", "code"),
    [("pass", "EVALUATION_CLEANUP_FAILED"), ("unresolved", "TASK_UNRESOLVED")],
)
async def test_cleanup_failure_preserves_first_error(evaluation_case, mode, code):
    evaluator, request, store = evaluation_case
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"mode": mode, "delete_fail": True})
    )
    result = await evaluator.evaluate(request)
    assert result.error is not None
    assert result.error.code == code
    summary = json.loads(
        await store.read(
            next(ref for ref in result.artifacts if ref.type == "evaluation_summary")
        )
    )
    assert summary["diagnostics"]


async def test_large_logs_are_preserved_with_bounded_summary(evaluation_case):
    evaluator, request, store = evaluation_case
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"log_size": 1000000})
    )
    result = await evaluator.evaluate(request)
    assert result.passed is True
    content = await store.read(
        next(ref for ref in result.artifacts if ref.type == "evaluation_stdout")
    )
    assert len(content) > 1000000
    summary = json.loads(
        await store.read(
            next(ref for ref in result.artifacts if ref.type == "evaluation_summary")
        )
    )
    assert len(summary.get("message", "")) <= 4096


async def test_external_report_symlink_is_not_consumed(evaluation_case):
    evaluator, request, _ = evaluation_case
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"mode": "external_report"})
    )
    result = await evaluator.evaluate(request)
    assert result.error is not None
    assert result.error.code == "REPORT_MISMATCH"
    assert "evaluation_report" not in {ref.type for ref in result.artifacts}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("pass", "EVALUATION_ARTIFACT_FAILED"), ("unresolved", "TASK_UNRESOLVED")],
)
async def test_real_storage_failure_preserves_verdict(evaluation_case, mode, expected):
    evaluator, request, _ = evaluation_case
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"mode": mode, "block_store": str(evaluator.artifact_store.root)})
    )
    result = await evaluator.evaluate(request)
    assert result.error is not None
    assert result.error.code == expected
    assert result.passed is False


async def test_start_failure_is_diagnostic(evaluation_case):
    evaluator, request, _ = evaluation_case
    evaluator.executable.unlink()
    result = await evaluator.evaluate(request)
    assert result.error is not None
    assert result.error.code == "HARNESS_START_FAILED"


async def test_summary_is_an_evidence_checkpoint_not_a_terminal_verdict(
    evaluation_case,
):
    evaluator, request, store = evaluation_case
    result = await evaluator.evaluate(request)
    summary = json.loads(
        await store.read(
            next(ref for ref in result.artifacts if ref.type == "evaluation_summary")
        )
    )
    assert summary["scope"] == "evaluation_evidence_snapshot"
    assert summary["verdict"] == "report_resolved"
    assert summary["terminal_status_source"] == "Run"


async def test_collection_timeout_never_retains_an_optimistic_summary(evaluation_case):
    evaluator, request, _ = evaluation_case
    evaluator.settings = evaluator.settings.model_copy(
        update={
            "swebench_artifact_timeout_seconds": 0.015,
        }
    )
    (evaluator.executable.parent / "harness-scenario.json").write_text(
        json.dumps({"log_size": 32 * 1024 * 1024})
    )
    result = await evaluator.evaluate(request)
    assert result.passed is False
    summaries = list(
        evaluator.settings.swebench_work_root.rglob("evaluation-summary.json")
    )
    assert summaries
    assert json.loads(summaries[0].read_bytes())["verdict"] == "collecting"
