import copy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('verify_release_recovery', Path(__file__).resolve().parents[2] / 'scripts/release/verify_release_recovery.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def source():
    run = {'status': 'completed', 'conclusion': 'failure', 'event': 'workflow_dispatch',
           'path': '.github/workflows/release.yml', 'head_sha': 'a' * 40,
           'head_repository': {'full_name': 'owner/engine'}, 'head_branch': 'master', 'run_attempt': 1}
    jobs = [{'name': name, 'status': 'completed', 'conclusion': 'success'} for name in module.BUILD_JOBS]
    jobs.append({'name': module.PUBLISH_JOB, 'status': 'completed', 'conclusion': 'failure'})
    return run, {'jobs': jobs, 'total_count': len(jobs)}


def test_recovery_preserves_original_tested_revision_and_artifact_attempt():
    run, jobs = source()
    paths = ['scripts/release/publish_hub_objects.py', 'docs/tools/build-api-diff.mjs',
             'docs/tools/check-site-information.mjs', 'docs/tools/test-site-information.mjs',
             'docs/tools/verify-site.mjs']
    assert module.validate(run, jobs, 'owner/engine', 'a' * 40, 'b' * 40, paths) == {
        'sha': 'a' * 40, 'publisher_sha': 'b' * 40, 'branch': 'master', 'attempt': 1}


@pytest.mark.parametrize('change', ['engine', 'incomplete_jobs', 'build_failed', 'build_skipped', 'missing_gate', 'duplicate', 'wrong_sha', 'fork', 'pr', 'wrong_workflow', 'running', 'public_success'])
def test_recovery_rejects_unvalidated_or_changed_payloads(change):
    run, jobs = source()
    paths = []
    if change == 'engine':
        paths = ['cpp/infernux/engine.cpp']
    elif change == 'incomplete_jobs':
        jobs['total_count'] += 1
    elif change in ('build_failed', 'build_skipped'):
        jobs['jobs'][0]['conclusion'] = 'failure' if change == 'build_failed' else 'skipped'
    elif change == 'missing_gate':
        jobs['jobs'].pop(0)
        jobs['total_count'] -= 1
    elif change == 'duplicate':
        jobs['jobs'].append(copy.deepcopy(jobs['jobs'][0]))
        jobs['total_count'] += 1
    elif change == 'wrong_sha':
        run['head_sha'] = 'c' * 40
    elif change == 'fork':
        run['head_repository']['full_name'] = 'fork/engine'
    elif change == 'pr':
        run['event'] = 'pull_request'
    elif change == 'wrong_workflow':
        run['path'] = '.github/workflows/test-windows-signing.yml'
    elif change == 'running':
        run['status'] = 'in_progress'
    else:
        run['conclusion'] = 'success'
    with pytest.raises(ValueError):
        module.validate(run, jobs, 'owner/engine', 'a' * 40, 'b' * 40, paths)
