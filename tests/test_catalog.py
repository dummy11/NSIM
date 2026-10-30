"""Name resolution stays deterministic across layouts, working directories and replay."""

from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from test_regression import Fixture
from nsim.catalog import config, resolve, toml
from nsim.cli import build_parser
from nsim.batch import prepare_batch
from nsim.config import ConfigError, load_project
from nsim.planning import create_plan
from nsim.storage import write_json


class CatalogTests(Fixture):
    def setUp(self):
        super().setUp()
        self.flows = self.project / 'custom flows'
        self.suites = self.project / 'plans'
        self.shared = self.base / 'shared'
        for directory in (self.flows, self.suites, self.shared):
            directory.mkdir()
        write_json(self.flows / 'rtl.json', self.data)
        (self.suites / 'smoke.list').write_text('smoke\n')
        self.mapping = {'submit': {'roots': {'local': 'custom flows', 'shared': '../shared'},
                                   'write_root': 'local', 'templates': {'rtl': 'templates/base.json'},
                                   'aliases': {'debug': 'custom flows/rtl.json'}},
                        'caselist': {'roots': {'local': 'plans'}},
                        'regrlist': {'aliases': {'nightly': 'plans/nightly.txt'}}}
        self.save_mapping()

    def save_mapping(self):
        self.descriptor.write_text('schema_version = 1\nname = "demo"\ninputs = ["input.txt"]\n' + toml(self.mapping))

    def cli(self, *args, cwd=None, code=0):
        result = subprocess.run([sys.executable, '-m', 'nsim', *map(str, args)], cwd=cwd or self.base,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return json.loads(result.stdout) if code == 0 else result.stderr

    def test_names_resolve_from_project_even_with_cwd_shadow_and_keep_case_name(self):
        (self.base / 'rtl').write_text('wrong')
        (self.base / 'rtl.json').write_text('wrong')
        with patch('pathlib.Path.cwd', return_value=self.base):
            plan = create_plan(self.project, cases=['unregistered_case'], submit_paths=['rtl'], seeds=[42])
        self.assertEqual(plan['tasks'][0]['case'], 'unregistered_case')
        self.assertEqual(plan['references'][0]['path'], str(self.flows / 'rtl.json'))
        self.assertEqual(plan['references'][0]['via'], 'local:rtl')
        self.assertEqual(plan['submits'][0]['source'], str(self.flows / 'rtl.json'))
        self.assertIsNone(plan['case_definitions']['unregistered_case'][0]['config'])
        result = self.cli('catalog', 'resolve', 'submit', 'rtl', '--project', self.project)
        self.assertEqual(result['path'], str(self.flows / 'rtl.json'))

    def test_explicit_cli_path_survives_dot_slash_and_does_not_fall_back(self):
        write_json(self.base / 'rtl.json', dict(self.data, name='explicit'))
        explicit = self.cli('submit', 'show', './rtl.json', '--project', self.project)
        named = self.cli('submit', 'show', 'rtl.json', '--project', self.project)
        self.assertEqual(explicit['effective']['name'], 'explicit')
        self.assertEqual(named['effective']['name'], 'base')
        self.assertIn('file not found', self.cli('submit', 'check', './debug', '--project', self.project, code=2))

    def test_ambiguity_requires_namespace_and_alias_is_explicit_selection(self):
        write_json(self.shared / 'rtl.json', dict(self.data, name='shared'))
        project = load_project(self.project)
        with self.assertRaisesRegex(ConfigError, 'ambiguous submit'):
            resolve(project, 'submit', 'rtl')
        self.assertEqual(resolve(project, 'submit', 'shared:rtl')[0], self.shared / 'rtl.json')
        self.assertEqual(resolve(project, 'submit', 'debug')[0], self.flows / 'rtl.json')
        for value in ('unknown:rtl', 'local:../submit', 'local:/submit'):
            with self.assertRaises(ConfigError):
                resolve(project, 'submit', value)

    def test_multiple_list_suffixes_are_ambiguous_until_exact_suffix_selected(self):
        (self.suites / 'smoke.caselist').write_text('different\n')
        project = load_project(self.project)
        with self.assertRaisesRegex(ConfigError, 'ambiguous caselist'):
            resolve(project, 'caselist', 'smoke')
        self.assertEqual(resolve(project, 'caselist', 'local:smoke.list')[0], self.suites / 'smoke.list')

    def test_qualified_nested_paths_and_no_recursive_basename_search(self):
        (self.flows / 'nested').mkdir()
        write_json(self.flows / 'nested' / 'special.json', self.data)
        project = load_project(self.project)
        self.assertEqual(resolve(project, 'submit', 'local:nested/special')[0], self.flows / 'nested/special.json')
        with self.assertRaisesRegex(ConfigError, 'not found'):
            resolve(project, 'submit', 'special')

    def test_no_hidden_default_directory_or_cwd_fallback(self):
        (self.project / 'submit').mkdir()
        write_json(self.project / 'submit' / 'old.json', self.data)
        project = replace(load_project(self.project), catalog={})
        with self.assertRaisesRegex(ConfigError, 'configure catalog.submit'):
            resolve(project, 'submit', 'old')
        self.assertEqual(resolve(project, 'submit', self.submit)[0], self.submit)

    def test_broken_alias_does_not_fall_through_to_directory(self):
        self.mapping['submit']['aliases']['rtl'] = 'missing.json'
        self.save_mapping()
        with self.assertRaisesRegex(ConfigError, 'file not found'):
            resolve(load_project(self.project), 'submit', 'rtl')

    def test_batch_and_rows_resolve_using_project_not_list_or_cwd(self):
        (self.suites / 'nightly.txt').write_text('-sub debug -l smoke -seed 42 -scheduler local\n')
        args = build_parser().parse_args(['regr', '--project', str(self.project), '-rl', 'nightly',
                                          '-o', str(self.out), '--dry-run'])
        batch, entries = prepare_batch(args)
        self.assertEqual(batch['reference']['reference'], 'nightly')
        self.assertEqual(batch['list'], str(self.suites / 'nightly.txt'))
        self.assertEqual(entries[0]['plan']['caselists'], [str(self.suites / 'smoke.list')])
        self.assertEqual([r['reference'] for r in entries[0]['plan']['references']], ['debug', 'smoke'])

    def test_catalog_mapping_is_in_plan_snapshot_and_only_selected_files_are_inputs(self):
        (self.suites / 'unused.list').write_text('unused\n')
        plan = create_plan(self.project, caselist='smoke', submit_paths=['debug'], seeds=[42])
        self.assertIn('[catalog.submit.roots]', plan['snapshots'][str(self.descriptor)])
        self.assertNotIn(str(self.suites / 'unused.list'), plan['input_hashes'])
        self.assertIn(str(self.flows / 'rtl.json'), plan['input_hashes'])

    def test_authoring_uses_configured_destination_and_mode_template(self):
        with patch.dict(os.environ, XDG_CONFIG_HOME=str(self.base / 'registry')):
            created = self.cli('submit', 'new', 'new_one', '--project', self.project)
        self.assertEqual(created['path'], str(self.flows / 'new_one.json'))
        self.assertEqual(created['parent'], str(self.project / 'templates/base.json'))
        inspected = self.cli('submit', 'show', 'new_one', '--project', self.project)
        self.assertEqual(inspected['effective']['name'], 'new_one')
        self.assertFalse((self.project / 'submit').exists())

    def test_listing_shows_aliases_namespaces_and_unavailable_aliases(self):
        self.mapping['submit']['aliases']['missing'] = 'gone.json'
        self.save_mapping()
        result = self.cli('catalog', 'list', 'submit', '--project', self.project)
        entries = {row['reference']: row for row in result['entries']}
        self.assertIn('local:rtl.json', entries)
        self.assertTrue(entries['debug']['exists'])
        self.assertFalse(entries['missing']['exists'])

    def test_invalid_tables_fail_before_execution(self):
        for value in ([], {'case': {}}, {'submit': {'roots': []}}, {'submit': {'aliases': {'bad:name': 'x'}}},
                      {'submit': {'roots': {'local': ''}}}, {'submit': {'write_root': 'absent'}},
                      {'caselist': {'templates': {}}}, {'submit': {'templates': {'badmode': 'x'}}}):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                config(value)

    def test_aliases_can_point_to_external_arbitrarily_named_files(self):
        external = self.shared / 'a custom file.anything'
        write_json(external, self.data)
        self.mapping['submit']['aliases']['external'] = str(external)
        self.save_mapping()
        self.assertEqual(resolve(load_project(self.project), 'submit', 'external')[0], external)

    def test_replay_remains_bound_to_original_file_after_alias_changes(self):
        from nsim.service import run, replay
        from nsim.reports import load_final
        plan = create_plan(self.project, cases=['smoke'], submit_paths=['debug'], seeds=[42])
        record, state = run(plan, [self.out])
        self.assertTrue(state['passed'])
        write_json(self.shared / 'different.json', dict(self.data, plusargs=['+REDIRECTED=1']))
        self.mapping['submit']['aliases']['debug'] = '../shared/different.json'
        self.save_mapping()
        repeated, state = replay(record, rebuild=True)
        self.assertTrue(state['passed'])
        actual, _ = load_final(repeated)
        self.assertEqual(actual['submits'][0]['source'], str(self.flows / 'rtl.json'))
        self.assertNotIn('+REDIRECTED=1', actual['tasks'][0]['case_config']['argv'])

    def test_descriptor_change_during_resolution_cannot_publish_mixed_plan(self):
        from nsim.catalog import resolve_many
        def changed(*args):
            resolved = resolve_many(*args)
            with self.descriptor.open('a') as stream:
                stream.write('\n# concurrent configuration edit\n')
            return resolved
        with patch('nsim.catalog.resolve_many', side_effect=changed), self.assertRaisesRegex(ConfigError, 'changed during'):
            create_plan(self.project, cases=['smoke'], submit_paths=['debug'], seeds=[42])
