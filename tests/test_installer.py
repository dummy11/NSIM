"""Exercise the standalone, offline installer and stable launcher."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
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

    def installer(self, *args, success=True, cwd=None, env=None):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=cwd,
                                capture_output=True, text=True, timeout=30, env=env)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

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
        (journal / "plan.json").write_text(json.dumps({"runtime": {"version": "0.1.0"}}))
        self.installer("switch", "0.2.0", "--project", self.project, "--root", self.root)
        result = subprocess.run([launcher, "--version"], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "nsim 0.2.0", result.stderr)
        result = subprocess.run([launcher, "rerun", str(journal)], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f"0.1.0 rerun {journal}", result.stderr)

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
        result = subprocess.run([launcher, "run", "case", "--project=" + str(self.project)],
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), f"0.1.0 run case --project={self.project}", result.stderr)

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
