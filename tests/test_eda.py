"""EDA previews validated without commercial tools or a private checkout."""
import json
from pathlib import Path
from unittest.mock import patch

from test_regression import Fixture
from nsim.config import ConfigError
from nsim.eda import commands
from nsim.planning import check_identity
from nsim.service import run
from nsim.storage import IntegrityError


class EdaPreviewTests(Fixture):
    def setUp(self):
        super().setUp()
        (self.project / 'src').mkdir()
        (self.project / 'lists').mkdir()
        (self.project / 'src' / 'dut.sv').write_text('module dut; endmodule\n')
        (self.project / 'src' / 'defs.svh').write_text('`define N 1\n')
        (self.project / 'top.f').write_text('-f lists/rtl.f\n-top dut\n')
        (self.project / 'lists' / 'rtl.f').write_text('+incdir+src\nsrc/dut.sv\n')
        self.data = {'schema_version': 1, 'name': 'native', 'adapter': 'vcs', 'required_checks': ['liveness'],
                     'vcs': {'filelist': 'top.f', 'executable': 'nsim-nonexistent-vcs-tool',
                             'compile_args': ['-full64'], 'fsdb': True, 'fsdb_file': 'acc.fsdb', 'fsdb_plusargs': ['+fsdb']},
                     'scheduler': {'kind': 'lsf', 'bsub': 'nsim-missing-bsub', 'bjobs': 'nsim-missing-bjobs', 'bkill': 'nsim-missing-bkill'}}
        self.save_submit()

    def test_preview_resolves_sources_and_fixed_wave_without_execution(self):
        plan = self.plan(allow_missing_tools=True)
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')):
            journal, state = run(plan, [self.out], self.events, dry_run=True)
        preview = json.loads((journal / 'commands.json').read_text())
        task = preview['tasks'][0]
        self.assertFalse(preview['executed'])
        self.assertFalse(state['passed'])
        self.assertFalse((journal / 'final.json').exists())
        self.assertIn(str(self.project / 'src' / 'dut.sv'), task['compile']['argv'])
        self.assertIn('+ntb_random_seed=42', task['simulate'])
        self.assertEqual(task['wave'], str(Path(task['cwd']) / 'acc.fsdb'))
        self.assertEqual(task['verdi']['argv'][-1], task['wave'])
        self.assertIn(str(self.project / 'src' / 'defs.svh'), plan['input_hashes'])
        self.assertEqual(len(task['unresolved_tools']), 4)
        self.assertNotIn('-q', task['scheduler']['submit_argv'])

    def test_native_execution_rejected_before_journal_creation(self):
        plan = self.plan(allow_missing_tools=True)
        with self.assertRaisesRegex(ConfigError, 'unresolved tools'):
            run(plan, [self.out], self.events)
        self.assertEqual(list(self.events.iterdir()), [])

    def test_real_plan_requires_tools(self):
        with self.assertRaisesRegex(ConfigError, 'executable unavailable'):
            self.plan()

    def test_cycle_missing_dependency_and_unknown_option_rejected(self):
        for content in ('-f top.f', 'absent.sv', '-unsupported'):
            (self.project / 'top.f').write_text(content)
            with self.assertRaises(ConfigError):
                self.plan(allow_missing_tools=True)

    def test_F_relative_semantics_and_header_identity(self):
        (self.project / 'top.f').write_text('-F lists/rtl.f')
        (self.project / 'lists' / 'rtl.f').write_text('../src/dut.sv\n+incdir+../src')
        plan = self.plan(allow_missing_tools=True)
        (self.project / 'src' / 'defs.svh').write_text('changed')
        with self.assertRaises(IntegrityError):
            check_identity(plan)

    def test_explicit_filelist_environment(self):
        (self.project / 'top.f').write_text('$RTL/dut.sv')
        with self.assertRaisesRegex(ConfigError, 'not declared'):
            self.plan(allow_missing_tools=True)
        self.data['env'] = {'RTL': 'src'}
        self.save_submit()
        self.assertIn(str(self.project / 'src' / 'dut.sv'), self.plan(allow_missing_tools=True)['input_hashes'])

    def test_scheduler_precedence_and_license_rendering(self):
        plan = self.plan(allow_missing_tools=True, scheduler_overrides={'queue': 'custom', 'resources': ['select[mem>1024]'], 'licenses': {'vcs': 2}})
        cmd = commands(plan, plan['tasks'][0], self.out)['scheduler']['submit_argv']
        self.assertEqual(cmd[cmd.index('-q') + 1], 'custom')
        self.assertIn('rusage[vcs=2]', cmd)
        self.assertIn('select[mem>1024]', cmd)

    def test_dpi_scopes_only_plan_never_create_source_cache(self):
        (self.project / 'Makefile.dpi').write_text('all:\n\tfalse\n')
        for scope in ('project', 'regression', 'run'):
            self.data['vcs']['dpi'] = {'makefile': 'Makefile.dpi', 'scope': scope}
            self.save_submit()
            plan = self.plan(allow_missing_tools=True)
            preview = commands(plan, plan['tasks'][0], self.out)
            self.assertEqual(preview['dpi']['scope'], scope)
            self.assertIn('NSIM_DPI_OUTPUT=' + preview['dpi']['library'], preview['dpi']['argv'])
            self.assertFalse(Path(preview['dpi']['cwd']).exists())

    def test_golden_and_templates_strict(self):
        self.data['required_checks'] = ['golden']
        self.save_submit()
        with self.assertRaisesRegex(ConfigError, 'golden_enabled'):
            self.plan(allow_missing_tools=True)
        self.data['vcs']['golden_enabled'] = True
        self.data['vcs']['compile_args'] = ['{case.__class__}']
        self.save_submit()
        with self.assertRaises(ConfigError):
            self.plan(allow_missing_tools=True)

    def test_external_descriptor_uses_explicit_source_root(self):
        profile = self.base / 'profile'
        profile.mkdir()
        descriptor = profile / 'nsim.toml'
        descriptor.write_text('schema_version=1\nname="overlay"\nsource_root="../project"\n')
        from nsim.planning import create_plan
        plan = create_plan(descriptor, cases=['smoke'], submit_paths=[self.submit], seeds=[42], allow_missing_tools=True)
        self.assertEqual(plan['project']['root'], str(self.project))
