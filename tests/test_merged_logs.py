"""Stage transcripts retain stderr, hooks, native logs and final evidence."""

import json
from pathlib import Path
import sys

from test_regression import Fixture
from nsim.planning import create_plan
from nsim.reports import load_final
from nsim.service import run
from nsim.storage import write_json


SCRIPT = r'''import json, os, sys, time
from pathlib import Path
mode, *args = sys.argv[1:]
for fd, suffix in ((1, 'out1'), (2, 'err1'), (1, 'out2'), (2, 'err2')):
    os.write(fd, (mode + ':' + suffix + '\n').encode())
if mode == 'compile':
    Path('model').write_text('compiled')
    text = 'Error-[BUILD] compiler failure\n' if args else 'compile native log\n'
    Path('vcs.log').write_text(text)
    if args:
        os.write(2, text.encode())
elif mode == 'sim':
    case, seed, result = args
    text = 'Error-[SIM] simulation failure\n' if case == 'bad' else 'simulation native log\n'
    Path('sim.log').write_text(text)
    if case == 'bad':
        os.write(2, text.encode())
    if case == 'slow':
        time.sleep(5)
    Path(result).write_text(json.dumps({'schema_version': 1, 'case': case, 'seed': int(seed),
        'complete': True, 'checks': {'functional': {'executed': True, 'passed': True}}}))
elif mode == 'analyze':
    Path(args[0]).write_text(json.dumps({'schema_version': 1, 'complete': True,
        'counts': {'warning': 0, 'error': 0, 'fatal': 0}, 'diagnostics': []}))
elif mode == 'fail':
    sys.exit(3)
'''


class MergedLogTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.project / 'workflow.py').write_text(SCRIPT)
        self.descriptor.write_text('schema_version=1\nname="demo"\ninputs=["workflow.py"]\n')
        self.data.update(adapter='command', required_checks=['functional'],
                         compile={'argv': [sys.executable, '{project}/workflow.py', 'compile'], 'artifacts': ['model']},
                         argv=[sys.executable, '{project}/workflow.py', 'sim', '{case}', '{seed}', '{result}'],
                         hooks={stage: self.hook(stage) for stage in ('pre_compile', 'post_compile', 'pre_sim', 'post_sim')})

    def hook(self, mode, name=None, *args):
        return {'name': name or mode, 'argv': [sys.executable, '{project}/workflow.py', mode, *args]}

    def execute(self, case='good'):
        self.save_submit()
        plan = create_plan(self.project, cases=[case], submit_paths=[self.submit], seeds=[42])
        return run(plan, [self.out])

    def assert_transcript(self, directory, filename, modes, scheduler=False):
        lines = (directory / filename).read_text().splitlines()
        if scheduler:
            lines = [line for line in lines if any(line.startswith(mode + ':') for mode in modes)]
        self.assertEqual(lines, [mode + ':' + suffix for mode in modes
                                 for suffix in ('out1', 'err1', 'out2', 'err2')])
        for hook in json.loads((directory / 'hooks.json').read_text())['hooks']:
            if hook['kind'] == 'command':
                self.assertEqual(hook['stdout'], str(directory / filename))
                self.assertEqual(hook['stderr'], hook['stdout'])
                self.assertGreater(hook['log_end_byte'], hook['log_start_byte'])

    def test_ordered_streams_and_hooks_share_one_log_per_stage(self):
        record, state = self.execute()
        self.assertTrue(state['passed'], state)
        attempt = state['tasks'][0]['attempts'][0]
        build = Path(attempt['build_dir'])
        self.assert_transcript(record, 'run.log', ['pre_sim', 'sim', 'post_sim'])
        self.assert_transcript(build, 'compile.log', ['pre_compile', 'compile', 'post_compile'])
        self.assertFalse((record / 'launch.log').exists())
        self.assertFalse((build / 'compile.err').exists())
        self.assertFalse((record / 'hooks').exists())
        self.assertIn(str(record / 'sim.log'), attempt['evidence'])
        self.assertIn(str(build / 'vcs.log'), attempt['evidence'])
        self.assertTrue(load_final(record)[1]['passed'])
        (record / 'sim.log').write_text('changed native evidence')
        self.assertEqual(load_final(record)[1]['tasks'][0]['effective_verdict'], 'INCOMPLETE')

    def test_stderr_and_native_duplicate_are_counted_once(self):
        record, state = self.execute('bad')
        attempt = state['tasks'][0]['attempts'][0]
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'FAIL')
        self.assertEqual(attempt['log_analysis']['counts']['error'], 1)
        self.assertEqual((record / 'run.log').read_text().count('Error-[SIM]'), 1)

    def test_compile_stderr_and_native_duplicate_are_counted_once(self):
        self.data['compile']['argv'].append('bad')
        _, state = self.execute()
        attempt = state['tasks'][0]['attempts'][0]
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'COMPILE_FAIL')
        self.assertEqual(attempt['compile_log_analysis']['counts']['error'], 1)
        self.assertIn('compiler failure', (Path(attempt['build_dir']) / 'compile.log').read_text())

    def test_custom_analyzer_output_is_merged_after_stability_check(self):
        self.data['hooks']['post_sim'] = [self.hook('post_sim'), self.hook('analyze', 'log_analysis', '{analysis}')]
        record, state = self.execute()
        self.assertTrue(state['passed'], state)
        self.assert_transcript(record, 'run.log', ['pre_sim', 'sim', 'post_sim', 'analyze'])
        self.assertFalse((record / '.nsim-analysis-output.log').exists())
        self.assertTrue(load_final(record)[1]['passed'])

    def test_explicit_legacy_log_lists_remain_compatible_without_duplicates(self):
        self.data['log_analysis'] = {'files': ['run.log', 'launch.log'],
                                     'compile_files': ['compile.log', 'compile.err']}
        record, state = self.execute()
        self.assertTrue(state['passed'], state)
        build = Path(state['tasks'][0]['attempts'][0]['build_dir'])
        self.assertEqual((record / 'launch.log').read_bytes(), b'')
        self.assertEqual((build / 'compile.err').read_bytes(), b'')
        self.assert_transcript(record, 'run.log', ['pre_sim', 'sim', 'post_sim'])

    def test_pre_hook_failure_retains_output_and_cleanup(self):
        self.data['hooks']['pre_sim'] = self.hook('fail')
        record, state = self.execute()
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'HOOK_FAIL')
        self.assert_transcript(record, 'run.log', ['fail', 'post_sim'])
        self.assertFalse((record / 'result.json').exists())

    def test_timeout_retains_main_and_post_hook_output(self):
        self.data['timeout_seconds'] = .3
        record, state = self.execute('slow')
        self.assertEqual(state['tasks'][0]['effective_verdict'], 'TIMEOUT')
        text = (record / 'run.log').read_text()
        for marker in ('pre_sim:err2', 'sim:err2', '[nsim] command timed out', 'post_sim:err2'):
            self.assertIn(marker, text)

    def test_lsf_worker_uses_the_same_merged_transcripts(self):
        farm = self.base / 'farm'
        farm.mkdir()
        write_json(farm / 'state.json', {'jobs': {}, 'events': [], 'peak': 0})
        stub = Path(__file__).with_name('lsf_stub.py')
        scheduler = {'kind': 'lsf', 'poll_seconds': .02, 'unknown_seconds': 3,
                     'cancel_seconds': 3, 'deadline_seconds': 20}
        for tool in ('bsub', 'bjobs', 'bkill'):
            executable = farm / tool
            executable.write_text('#!' + sys.executable + '\nimport runpy,sys\nsys.argv = ' +
                                  repr([str(stub), tool, str(farm)]) + ' + sys.argv[1:]\nrunpy.run_path(' +
                                  repr(str(stub)) + ', run_name="__main__")\n')
            executable.chmod(0o755)
            scheduler[tool] = str(executable)
        self.data['scheduler'] = scheduler
        record, state = self.execute()
        self.assertTrue(state['passed'], state)
        self.assert_transcript(record, 'run.log', ['pre_sim', 'sim', 'post_sim'], scheduler=True)
        build = Path(state['tasks'][0]['attempts'][0]['build_dir'])
        self.assert_transcript(build, 'compile.log', ['pre_compile', 'compile', 'post_compile'], scheduler=True)
        for directory, filename in ((record, 'run.log'), (build, 'compile.log')):
            text = (directory / filename).read_text()
            self.assertIn('Job <', text)
            self.assertIn('final scheduler state', text)
            self.assertIn('worker result', text)
            self.assertIn('BEGIN farm.err', text)
        self.assertEqual(len(json.loads((farm / 'state.json').read_text())['jobs']), 2)
        self.assertTrue(load_final(record)[1]['passed'])
