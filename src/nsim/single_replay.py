"""Bind single-case reruns to their recorded, independently copied compilation."""

import copy
from pathlib import Path
import threading

from nsim.compilation import Builds
from nsim.config import ConfigError


def case_source(plan):
    return len(plan['tasks']) == 1 and (
        plan.get('kind') == 'case' or (plan.get('source') or {}).get('scope') == 'case')


def reuse_compilation(plan, previous, root):
    """Check the exact source build before allocating any replay output.

    Builds checks it again under a reader lock when copying the snapshot. A
    source replacement between this preflight and the copy cannot be adopted.
    """
    attempt = previous['attempts'][-1] if previous['attempts'] else {}
    path = attempt.get('build_dir')
    checksum = attempt.get('evidence', {}).get(str(Path(path) / 'build.json')) if path else None
    if not path or not checksum:
        raise ConfigError('case has no recorded compilation to reuse; rerun with --rebuild')
    reference = {'path': path, 'sha256': checksum}
    source_plan = copy.deepcopy(plan)
    task = source_plan['tasks'][0]
    source_plan['execution'].update(phase='sim', layout='single-v1', com_dir=path)
    source_plan['build_refs'] = {task['id']: reference}
    try:
        with Builds(source_plan) as builds:
            builds.ensure(root, task, threading.Event(), {'min_free_bytes': 0, 'min_free_inodes': 0})
    except (OSError, ValueError) as exc:
        raise ConfigError(f'cannot reuse the recorded case compilation: {exc}; '
                          'rerun with --rebuild to compile current inputs') from exc
    plan['execution'].update(phase='sim', reuse_builds={str(plan['tasks'][0]['submit']): reference})
