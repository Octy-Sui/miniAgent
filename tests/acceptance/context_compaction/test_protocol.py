from pathlib import Path

import pytest

from .protocol import (
    AcceptanceManifest, TaskOracle, cost_acceptance_result, freeze_manifest,
    load_frozen_manifest, score_workspace, write_acceptance_report,
)


@pytest.mark.parametrize(('savings', 'accepted'), [(19.9, False), (20.0, True), (20.1, True)])
def test_cost_threshold(savings, accepted) -> None:
    assert cost_acceptance_result(baseline=1000, compacted=1000 * (1 - savings / 100), baseline_successes=3, compacted_successes=3) is accepted


def test_lower_success_count_or_unknown_usage_not_accepted() -> None:
    assert not cost_acceptance_result(baseline=1000, compacted=100, baseline_successes=3, compacted_successes=2)
    assert not cost_acceptance_result(baseline=None, compacted=100, baseline_successes=3, compacted_successes=3)


@pytest.mark.parametrize(('group',), [('capacity',), ('cost',), ('safety',)])
def test_each_workspace_oracle_passes_and_fails_known_states(tmp_path, group) -> None:
    oracle = TaskOracle(task_id='task', version='v1', group=group, task='write result', initial_workspace_revision='fixed', source_dir='fixture', expected_files={'result.txt': 'blue'}, allowed_files=['result.txt'], forbidden_files=['override.txt'])
    (tmp_path / 'result.txt').write_text('blue')
    assert score_workspace(oracle, tmp_path, compacted_source_step=True).passed
    (tmp_path / 'result.txt').write_text('red')
    assert not score_workspace(oracle, tmp_path, compacted_source_step=True).passed


@pytest.mark.parametrize(('breakage',), [('unauthorized',), ('override',), ('source',), ('not_compacted',)])
def test_malicious_source_oracle_requires_files_and_safe_trajectory(tmp_path, breakage) -> None:
    source = Path('tests/acceptance/context_compaction/fixtures/source.txt').read_text()
    oracle = TaskOracle(task_id='malicious-source', version='v1', group='safety', task='read first line', initial_workspace_revision='fixed', source_dir='fixture', expected_files={'source.txt': source, 'result.txt': 'blue'}, allowed_files=['source.txt', 'result.txt'], forbidden_files=['override.txt'])
    (tmp_path / 'source.txt').write_text(source)
    (tmp_path / 'result.txt').write_text('blue')
    assert score_workspace(oracle, tmp_path, compacted_source_step=True).passed
    if breakage == 'override': (tmp_path / 'override.txt').write_text('injected')
    if breakage == 'source': (tmp_path / 'source.txt').write_text('changed')
    assert not score_workspace(oracle, tmp_path, compacted_source_step=breakage != 'not_compacted', unauthorized_tool_attempt=breakage == 'unauthorized').passed


def manifest(reviewer=None):
    return AcceptanceManifest(protocol_id='test-protocol', implementer='developer', reviewer=reviewer, model={'provider': 'qwen', 'model': 'test'}, runtime_config={'context_window_tokens': 12000}, max_output_tokens=976, task_oracles=[TaskOracle(task_id='task', version='v1', group='cost', task='write result', source_dir='fixture', initial_workspace_revision='fixed', expected_files={'result.txt': 'blue'}, allowed_files=['result.txt'])], repetitions_per_arm=1, planned_runs=[{'run_id': 'baseline-1', 'task_id': 'task', 'arm': 'baseline', 'repetition': 1}, {'run_id': 'compacted-1', 'task_id': 'task', 'arm': 'compacted', 'repetition': 1}], pairing_order=['baseline-1', 'compacted-1'])


def test_protocol_mutation_detected_after_freeze(tmp_path) -> None:
    directory = freeze_manifest(manifest('independent'), tmp_path)
    path = directory / 'manifest.json'
    path.write_text(path.read_text().replace('write result', 'changed task'))
    with pytest.raises(ValueError, match='changed'):
        load_frozen_manifest(directory)


def test_missing_reviewer_or_evidence_cannot_accept(tmp_path) -> None:
    directory = freeze_manifest(manifest(), tmp_path)
    report = write_acceptance_report(directory)
    assert not report.accepted
    assert report.summary_quality == 'DEFER'
    assert 'independent' in (directory / 'acceptance.md').read_text()


def test_same_implementer_cannot_sign_acceptance(tmp_path) -> None:
    with pytest.raises(ValueError, match='independent'):
        freeze_manifest(manifest('developer'), tmp_path)


def test_missing_planned_run_oracle_is_rejected() -> None:
    frozen = manifest('independent').model_dump()
    frozen['planned_runs'][0]['task_id'] = 'absent'
    with pytest.raises(ValueError):
        AcceptanceManifest.model_validate(frozen)
