"""Exercise the installed program from outside the checkout."""

import json
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args):
        with tempfile.TemporaryDirectory() as cwd:
            return subprocess.run(
                [sys.executable, "-m", "nsim", *args], cwd=cwd,
                capture_output=True, text=True, timeout=10,
            )

    def test_installed_identity_matches_package(self):
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "nsim " + version("nsim"))

    def test_machine_readable_provenance(self):
        result = self.run_cli("version", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"tool": "nsim", "version": version("nsim")})

    def test_console_entry_point(self):
        executable = Path(sys.executable).parent / "nsim"
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [str(executable), "--version"], cwd=cwd,
                capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "nsim " + version("nsim"))

    def test_help_has_no_execution_side_effect(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version", result.stdout)

    def test_unknown_command_fails(self):
        result = self.run_cli("unknown-command")
        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)


if __name__ == "__main__":
    unittest.main()
