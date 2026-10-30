"""Exercise the standalone, offline installer and stable launcher."""

import base64
import hashlib
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
import zipfile


SCRIPT = Path(__file__).resolve().parent.parent / "nsim-install.py"


def test_wheel(folder, version):
    """Make a minimal, dependency-free wheel with the real nsim CLI entry name."""
    path = folder / f"nsim-{version}-py3-none-any.whl"
    metadata = f"nsim-{version}.dist-info"
    members = {
        "nsim/__init__.py": "",
        "nsim/cli.py": ("import sys\n"
                        f"def main():\n    print('nsim {version}' if sys.argv[1:] == ['--version'] "
                        f"else '{version} ' + ' '.join(sys.argv[1:]))\n"),
        f"{metadata}/METADATA": f"Metadata-Version: 2.1\nName: nsim\nVersion: {version}\n",
        f"{metadata}/WHEEL": "Wheel-Version: 1.0\nGenerator: nsim-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{metadata}/entry_points.txt": "[console_scripts]\nnsim = nsim.cli:main\n",
    }
    records = []
    for name, content in members.items():
        blob = content.encode()
        digest = base64.urlsafe_b64encode(hashlib.sha256(blob).digest()).rstrip(b"=").decode()
        records.append(f"{name},sha256={digest},{len(blob)}")
    records.append(f"{metadata}/RECORD,,")
    members[f"{metadata}/RECORD"] = "\n".join(records) + "\n"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "installation"
        self.project = self.base / "project"
        self.project.mkdir()
        (self.project / "nsim.toml").write_text('schema_version = 1\nname = "demo"\n')
        spec = importlib.util.spec_from_file_location('nsim_installer', SCRIPT)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def installer(self, *args, success=True, cwd=None, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=cwd,
                                capture_output=True, text=True, timeout=30, env=env)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def test_shared_launcher_installs_for_current_user(self):
        self.installer('bootstrap', '--root', self.root)
        launcher = self.root / 'bin' / 'nsim'
        original_launcher = launcher.read_bytes()
        home = self.base / 'user-home'
        user_root = home / '.local' / 'share' / 'nsim'
        environment = dict(os.environ, HOME=str(home), NSIM_HOME=str(self.root))
        for version, scope in [('0.1.0', []), ('0.2.0', ['--user'])]:
            wheel = test_wheel(self.base, version)
            result = subprocess.run([launcher, 'install', '--wheel', str(wheel), *scope],
                                    cwd=self.base, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((user_root / 'versions' / version / 'receipt.json').is_file())
        self.assertEqual(list((self.root / 'versions').iterdir()), [])
        self.assertEqual(launcher.read_bytes(), original_launcher)
        result = subprocess.run([user_root / 'bin' / 'nsim', '--version'], cwd=self.base,
                                env=dict(environment, NSIM_VERSION='0.2.0'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'nsim 0.2.0')
        # Ordinary queries continue to use the invoked launcher's own version store.
        result = subprocess.run([launcher, 'versions'], cwd=self.base, env=environment,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), 'installed: none')

    def test_system_bundle_install_and_other_user_launch(self):
        wheel = test_wheel(self.base, '0.1.0')
        bundle = self.base / 'bundle.tar.gz'
        self.installer('bundle', 'create', '--wheel', wheel, '--output', bundle)
        # Redirect the system prefix only in this test; never write to the real /opt.
        shared_root = self.base / 'shared'
        with patch.object(self.module, 'SYSTEM_ROOT', shared_root), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(self.module.main(['install', '--bundle', str(bundle), '--system']), 0)
        self.assertTrue((shared_root / 'versions' / '0.1.0' / 'receipt.json').is_file())
        other_home = self.base / 'other-home'
        result = subprocess.run([shared_root / 'bin' / 'nsim', '--version'], cwd=self.base,
                                env=dict(os.environ, HOME=str(other_home), NSIM_VERSION='0.1.0'),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'nsim 0.1.0')
        self.assertFalse((other_home / '.local' / 'share' / 'nsim').exists())

    def test_install_destinations_apply_to_remote_versions_and_all(self):
        home = self.base / 'user-home'
        user_root = home / '.local' / 'share' / 'nsim'
        destinations = [([], user_root), (['--user'], user_root),
                        (['--system'], Path('/opt/nsim')), (['--root', str(self.root)], self.root)]
        with patch.dict(os.environ, HOME=str(home), NSIM_HOME=str(self.base / 'env-root')):
            for flags, expected in destinations:
                for source, versions in [(['0.1.0'], ['0.1.0']), (['--all'], ['0.1.0', '0.2.0'])]:
                    with self.subTest(flags=flags, source=source), \
                            patch.object(self.module, 'remote_versions', return_value=versions), \
                            patch.object(self.module, 'install_remote') as install:
                        self.assertEqual(self.module.main(['install', *source, *flags]), 0)
                        self.assertEqual([call.args for call in install.call_args_list],
                                         [(expected.resolve(), version) for version in versions])
        self.assertFalse((home / '.local' / 'share' / 'nsim').exists())

    def test_conflicting_install_destinations_fail_before_writing(self):
        wheel = test_wheel(self.base, '0.1.0')
        home = self.base / 'user-home'
        environment = dict(os.environ, HOME=str(home), NSIM_HOME=str(self.root))
        for flags in [('--user', '--system'), ('--user', '--root', self.root),
                      ('--root', self.root, '--system')]:
            with self.subTest(flags=flags):
                result = self.installer('install', '--wheel', wheel, *flags, env=environment, success=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn('not allowed with argument', result.stderr)
        self.assertFalse(self.root.exists())
        self.assertFalse((home / '.local' / 'share' / 'nsim').exists())

    def test_system_permission_failure_does_not_fall_back_to_user_install(self):
        home = self.base / 'user-home'
        wheel = test_wheel(self.base, '0.1.0')
        with patch.dict(os.environ, HOME=str(home)), \
                patch.object(self.module, 'bootstrap', side_effect=PermissionError(13, 'Permission denied', '/opt/nsim')), \
                patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(self.module.main(['install', '--wheel', str(wheel), '--system']), 2)
        self.assertIn('/opt/nsim', error.getvalue())
        self.assertNotIn('Traceback', error.getvalue())
        self.assertFalse((home / '.local' / 'share' / 'nsim').exists())

    def test_offline_install_switch_and_historical_dispatch(self):
        for version in ("0.1.0", "0.2.0"):
            wheel = test_wheel(self.base, version)
            bundle = self.base / f"bundle-{version}.tar.gz"
            self.installer("bundle", "create", "--wheel", wheel, "--output", bundle)
            digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
            if version == "0.1.0":
                self.installer("install", "--bundle", bundle, "--sha256", digest, "--root", self.root)
            else:
                self.installer("update", "--bundle", bundle, "--sha256", digest, "--root", self.root)
        launcher = self.root / "bin" / "nsim"
        self.assertTrue(launcher.is_file())
        self.installer("switch", "0.1.0", "--project", self.project, "--root", self.root)
        self.assertEqual((self.project / ".nsim_version").read_text(), "0.1.0\n")
        result = subprocess.run([launcher, "--version"], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "nsim 0.1.0", result.stderr)
        journal = self.base / "journal"
        journal.mkdir()
        (journal / "plan.json").write_text(json.dumps({"runtime": {"version": "0.1.0"},
                                                    'project': {'root': str(self.project)}}))
        self.installer("switch", "0.2.0", "--project", self.project, "--root", self.root)
        result = subprocess.run([launcher, "--version"], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "nsim 0.2.0", result.stderr)
        result = subprocess.run([launcher, "rerun", str(journal)], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f"0.2.0 rerun {journal}", result.stderr)
        for option in ('--rebuild', '-rebuild'):
            result = subprocess.run([launcher, 'rerun', option, str(journal)], cwd=self.project,
                                    capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), f'0.2.0 rerun {option} {journal}', result.stderr)
        result = subprocess.run([launcher, 'rerun', '-dir', 'debug', '--rebuild', str(journal)],
                                cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f'0.2.0 rerun -dir debug --rebuild {journal}', result.stderr)
        compile_dir = self.base / 'com_dir'
        compile_dir.mkdir()
        (compile_dir / 'build.json').write_text(json.dumps({'record_dir': str(journal)}))
        result = subprocess.run([launcher, 'rerun', str(compile_dir)], cwd=self.project,
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f'0.2.0 rerun {compile_dir}', result.stderr)
        batch = self.base / 'batch'
        batch.mkdir()
        child_plan = {'runtime': {'version': '0.1.0'}, 'project': {'root': str(self.project)}}
        (batch / 'plan.json').write_text(json.dumps({'kind': 'batch', 'entries': [{'plan': child_plan}]}))
        for command, version in (('rerun', '0.2.0'), ('recover', '0.1.0')):
            result = subprocess.run([launcher, command, str(batch)], cwd=self.project,
                                    capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), f'{version} {command} {batch}', result.stderr)
        for prefix in (['-o', str(self.base / 'out')], ['--output-root=' + str(self.base / 'out')]):
            result = subprocess.run([launcher, *prefix, 'rerun', str(journal)], cwd=self.project,
                                    capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), f"0.2.0 {' '.join(prefix)} rerun {journal}", result.stderr)
        simulation = self.base / 'simulation'
        simulation.mkdir()
        (simulation / 'run.json').write_text(json.dumps({'record_dir': str(journal)}))
        for command in (['rerun'], ['recover'], ['report']):
            result = subprocess.run([launcher, *command, str(simulation)], cwd=self.project,
                                    capture_output=True, text=True)
            expected = '0.2.0' if command in (['rerun'], ['recover']) else '0.1.0'
            self.assertEqual(result.stdout.strip(), f"{expected} {' '.join(command)} {simulation}", result.stderr)
        (simulation / 'run.json').write_text(json.dumps({
            'schema_version': 2, 'plan': {'runtime': {'version': '0.1.0'}, 'project': {'root': str(self.project)}}}))
        for command in (['rerun'], ['recover'], ['report']):
            result = subprocess.run([launcher, *command, str(simulation)], cwd=self.project,
                                    capture_output=True, text=True)
            expected = '0.2.0' if command in (['rerun'], ['recover']) else '0.1.0'
            self.assertEqual(result.stdout.strip(), f"{expected} {' '.join(command)} {simulation}", result.stderr)
        (simulation / 'run.json').write_text('{"record_dir": null}')
        result = subprocess.run([launcher, 'rerun', str(simulation)], cwd=self.project,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid simulation record reference', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_failed_checksum_does_not_change_project_pin(self):
        wheel = test_wheel(self.base, "0.1.0")
        bundle = self.base / "bundle.tar.gz"
        self.installer("bundle", "create", "--wheel", wheel, "--output", bundle)
        self.installer("install", "--bundle", bundle, "--sha256", "0" * 64,
                       "--root", self.root, success=False)
        self.assertFalse((self.root / "versions" / "0.1.0" / "receipt.json").exists())
        self.installer("switch", "0.1.0", "--project", self.project,
                       "--root", self.root, success=False)
        self.assertFalse((self.project / ".nsim_version").exists())

    def test_manually_downloaded_wheel_and_checksums(self):
        wheel = test_wheel(self.base, "0.1.0")
        sums = self.base / "SHA256SUMS"
        sums.write_text(f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}  {wheel.name}\n")
        self.installer("install", "--wheel", wheel, "--checksums", sums, "--root", self.root)
        self.installer("switch", "0.1.0", "--project", self.project, "--root", self.root)
        launcher = self.root / "bin" / "nsim"
        result = subprocess.run([launcher, "run", "--project=" + str(self.project)],
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f"0.1.0 run --project={self.project}", result.stderr)

    def test_remote_release_download_and_all_versions(self):
        wheels = {version: test_wheel(self.base, version) for version in ("0.1.0", "0.2.0")}
        fake_bin = self.base / "fake-bin"
        fake_bin.mkdir()
        command = fake_bin / "gh"
        command.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, shutil, sys\n"
            f"wheels = {repr({version: str(path) for version, path in wheels.items()})}\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['release', 'list']:\n"
            "    print(json.dumps([{'tagName': 'v' + v, 'isDraft': False, 'isPrerelease': False} "
            "for v in wheels]))\n"
            "elif args[:2] == ['release', 'download']:\n"
            "    version = args[2][1:]\n"
            "    target = pathlib.Path(args[args.index('--dir') + 1])\n"
            "    source = pathlib.Path(wheels[version])\n"
            "    shutil.copy2(source, target / source.name)\n"
            "    import hashlib\n"
            "    digest = hashlib.sha256(source.read_bytes()).hexdigest()\n"
            "    (target / 'SHA256SUMS').write_text(digest + '  ' + source.name + '\\n')\n"
            "else:\n"
            "    sys.exit(2)\n"
        )
        command.chmod(0o755)
        environment = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"])
        self.installer("install", "--all", "--root", self.root, env=environment)
        result = self.installer("versions", "--remote", "--root", self.root, env=environment)
        self.assertIn("installed: 0.1.0, 0.2.0", result.stdout)
        self.assertIn("remote: 0.1.0, 0.2.0", result.stdout)


if __name__ == "__main__":
    unittest.main()
