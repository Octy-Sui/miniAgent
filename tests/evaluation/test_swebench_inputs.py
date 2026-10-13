import hashlib
import json
import subprocess
from uuid import uuid4

import pytest

from miniagent.artifacts import ArtifactWrite, FileArtifactStore
from miniagent.evaluation import CandidateOutput, EvaluationRequest
from miniagent.evaluation.swebench import EvaluationInputError, prepare_attempt
from miniagent.evaluation.swebench_data import SWEbenchTaskContext, load_prepared_task


@pytest.fixture
async def input_case(tmp_path, prepared_bundle):
    run_id = uuid4()
    store = FileArtifactStore(tmp_path / "artifacts")
    prepared = load_prepared_task(
        prepared_bundle, SWEbenchTaskContext(instance_id="django__django-11477")
    )
    return run_id, store, prepared


async def patch_request(run_id, store, content):
    ref = await store.put(
        ArtifactWrite(
            run_id=run_id,
            type="workspace_patch",
            metadata={"submission_id": str(uuid4()), "base_revision": "agent-baseline"},
        ),
        content,
    )
    return EvaluationRequest(
        run_id=run_id,
        task="issue",
        candidate=CandidateOutput(text="candidate", patch=ref),
    )


@pytest.mark.parametrize(
    ("patch_bytes",),
    [
        (b"diff\nline\n",),
        (b"diff\r\nline\r\n",),
        ('Unicode 中文 " \\ end'.encode(),),
        (b"no trailing newline",),
        (b" ",),
    ],
)
async def test_predictions_round_trip_exact_bytes(input_case, tmp_path, patch_bytes):
    run_id, store, prepared = input_case
    request = await patch_request(run_id, store, patch_bytes)
    model_name = "../../模型/name"
    attempt = await prepare_attempt(
        request,
        prepared=prepared,
        artifact_store=store,
        work_root=tmp_path / "runs",
        model_name=model_name,
    )
    content = (attempt.directory / "predictions.jsonl").read_bytes()
    assert content.endswith(b"\n")
    assert len(content.splitlines()) == 1
    prediction = json.loads(content)
    assert set(prediction) == {"instance_id", "model_name_or_path", "model_patch"}
    assert prediction["model_patch"].encode() == patch_bytes
    assert (
        prediction["model_name_or_path"]
        == "model-" + hashlib.sha256(model_name.encode()).hexdigest()[:16]
    )
    config = json.loads((attempt.directory / "evaluation-config.json").read_bytes())
    assert config["model_name"] == model_name
    assert config["patch_sha256"] == hashlib.sha256(patch_bytes).hexdigest()
    assert config["base_revision"] == "agent-baseline"
    assert attempt.directory.is_relative_to(tmp_path / "runs")


async def test_attempt_does_not_reuse_directory(input_case, tmp_path):
    run_id, store, prepared = input_case
    request = await patch_request(run_id, store, b"patch")
    first = await prepare_attempt(
        request,
        prepared=prepared,
        artifact_store=store,
        work_root=tmp_path,
        model_name="model",
    )
    second = await prepare_attempt(
        request,
        prepared=prepared,
        artifact_store=store,
        work_root=tmp_path,
        model_name="model",
    )
    assert first.directory != second.directory
    assert first.harness_run_id != second.harness_run_id
    assert (
        first.instance_directory
        == first.directory
        / "logs/run_evaluation"
        / first.harness_run_id
        / first.model_key
        / prepared.record.instance_id
    )


@pytest.mark.parametrize(
    ("content", "code"), [(b"", "EMPTY_PATCH"), (b"\xff", "PATCH_ARTIFACT_INVALID")]
)
async def test_invalid_patch_does_not_create_attempt(
    input_case, tmp_path, content, code
):
    run_id, store, prepared = input_case
    request = await patch_request(run_id, store, content)
    root = tmp_path / "runs"
    with pytest.raises(EvaluationInputError, match=code):
        await prepare_attempt(
            request,
            prepared=prepared,
            artifact_store=store,
            work_root=root,
            model_name="model",
        )
    assert not root.exists()


async def test_patch_from_another_run_is_rejected(input_case, tmp_path):
    _, store, prepared = input_case
    request = await patch_request(uuid4(), store, b"patch")
    request = request.model_copy(update={"run_id": uuid4()})
    with pytest.raises(EvaluationInputError, match="PATCH_ARTIFACT_INVALID"):
        await prepare_attempt(
            request,
            prepared=prepared,
            artifact_store=store,
            work_root=tmp_path / "runs",
            model_name="model",
        )


@pytest.fixture
def binary_patch(tmp_path):
    repo = tmp_path / "git"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], check=True, capture_output=True
        ).stdout

    git("init", "--quiet")
    (repo / "binary").write_bytes(b"\x00original\x01")
    (repo / "renamed").write_text("rename\n")
    git("add", ".")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.test",
        "commit",
        "--quiet",
        "-m",
        "baseline",
    )
    (repo / "binary").write_bytes(b"\x00changed\x02")
    (repo / "renamed").rename(repo / "new-name")
    (repo / "new-name").chmod(0o755)
    git("add", "-A")
    return git("diff", "--cached", "--binary", "--full-index", "HEAD")


async def test_real_binary_mode_and_rename_diff_round_trips(
    input_case, tmp_path, binary_patch
):
    run_id, store, prepared = input_case
    assert b"GIT binary patch" in binary_patch
    assert b"rename from" in binary_patch
    assert b"new mode 100755" in binary_patch
    request = await patch_request(run_id, store, binary_patch)
    attempt = await prepare_attempt(
        request,
        prepared=prepared,
        artifact_store=store,
        work_root=tmp_path / "runs",
        model_name="model",
    )
    prediction = json.loads((attempt.directory / "predictions.jsonl").read_bytes())
    assert prediction["model_patch"].encode() == binary_patch
