"""Explicit CLI aliases, also embedded in the dependency-free launcher."""


COMMAND_ALIASES = {
    'bootstrap': ('init',), 'list': ('ls',), 'status': ('st',),
    'update': ('up',), 'switch': ('use',), 'project': ('proj',),
    'submit': ('sub',), 'capabilities': ('caps',), 'version': ('ver',),
    'rerun': ('rr',), 'recover': ('rc',), 'report': ('rpt',),
    'storage': ('disk',), 'install': ('inst',), 'versions': ('vers',),
    'bundle': ('pack',),
}

OPTION_ALIASES = {
    '--version': ('-V',), '--json': ('-J',), '--project': ('-p',),
    '--name': ('-N',), '--verification-root': ('-vr',),
    '--rtl-root': ('-rtl',), '--rtl-filelist': ('-rf',),
    '--verification-filelist': ('-vf',), '--nsim-version': ('-nv',),
    '--output-root': ('-o',), '--case': ('-c',), '--submit': ('-s',),
    '--seed': ('-S',), '-mode': ('-m', '--mode'),
    '-dir': ('-d', '--dir'), '-com-dir': ('-cd', '--com-dir'),
    '-sim-dir': ('-sd', '--sim-dir'), '-repeat': ('-r', '--repeat'),
    '--jobs': ('-j', '-jobs'), '--retries': ('-retry',),
    '--retry-seconds': ('-rt',), '--min-free-bytes': ('-fb',),
    '--min-free-inodes': ('-fi',), '--dry-run': ('-n',),
    '--scheduler': ('-sched',), '--resource': ('-R',),
    '--license': ('-lic',), '--extends': ('-e',),
    '--plusargs': ('-pa',),
    '--bundle': ('-b',), '--wheel': ('-w',), '--wheelhouse': ('-wh',),
    '--checksums': ('-sum',), '--sha256': ('-sha',), '--all': ('-a',),
    '--user': ('-u',), '--system': ('-sys',), '--output': ('-o',),
    '--download-deps': ('-deps',), '--platform': ('-plat',),
    '--python-version': ('-py',), '--implementation': ('-impl',),
}


def add_argument(parser, *names, **kwargs):
    """Keep aliases on the original action, including mutually exclusive groups."""
    expanded = list(names)
    for name in names:
        for alias in OPTION_ALIASES.get(name, ()):
            if alias not in expanded:
                expanded.append(alias)
    return parser.add_argument(*expanded, **kwargs)


def add_command(commands, name, **kwargs):
    parser = commands.add_parser(name, aliases=COMMAND_ALIASES.get(name, ()), **kwargs)
    parser.set_defaults(**{commands.dest: name})
    return parser


def canonical_command(name):
    return next((command for command, aliases in COMMAND_ALIASES.items() if name in aliases), name)


def runtime_arguments(argv):
    """Expand runtime aliases before selecting/launching an installed wheel.

    Only command positions and option spellings change. Values after '=' and
    positional values (including names such as 'init') remain untouched.
    """
    args = list(argv)
    index = 0
    while index < len(args):
        token = args[index]
        if token in ('-o', '--output-root'):
            index += 2
        elif token.startswith(('-o=', '--output-root=')) or (token.startswith('-o') and not token.startswith('--')):
            index += 1
        else:
            break
    if index >= len(args):
        return args
    command = args[index] = canonical_command(args[index])
    if command == 'project' and index + 1 < len(args) and args[index + 1] == 'ls':
        args[index + 1] = 'list'

    # Installer options are handled by its own parser. Preserve existing -o/-j
    # spellings except in commands which have only just gained those aliases.
    aliases = {alias: option for option, values in OPTION_ALIASES.items()
               for alias in values if option not in (
                   '--bundle', '--wheel', '--wheelhouse', '--checksums', '--sha256',
                   '--all', '--user', '--system', '--output', '--download-deps',
                   '--platform', '--python-version', '--implementation')}
    if command not in ('bootstrap', 'update'):
        aliases.pop('-o', None)
    if command not in ('rerun', 'recover'):
        aliases.pop('-j', None)
        aliases.pop('-jobs', None)
    existing = set(OPTION_ALIASES) | set(aliases) | {
        '-sub', '-seed', '-scheduler', '-queue', '-q', '-l', '-jobs', '-j',
        '-o', '-h', '--help', '--caselist', '--queue', '-rl', '--regr-list', '-rebuild', '--rebuild',
    }
    attached = {key: value for key, value in aliases.items()
                if len(key) == 2 and key not in ('-n', '-J', '-V')}
    for position in range(index + (0 if command.startswith('-') else 1), len(args)):
        token = args[position]
        if token == '--':
            break
        option, separator, value = token.partition('=')
        if option in aliases:
            args[position] = aliases[option] + (separator + value if separator else '')
        elif option not in existing and token[:2] in attached:
            args[position] = attached[token[:2]] + '=' + token[2:]
    return args
