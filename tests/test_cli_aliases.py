"""Short spellings preserve plans, dispatch, and existing argument constraints."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from nsim.cli import build_parser
from nsim.cli_aliases import runtime_arguments
from test_regression import Fixture


ROOT = Path(__file__).resolve().parent.parent


class AliasParsingTests(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def same(self, long, short):
        self.assertEqual(vars(self.parser.parse_args(long)), vars(self.parser.parse_args(short)))

    def test_project_configuration_aliases(self):
        self.same(['bootstrap', '--name', 'verification', '--verification-root', '/work/verification',
                   '--rtl-root', '/work/rtl', '--rtl-filelist', '/work/rtl/rtl.f',
                   '--verification-filelist', '/work/verification/tb.f', '--nsim-version', '0.1.0',
                   '--output-root', '/out', '--output-root', '/backup', '--json'],
                  ['init', '-N', 'verification', '-vr', '/work/verification', '-rtl', '/work/rtl',
                   '-rf', '/work/rtl/rtl.f', '-vf', '/work/verification/tb.f', '-nv', '0.1.0',
                   '-o', '/out', '-o', '/backup', '-J'])
        self.same(['update', '--project', 'demo', '--nsim-version', '0.1.0', '--output-root', '/out'],
                  ['up', '-p', 'demo', '-nv', '0.1.0', '-o', '/out'])
        for long, short in [('list', 'ls'), ('status', 'st'), ('version', 'ver'), ('capabilities', 'caps')]:
            self.same([long, '--json'], [short, '-J'])
        self.same(['switch', '0.1.0', '--project', 'demo'], ['use', '0.1.0', '-pdemo'])
        self.same(['project', 'list', '/work'], ['proj', 'ls', '/work'])
        self.same(['project', 'inspect', '/work'], ['proj', 'inspect', '/work'])

    def test_execution_and_resource_aliases(self):
        for stage in ('com', 'sim', 'run', 'regr'):
            long = [stage, '--project', '/project', '--submit', 'rtl', 'debug', '-mode', 'rtl',
                    '-dir', 'nightly', '-com-dir', 'build', '-sim-dir', 'results',
                    '--jobs', '4', '--retries', '2', '--retry-seconds', '30',
                    '--min-free-bytes', '4096', '--min-free-inodes', '10', '--dry-run',
                    '--scheduler', 'lsf', '--resource', 'rusage[mem=4096]', '--license', 'vcs=1']
            short = [stage, '-p', '/project', '-s', 'rtl', 'debug', '-m', 'rtl', '-d', 'nightly',
                     '-cd', 'build', '-sd', 'results', '-j', '4', '-retry', '2', '-rt', '30',
                     '-fb', '4096', '-fi', '10', '-n', '-sched', 'lsf', '-R', 'rusage[mem=4096]', '-lic', 'vcs=1']
            if stage == 'regr':
                long += ['--caselist', 'smoke.list', '--seed', '42', '43']
                short += ['-l', 'smoke.list', '-S', '42', '43']
            elif stage in ('run', 'sim'):
                long += ['--case', 'smoke', 'fast', '--seed', '42', '43']
                short += ['-c', 'smoke', 'fast', '-S', '42', '43']
            with self.subTest(stage=stage):
                self.same(long, short)
        self.same(['run', '-sub', 'rtl', '--case', 'smoke', '--seed', '42'],
                  ['run', '-s', 'rtl', '-c', 'smoke', '-S42'])
        self.same(['regr', '-sub', 'rtl', '-l', 'smoke.list', '-repeat', '3'],
                  ['regr', '-s', 'rtl', '-l', 'smoke.list', '-r', '3'])
        self.same(['run', '-sub', 'rtl', '--case', 'smoke', '-mode', 'rtl', '-dir', 'debug',
                   '-com-dir', 'build', '-sim-dir', 'results'],
                  ['run', '-s', 'rtl', '-c', 'smoke', '--mode', 'rtl', '--dir', 'debug',
                   '--com-dir', 'build', '--sim-dir', 'results'])

    def test_submit_replay_and_storage_aliases(self):
        self.same(['submit', 'new', 'init', '--extends', 'common/rtl.json', '--mode', 'rtl', '--project', 'demo'],
                  ['sub', 'new', 'init', '-e', 'common/rtl.json', '-m', 'rtl', '-p', 'demo'])
        for action in ('show', 'check'):
            self.same(['submit', action, 'init', '--project', 'demo'], ['sub', action, 'init', '-p', 'demo'])
        for long, short in [('rerun', 'rr'), ('recover', 'rc')]:
            self.same([long, '/record', '--jobs', '3', '--retries', '2', '--retry-seconds', '20'],
                      [short, '/record', '-j3', '-retry', '2', '-rt', '20'])
        self.same(['recover', '/record', '--seed', '42'], ['rc', '/record', '-S42'])
        self.same(['rerun', '/record', '--rebuild'], ['rr', '/record', '-rebuild'])
        self.same(['report', '/record', '--json', '--rebuild'], ['rpt', '/record', '-J', '--rebuild'])
        self.same(['storage', 'check', '/out', '--min-free-bytes', '10', '--min-free-inodes', '2'],
                  ['disk', 'check', '/out', '-fb', '10', '-fi', '2'])

    def test_mixed_spellings_share_actions_and_constraints(self):
        args = self.parser.parse_args(['run', '-s', 'rtl', '--submit', 'debug', '-c', 'smoke',
                                       '-S', '42', '--seed', '43', '-p', 'old', '--project', 'new'])
        self.assertEqual(args.submit, [Path('rtl'), Path('debug')])
        self.assertEqual(args.case, ['smoke'])
        self.assertEqual(args.seed, [42, 43])
        self.assertEqual(args.project, Path('new'))
        repeated = self.parser.parse_args(['run', '-s', 'rtl', '-c', 'smoke', '--case', 'fast'])
        self.assertEqual(repeated.case, ['smoke', 'fast'])
        for args in (['run', '-s', 'rtl', '-c', 'smoke', '-l', 'smoke.list'],
                     ['sim', '-s', 'rtl', '-l', 'smoke.list'],
                     ['run', '-s', 'rtl'], ['sim', '-s', 'rtl'],
                     ['regr', '-s', 'rtl', '-l', 'smoke.list', '-S', '42', '-r', '2'],
                     ['com', '-s', 'rtl', '-S', '42'], ['run', '-c', 'smoke']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                self.parser.parse_args(args)
            self.assertEqual(exc.exception.code, 2)

    def test_runtime_expansion_preserves_values_and_legacy_spellings(self):
        self.assertEqual(runtime_arguments(['sub', 'new', 'init', '-e', 'common/rtl.json', '-p=/project']),
                         ['submit', 'new', 'init', '--extends', 'common/rtl.json', '--project=/project'])
        original = ['run', '-sub', 'rtl', '-seed', '42', '-sim-dir', 'sim', '-scheduler', 'local',
                    '-o', '/out', '-j4', '--case=-n', '--', '-p']
        self.assertEqual(runtime_arguments(original), original)
        self.assertEqual(runtime_arguments(['-o/out', 'rr', '/record', '-j4', '-S42']),
                         ['-o/out', 'rerun', '/record', '--jobs=4', '--seed=42'])
        self.assertEqual(runtime_arguments(['run', '-sd=results', '-p/project', '-c', 'init', '-n']),
                         ['run', '-sim-dir=results', '--project=/project', '--case', 'init', '--dry-run'])
        self.assertEqual(runtime_arguments(['-V']), ['--version'])


class AliasWorkflowTests(Fixture):
    def test_short_execution_reaches_the_same_plan_and_preview(self):
        commands = [
            ['run', '--project', str(self.project), '--submit', str(self.submit), '--case', 'smoke',
             '--seed', '42', '-mode', 'rtl', '-dir', 'debug', '-com-dir', 'build', '-sim-dir', 'results',
             '--jobs', '2', '--dry-run', '--output-root', str(self.out)],
            ['run', '-p', str(self.project), '-s', str(self.submit), '-c', 'smoke', '-S42', '-m', 'rtl',
             '-d', 'debug', '-cd', 'build', '-sd', 'results', '-j2', '-n', '-o', str(self.out)],
        ]
        plans = []
        for command in commands:
            result = subprocess.run([sys.executable, '-m', 'nsim', *command], cwd=self.base,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertTrue(output['dry_run'])
            record = Path(output['record_dir'])
            self.assertTrue((record / 'commands.json').is_file())
            plans.append(json.loads((record / 'plan.json').read_text()))
            # Each preview publishes its own record; execution settings match.
            plans[-1]['policy'].pop('record_dir')
        for key in ('execution', 'tasks', 'seed_selection', 'policy', 'submits', 'project', 'input_hashes'):
            self.assertEqual(plans[0][key], plans[1][key], key)
        self.assertEqual(plans[1]['execution']['workspace'], 'debug')
        self.assertEqual([(t['case'], t['seed']) for t in plans[1]['tasks']], [('smoke', 42)])
        self.assertFalse((self.out / 'base').exists())


class LauncherAliasTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('alias_installer', ROOT / 'nsim-install.py')
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def test_embedded_definitions_and_install_options(self):
        self.assertEqual(self.installer.CLI_ALIASES_SOURCE, (ROOT / 'src/nsim/cli_aliases.py').read_text())
        parser = self.installer.parser_for(self.base)
        pairs = [(['install', '--wheel', 'nsim.whl', '--wheelhouse', 'deps', '--checksums', 'sums', '--user'],
                  ['inst', '-w', 'nsim.whl', '-wh', 'deps', '-sum', 'sums', '-u']),
                 (['install', '--bundle', 'nsim.tar.gz', '--sha256', 'abc', '--system'],
                  ['inst', '-b', 'nsim.tar.gz', '-sha', 'abc', '-sys']),
                 (['versions', '--remote'], ['vers', '--remote']),
                 (['bundle', 'create', '--wheel', 'nsim.whl', '--output', 'bundle.tar.gz', '--download-deps',
                   '--platform', 'manylinux2014_x86_64', '--python-version', '39', '--implementation', 'cp', '--abi', 'cp39'],
                  ['pack', 'create', '-w', 'nsim.whl', '-o', 'bundle.tar.gz', '-deps',
                   '-plat', 'manylinux2014_x86_64', '-py', '39', '-impl', 'cp', '--abi', 'cp39'])]
        for long, short in pairs:
            with self.subTest(short=short):
                self.assertEqual(vars(parser.parse_args(long)), vars(parser.parse_args(short)))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['inst', '-u', '-sys', '-b', 'bundle.tar.gz'])

    def test_project_selection_and_historical_version_dispatch(self):
        for name, version in [('first', '0.1.0'), ('second', '0.2.0')]:
            folder = self.base / name
            folder.mkdir()
            (folder / 'nsim.toml').write_text('schema_version = 1\nname = "demo"\n')
            (folder / '.nsim_version').write_text(version + '\n')
        first, second = map(str, (self.base / 'first', self.base / 'second'))
        for options in (['-p', first], ['-p=' + first], ['-p' + first], ['--project', second, '-p', first],
                        ['-p', second, '--project=' + first]):
            with self.subTest(options=options):
                self.assertEqual(self.installer.selected_version(['run', *options], self.base), '0.1.0')
        record = self.base / 'record'
        record.mkdir()
        (record / 'plan.json').write_text(json.dumps({'runtime': {'version': '0.1.0'},
                                                    'project': {'root': first}}))
        for args in (['rr', str(record), '-j4'], ['rc', str(record), '-S42'], ['rpt', str(record), '-J'],
                     ['-o', '/out', 'rr', str(record)], ['rr', '-j4', str(record)],
                     ['rc', '-S', '42', '-rt', '30', str(record)], ['rpt', '-J', '--rebuild', str(record)]):
            with self.subTest(args=args), patch.object(self.installer, 'receipt', return_value={'wheel_sha256': 'a' * 64}), patch.object(self.installer.os, 'execve') as execute:
                self.installer.launch(self.base, args)
                executable, actual, _ = execute.call_args.args
                self.assertIn('versions/0.1.0', str(executable))
                self.assertEqual(actual[1:], runtime_arguments(args))

    def test_launcher_works_without_an_installed_runtime_for_queries(self):
        root = self.base / 'tools'
        self.installer.bootstrap(root)
        environment = dict(os.environ, XDG_CONFIG_HOME=str(self.base / 'config'))
        result = subprocess.run([str(root / 'bin/nsim'), 'ls', '-J'], env=environment,
                                cwd=self.base, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])
        result = subprocess.run([str(root / 'bin/nsim'), 'vers'], env=environment,
                                cwd=self.base, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'installed: none')


if __name__ == '__main__':
    unittest.main()
