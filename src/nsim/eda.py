"""EDA input resolution and command previews. Planning never invokes an EDA tool."""

import os
from pathlib import Path
import shlex
import shutil
import string

from nsim.config import ConfigError


def expand(value, env):
    # Only explicit submit environment is eligible for filelist expansion.
    import re
    def replace(match):
        key = match.group(1) or match.group(2)
        if key not in env:
            raise ConfigError(f"filelist environment variable is not declared: {key}")
        return env[key]
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)", replace, value)


def filelist(path, root, env, directories=None):
    inputs, output, active = set(), [], set()
    def resolve(token, cwd):
        return (cwd / expand(token, env)).resolve()
    def existing(path, directory=False):
        if not (path.is_dir() if directory else path.is_file()):
            raise ConfigError(f"filelist input unavailable: {path}")
        if directory:
            members = sorted(p.resolve() for p in path.rglob('*') if p.is_file() and p.suffix in ('.v', '.sv', '.vh', '.svh'))
            inputs.update(members)
            if directories is not None:
                directories[str(path)] = [str(p) for p in members]
        else:
            inputs.add(path)
        return str(path)
    def visit(path, cwd):
        if path in active:
            raise ConfigError(f"cyclic filelist: {path}")
        existing(path)
        active.add(path)
        # This intentionally supports a documented subset, rejecting unknown switches.
        lines = [line.split('//', 1)[0] for line in path.read_text().splitlines()]
        tokens = shlex.split('\n'.join(lines), comments=True)
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in ('-f', '-F', '-v', '-y', '-top'):
                i += 1
                if i >= len(tokens):
                    raise ConfigError(f"missing {token} argument in {path}")
                arg = tokens[i]
                if token in ('-f', '-F'):
                    nested = resolve(arg, cwd)
                    visit(nested, nested.parent if token == '-F' else cwd)
                elif token == '-top':
                    output.extend([token, arg])
                else:
                    output.extend([token, existing(resolve(arg, cwd), token == '-y')])
            elif token.startswith('+incdir+'):
                output.append('+incdir+' + '+'.join(existing(resolve(p, cwd), True) for p in token[8:].split('+')))
            elif token.startswith(('+define+', '+libext+')):
                output.append(expand(token, env))
            elif token.startswith(('-', '+')):
                raise ConfigError(f"unsupported filelist option {token}; use vcs.compile_args")
            else:
                output.append(existing(resolve(token, cwd)))
            i += 1
        active.remove(path)
    visit(path, root)
    return output, inputs


def tool(value, base, inputs, missing, allow_missing):
    if '{' in value or '}' in value:
        raise ConfigError('tool executable must be literal')
    found = str((base / value).resolve()) if '/' in value else shutil.which(value)
    if not found or not Path(found).is_file() or not os.access(found, os.X_OK):
        if not allow_missing:
            raise ConfigError(f"executable unavailable: {value}; use --dry-run to inspect commands")
        missing.append(value)
        return value
    inputs.add(Path(found))
    return found


def validate_templates(arguments):
    for arg in arguments:
        try:
            for _, key, spec, conv in string.Formatter().parse(arg):
                if key is not None and (key not in {'project', 'case', 'seed', 'result', 'wave', 'wave_dir', 'log', 'build_dir', 'dpi_library'} or spec or conv):
                    raise ConfigError(f"invalid EDA placeholder: {arg}")
        except ValueError as exc:
            raise ConfigError(f"invalid EDA template: {arg}") from exc


def prepare(submit, project, inputs, allow_missing):
    missing = []
    scheduler = submit['scheduler']
    if scheduler['kind'] == 'lsf':
        for key in ('bsub', 'bjobs', 'bkill'):
            scheduler[key] = tool(scheduler[key], Path(submit['source']).parent, inputs, missing, allow_missing)
    if submit['adapter'] == 'vcs':
        config = submit['vcs']
        if project.schema_version == 2:
            if 'filelist' in config and (project.root / config['filelist']).resolve() != project.root / 'filelist' / 'sim.f':
                raise ConfigError('schema 2 uses registered filelists; omit vcs.filelist or set filelist/sim.f')
            tokens = []
            config['input_directories'] = {}
            for path, root in ((project.rtl_filelist, project.rtl_root), (project.verification_filelist, project.root)):
                resolved, dependencies = filelist(path, root, submit['env'], config['input_directories'])
                if not resolved:
                    raise ConfigError(f'filelist is empty; add sources before planning: {path}')
                tokens.extend(resolved)
                inputs.update(dependencies)
            config['filelist'] = str(project.root / 'filelist' / 'sim.f')
            config['filelist_tokens'] = tokens
        else:
            if 'filelist' not in config:
                raise ConfigError('vcs.filelist is required for legacy projects')
            path = (project.root / config['filelist']).resolve()
            config['filelist'] = str(path)
            config['filelist_tokens'], dependencies = filelist(path, project.root, submit['env'])
            inputs.update(dependencies)
        config['executable'] = tool(config['executable'], Path(submit['source']).parent, inputs, missing, allow_missing)
        for key in ('compile_args', 'run_args', 'fsdb_plusargs', 'result_argv'):
            validate_templates(config[key])
        validate_templates(submit['plusargs'] + [config['fsdb_file']])
        if config['result_argv']:
            config['result_argv'][0] = tool(config['result_argv'][0], Path(submit['source']).parent, inputs, missing, allow_missing)
        if config.get('dpi'):
            dpi = config['dpi']
            dpi['makefile'] = str((project.root / dpi['makefile']).resolve())
            if not Path(dpi['makefile']).is_file():
                raise ConfigError(f"DPI Makefile missing: {dpi['makefile']}")
            inputs.add(Path(dpi['makefile']))
            for key in ('make', 'cc', 'cxx'):
                dpi[key] = tool(dpi[key], Path(submit['source']).parent, inputs, missing, allow_missing)
    submit['unresolved_tools'] = sorted(set(missing))


def commands(plan, task, root, attempt='preview'):
    submit = plan['submits'][task['submit']]
    base = Path(root) / 'regressions' / plan['id']
    build = base / 'build' / task['build_id']
    work = base / 'tasks' / task['id'] / attempt
    waves = base / 'waves' / task['id'] / attempt
    logs = base / 'logs' / task['id'] / attempt
    values = dict(project=plan['project']['root'], case=task['case'], seed=task['seed'],
                  build_dir=str(build), result=str(work / 'result.json'), wave_dir=str(waves),
                  wave=str(waves / 'wave.fsdb'), log=str(logs / 'run.log'), dpi_library='')
    result = dict(task=task['id'], case=task['case'], seed=task['seed'], submit=submit['name'],
                  build_id=task['build_id'], attempt=attempt, cwd=str(work), required_checks=submit['required_checks'],
                  unresolved_tools=submit.get('unresolved_tools', []), environment=submit['env'])
    if submit['adapter'] == 'vcs':
        config = submit['vcs']
        values['wave'] = str((work / config['fsdb_file'].format(**values)).resolve())
        result['wave'] = values['wave']
        if config.get('dpi'):
            dpi = config['dpi']
            cache = (Path(plan['project']['root']) / '.nsim-cache' / 'dpi' if dpi['scope'] == 'project'
                     else base / 'build' / 'dpi' if dpi['scope'] == 'regression' else work / 'dpi') / task['build_id']
            library = cache / 'libnsim.so'
            values['dpi_library'] = str(library)
            result['dpi'] = {'cwd': str(cache), 'library': str(library), 'scope': dpi['scope'], 'argv':
                             [dpi['make'], '-f', dpi['makefile'], dpi['target'], *dpi['args'],
                              'NSIM_DPI_OUTPUT=' + str(library), 'NSIM_PROJECT_ROOT=' + values['project'],
                              'CC=' + dpi['cc'], 'CXX=' + dpi['cxx']]}
        fmt = lambda args: [arg.format(**values) for arg in args]
        result['compile'] = {'cwd': str(build), 'argv': [config['executable'], *fmt(config['compile_args']),
                             *config['filelist_tokens'], *(['-top', config['top']] if config.get('top') else []),
                             '-o', str(build / 'simv'), '-Mdir=' + str(build / 'csrc'), '-l', str(build / 'compile.log')]}
        result['simulate'] = [str(build / 'simv'), *fmt(config['run_args']), *fmt(submit['plusargs']),
                              '+UVM_TESTNAME=' + task['case'], '+ntb_random_seed=' + str(task['seed']),
                              '+NSIM_RESULT=' + values['result']]
        if config.get('dpi'):
            result['simulate'] += ['-sv_lib', values['dpi_library'][:-3]]
        if config['fsdb']:
            result['simulate'] += fmt(config['fsdb_plusargs'])
        result['checker'] = fmt(config['result_argv'])
        result['golden_enabled'] = config['golden_enabled']
        result['verdi'] = {'argv': [config['verdi'], '-dbdir', str(build / 'simv.daidir'), '-ssf', values['wave']],
                           'requires': ['recorded design database', 'recorded FSDB'], 'executed': False}
    else:
        from nsim.runner import command
        result['simulate'] = command(plan, task, Path(values['result']), waves)
    config = submit['scheduler']
    result['scheduler'] = {'kind': config['kind']}
    if config['kind'] == 'lsf':
        argv = [config['bsub'], '-cwd', str(work), '-o', str(logs / 'farm.out'), '-e', str(logs / 'farm.err')]
        if config.get('queue'):
            argv += ['-q', config['queue']]
        for expression in config['resources']:
            argv += ['-R', expression]
        if config['licenses']:
            argv += ['-R', 'rusage[' + ':'.join(f'{k}={v}' for k, v in sorted(config['licenses'].items())) + ']']
        result['scheduler'].update(submit_argv=argv, query_argv=[config['bjobs'], '-json', '-o', 'jobid stat exit_code exec_host', '<job-id>'],
                                   cancel_argv=[config['bkill'], '<job-id>'], worker='execute-node preflight -> compile -> simulate -> checker',
                                   environment={'LSB_EXIT_IF_CWD_NOTEXIST': 'Y'})
    return result
