"""Submit-owned compile/simulation artifacts and optional regression records."""

from pathlib import Path
from contextlib import contextmanager, ExitStack
import fcntl
import os
import shutil
import threading

from nsim.config import ConfigError
from nsim.ownership import OWNER_FILE, claim_product


def check_environment_link(directory, project_root, required=False):
    """Reserve env for the complete verification root, never a resource subset."""
    target = Path(project_root).resolve()
    link = Path(directory) / 'env'
    if not target.is_dir():
        raise ConfigError(f'verification environment root is unavailable: {target}')
    if link.is_symlink():
        try:
            actual = link.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ConfigError(f'invalid environment link: {link}') from exc
        if actual != target:
            raise ConfigError(f'environment link target mismatch: {link}; expected {target}')
    elif link.exists():
        raise ConfigError(f'environment link path is occupied: {link}; env is reserved by NSIM')
    elif required:
        raise ConfigError(f'environment link is missing: {link}')
    return link


def prepare_environment_link(directory, project_root):
    link = check_environment_link(directory, project_root)
    if not link.is_symlink():
        try:
            link.symlink_to(Path(project_root).resolve(), target_is_directory=True)
        except FileExistsError:
            # Concurrent readers may prepare the same link; accept only our target.
            pass
    return check_environment_link(directory, project_root, required=True)


def is_regression(plan):
    return plan.get('kind', 'regression') == 'regression' or len(plan['tasks']) != 1


def isolated_regression(plan):
    # Keep historical layouts readable, including single-task views of a regression.
    return plan.get('execution', {}).get('layout') == 'isolated-regression-v1'


def regression_directory(root, plan):
    replay_parent = plan.get('execution', {}).get('replay_parent')
    if replay_parent is not None:
        parent = Path(replay_parent)
        name = plan['execution']['replay_dir']
        if (parent.is_absolute() or not parent.parts or '..' in parent.parts or
                not isinstance(name, str) or not name or name in ('.', '..') or
                '/' in name or '\\' in name or '\0' in name):
            raise ConfigError('replay directory must be a simple name below the original regression')
        return Path(root) / parent / name
    parent = plan.get('execution', {}).get('rerun_parent')
    if parent is not None:
        parent = Path(parent)
        if parent.is_absolute() or not parent.parts or '..' in parent.parts:
            raise ConfigError('rerun parent must be a relative path inside the original output root')
        return Path(root) / parent / 'rerun' / plan['id']
    return Path(root) / 'regr_dir' / plan['id']


def workspace_directory(root, plan, task):
    submit = plan['submits'][task['submit']]['name']
    if isolated_regression(plan):
        root = regression_directory(root, plan)
    return Path(root) / submit / plan.get('execution', {}).get('workspace', 'rtl')


def build_directory(root, plan, task):
    value = plan.get('execution', {}).get('com_dir', 'com_dir')
    return Path(os.path.abspath(workspace_directory(root, plan, task) / value))


def simulation_root(root, plan, task):
    value = plan.get('execution', {}).get('sim_dir', 'sim_dir')
    return Path(os.path.abspath(workspace_directory(root, plan, task) / value))


def simulation_directory(root, plan, task):
    return simulation_root(root, plan, task) / f"{task['case']}_{task['seed']}"


def record_directory(root, plan):
    if is_regression(plan):
        path = regression_directory(root, plan)
        return path / 'report' if plan.get('execution', {}).get('layout') else path
    if plan.get('execution', {}).get('phase') == 'com':
        return build_directory(root, plan, plan['tasks'][0])
    return simulation_directory(root, plan, plan['tasks'][0])


def validate_layout(plan, roots):
    for root in roots:
        destinations = [str(simulation_directory(root, plan, task)).casefold() for task in plan['tasks']]
        if len(set(destinations)) != len(destinations):
            raise ConfigError('duplicate simulation destinations; case names must not differ only in case')
    def overlaps(a, b):
        return a == b or a in b.parents or b in a.parents
    protected = [Path(plan['project']['root']), Path(__file__).resolve().parent,
                 *[Path(s['root']) for s in plan['project'].get('sources', {}).values()],
                 *[Path(p) for p in plan['input_hashes']],
                 *[root / 'preview_dir' for root in roots],
                 *[root / 'regr_batch_dir' for root in roots]]
    from nsim.submit_files import protected_paths
    protected += [Path(path) for submit in plan['submits']
                  for path in protected_paths(submit.get('resolved_files', {}))]
    if not isolated_regression(plan):
        protected += [root / 'regr_dir' for root in roots]
    else:
        for root in roots:
            for task in plan['tasks']:
                submit_name = plan['submits'][task['submit']]['name']
                if submit_name.casefold() in ('report', 'rerun'):
                    raise ConfigError(f'submit name conflicts with regression metadata: {submit_name}')
                owner = regression_directory(root, plan) / submit_name
                for path in (build_directory(root, plan, task), simulation_root(root, plan, task)):
                    if owner not in path.parents:
                        raise ConfigError(f'regression product must stay inside its submit directory: {path}')
    workspaces = [workspace_directory(root, plan, task) for root in roots for task in plan['tasks']]
    simulation_roots = {simulation_root(root, plan, task) for root in roots for task in plan['tasks']}
    simulation_owners = {}
    for root in roots:
        for task in plan['tasks']:
            parent = simulation_root(root, plan, task)
            if (any(overlaps(parent, path) for path in protected)
                    or any(parent == path or parent in path.parents for path in workspaces)):
                raise ConfigError(f'simulation directory overlaps protected locations: {parent}')
            if any(p.is_symlink() for p in (parent, *parent.parents)):
                raise ConfigError(f'refusing symlink in simulation directory: {parent}')
            for candidate in roots:
                if candidate in parent.parents:
                    relative = parent.relative_to(candidate)
                    if len(relative.parts) < 3 or 'com_dir' in relative.parts:
                        raise ConfigError(f'simulation directory is not a product directory: {parent}')
            key = str(parent).casefold()
            if key in simulation_owners and simulation_owners[key] != task['submit']:
                raise ConfigError(f'multiple submits target the same simulation directory: {parent}')
            simulation_owners[key] = task['submit']
    protected += list(simulation_roots)
    owners = {}
    for root in roots:
        for task in plan['tasks']:
            build = build_directory(root, plan, task)
            if (any(overlaps(build, path) for path in protected)
                    or any(build == path or build in path.parents for path in workspaces)):
                raise ConfigError(f'compile directory overlaps protected input/output locations: {build}')
            if any(p.is_symlink() for p in (build, *build.parents)):
                raise ConfigError(f'refusing symlink in compile directory: {build}')
            # Never let one submit erase another submit or workspace tree.
            for candidate in roots:
                if candidate in build.parents:
                    relative = build.relative_to(candidate)
                    if len(relative.parts) < 3 or 'sim_dir' in relative.parts:
                        raise ConfigError(f'compile directory is not a product directory: {build}')
            key = str(build).casefold()
            owner = (task['submit'], task['build_id'])
            if key in owners and owners[key] != owner:
                raise ConfigError(f'multiple compile configurations target the same directory: {build}')
            owners[key] = owner
    paths = [Path(path) for path in owners] + [Path(path) for path in simulation_owners]
    for index, path in enumerate(paths):
        if any(overlaps(path, other) for other in paths[index + 1:]):
            raise ConfigError('compile and simulation directories must not be nested')


def make_directory(path, root):
    """Create beneath a preflighted root without following managed symlinks."""
    path, root = Path(path), Path(root)
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ConfigError(f'refusing symlink in output layout: {current}')
        current.mkdir(exist_ok=True)
    return path


@contextmanager
def simulation_lock(path, root, plan):
    existing = (path / OWNER_FILE).exists()
    make_directory(path, root)
    claim_product(path, plan, 'simulation')
    lock = path / '.run.lock'
    if lock.is_symlink():
        raise ConfigError(f'refusing symlink in simulation lock: {lock}')
    with lock.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ConfigError(f'simulation directory is in use: {path}') from exc
        from nsim.lsf import check_reservations
        check_reservations(path, require_case_record=existing)
        check_environment_link(path, plan['project']['root'])
        yield stream


class SimulationDirectories:
    """Own each case/seed directory until the controller finishes publication."""

    def __init__(self, plan, min_free_bytes=0, min_free_inodes=0):
        self.plan = plan
        self.thresholds = (min_free_bytes, min_free_inodes)
        self.stack = ExitStack()
        self.lock = threading.Lock()
        self.reserved = {}
        self.prepared = set()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def reserve(self, root, task):
        path = simulation_directory(root, self.plan, task)
        with self.lock:
            if path not in self.reserved:
                parent = simulation_root(root, self.plan, task)
                if root in parent.parents:
                    make_directory(parent, root)
                else:
                    # An explicitly external output parent must already be mounted.
                    from nsim.storage import preflight
                    preflight(parent if parent.is_dir() else parent.parent, *self.thresholds)
                    parent.mkdir(exist_ok=True)
                self.reserved[path] = self.stack.enter_context(simulation_lock(path, parent, self.plan))
        return path

    def descriptor(self, root, task):
        return self.reserved[simulation_directory(root, self.plan, task)].fileno()

    def prepare(self, root, task):
        path = self.reserve(root, task)
        with self.lock:
            if path not in self.prepared:
                check_environment_link(path, self.plan['project']['root'])
                # Invalidate completion before deleting any result. Keep the lock
                # inode stable so a competing controller cannot bypass ownership.
                (path / 'final.json').unlink(missing_ok=True)
                for child in path.iterdir():
                    if child.name in ('.run.lock', OWNER_FILE):
                        continue
                    if child.is_symlink() or not child.is_dir():
                        child.unlink()
                    else:
                        shutil.rmtree(child)
                prepare_environment_link(path, self.plan['project']['root'])
                self.prepared.add(path)
        return path
