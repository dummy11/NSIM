"""Resolve scenario defaults, submit recipes and explicit execution overrides."""

import re

from nsim.config import ConfigError


def _replace(args, flag, value):
    result = []
    index = 0
    while index < len(args):
        if args[index] == flag:
            index += 2
        else:
            result.append(args[index])
            index += 1
    result.extend((flag, value))
    return result


def recorded(plan):
    """Read new effective profiles, with a conservative legacy-plan fallback."""
    result = {}
    fallback_coverage = plan['execution']['phase'] == 'regr'
    fallback_waves = plan['execution']['phase'] in ('run', 'sim')
    for submit in plan['submits']:
        vcs = submit.get('vcs', {})
        profile = submit.get('effective_profile')
        if profile is None:
            compile_on = '-cm' in vcs.get('compile_args', [])
            profile = dict(coverage='-cm' in vcs.get('run_args', []) if submit['adapter'] == 'vcs' else fallback_coverage,
                           waves=vcs.get('fsdb', fallback_waves), compile_coverage=compile_on)
        result[submit['name']] = {key: profile[key] for key in ('coverage', 'waves')}
    return result


def merged_options(previous, current):
    result = dict(previous or {})
    for key, value in (current or {}).items():
        if value is not None and value != [] and value != {}:
            result[key] = value
    return result


def changed(options, plusargs=()):
    return bool(plusargs or any(value is not None and value != [] and value != {}
                                for value in (options or {}).values()))


def override_groups(previous, current):
    """Apply final command-line arguments by option key, appending positionals."""
    from nsim.caselist import argument_groups
    groups = argument_groups(previous)
    for index, (key, value) in enumerate(argument_groups(current).items()):
        groups[f'@cli{index}' if key.startswith('@') else key] = value
    return [argument for group in groups.values() for argument in group]


def runtime_overrides(config, arguments, submit, root, case):
    """Give --sim-arg precedence over submit and caselist arguments."""
    if not arguments:
        return config
    from nsim.caselist import MANAGED, RUNTIME, bind_case, template, argument_groups
    from nsim.submit_files import bind_tree
    values = bind_tree(list(arguments), submit['files'], root, 'simulate')
    allowed = RUNTIME if submit['adapter'] == 'vcs' else RUNTIME - {'wave', 'log', 'dpi_library'}
    for value in values:
        template(value, allowed)
    overrides = argument_groups([bind_case(value, case) for value in values])
    groups = argument_groups(config['argv'])
    for index, (key, value) in enumerate(overrides.items()):
        if key in MANAGED:
            raise ConfigError(f'{key} is managed by NSIM')
        if config['origins'].get(key) == 'cli':
            continue
        target = f'@cli{index}' if key.startswith('@') else key
        groups[target] = value
        config['origins'][target] = 'cli-sim-arg'
    config['argv'] = [argument for group in groups.values() for argument in group]
    return config


def apply(submit, phase, options=None, defaults=None):
    options, defaults = options or {}, defaults or {}
    config = submit.get('vcs', {})
    fallback_coverage = phase == 'regr'
    fallback_waves = phase in ('run', 'sim')
    coverage = options.get('coverage')
    if coverage is None:
        coverage = defaults.get('coverage', fallback_coverage)
    waves = options.get('waves')
    if waves is None:
        waves = defaults.get('waves', config.get('fsdb', fallback_waves))
    compile_coverage = options.get('compile_coverage')
    if compile_coverage is None:
        compile_coverage = coverage
    if any(type(value) is not bool for value in (coverage, waves, compile_coverage)):
        raise ConfigError('coverage and waves must be on or off')
    if phase == 'com' and options.get('waves') is not None:
        raise ConfigError('com has no waveform simulation phase')
    submit['effective_profile'] = dict(coverage=coverage, waves=waves,
                                       compile_coverage=compile_coverage,
                                       coverage_source=('cli' if options.get('coverage') is not None else
                                                        'source' if 'coverage' in defaults else 'scenario'),
                                       waves_source=('cli' if options.get('waves') is not None else
                                                     'source' if 'waves' in defaults else
                                                     'legacy_submit' if 'fsdb' in config else 'scenario'))
    raw_compile = list(options.get('compile_args') or ())
    raw_sim = list(options.get('sim_args') or ())
    if any(arg.startswith('-cm') for arg in raw_compile + raw_sim):
        raise ConfigError('configure -cm options through coverage settings, not raw stage arguments')
    if submit['adapter'] == 'vcs':
        recipe = {key: list(config['coverage'][key]) for key in ('compile_args', 'run_args')}
        metrics = options.get('cm_metrics')
        hierarchy = options.get('cm_hier')
        directory = options.get('cm_dir')
        if metrics is not None:
            if not re.fullmatch(r'[A-Za-z0-9_+]+', metrics):
                raise ConfigError('--cm-metrics requires a non-empty metric list')
            for key in recipe:
                recipe[key] = _replace(recipe[key], '-cm', metrics)
        if hierarchy is not None:
            recipe['compile_args'] = _replace(recipe['compile_args'], '-cm_hier', hierarchy)
        if directory is not None:
            recipe['run_args'] = _replace(recipe['run_args'], '-cm_dir', directory)
        if any(value is not None for value in (metrics, hierarchy, directory)) and not coverage:
            raise ConfigError('coverage parameter overrides require --coverage on')
        config['coverage'] = recipe
        defines = list(options.get('defines') or ())
        for definition in defines:
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(?:=[^\0]*)?', definition):
                raise ConfigError('--define requires NAME or NAME=VALUE')
        config['compile_args'] += ([*recipe['compile_args']] if compile_coverage else [])
        config['compile_args'] += ['+define+' + value for value in defines] + raw_compile
        config['run_args'] += ([*recipe['run_args']] if coverage else [])
        config['fsdb'] = waves
        config['coverage_compile_enabled'] = compile_coverage
        config['coverage_collect_enabled'] = coverage
        submit['effective_profile'].update(coverage_compile_applied=compile_coverage,
                                           coverage_collect_applied=coverage,
                                           waves_applied=waves and bool(config['fsdb_plusargs']))
    else:
        if any(options.get(key) is not None for key in ('cm_metrics', 'cm_hier', 'cm_dir')):
            raise ConfigError('-cm options require the vcs adapter')
        if options.get('defines'):
            raise ConfigError('--define requires the vcs adapter')
        if submit['adapter'] == 'command':
            recipe = submit.get('coverage', {})
            wave_recipe = submit.get('waveform', {})
            compile_recipe = recipe.get('compile_args', [])
            run_recipe = recipe.get('run_args', [])
            wave_args = wave_recipe.get('run_args', [])
            if compile_coverage and compile_recipe and not submit.get('compile'):
                raise ConfigError('coverage compilation requires a command compile step')
            if submit.get('compile') and compile_coverage:
                submit['compile']['argv'] += compile_recipe
            submit['argv'] += (run_recipe if coverage else []) + (wave_args if waves else [])
            submit['effective_profile'].update(coverage_compile_applied=compile_coverage and bool(compile_recipe),
                                               coverage_collect_applied=coverage and bool(run_recipe),
                                               waves_applied=waves and bool(wave_args))
        else:
            submit['effective_profile'].update(coverage_compile_applied=False,
                                               coverage_collect_applied=False, waves_applied=False)
        if raw_compile:
            if not submit.get('compile'):
                raise ConfigError('--compile-arg requires a command compile step')
            submit['compile']['argv'] += raw_compile
        if raw_sim and submit['adapter'] != 'command':
            raise ConfigError('--sim-arg requires a command or vcs adapter')
    return submit
