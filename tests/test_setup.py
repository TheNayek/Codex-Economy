import argparse
import copy
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import economy
import observe
from workspace_temp import WorkspaceTempDirectory


class PortableSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTempDirectory(ROOT, prefix="test-setup-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.manifest, _ = economy._load_manifest(ROOT / "manifest.json")
        self.manifest = copy.deepcopy(self.manifest)
        self.manifest["profiles"] = {}
        (self.base / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.base / "policy.md").write_text((ROOT / "policy.md").read_text(encoding="utf-8"), encoding="utf-8")

    def test_init_creates_local_single_profile_without_touching_home(self):
        home = self.base / "profiles" / "main" / ".codex"
        path = economy._init_manifest(str(home), "main", self.base)
        self.assertEqual(path, self.base / "manifest.local.json")
        local, _ = economy._load_manifest(path)
        self.assertEqual(local["profiles"], {"main": str(home.resolve())})
        self.assertNotIn("deployment_root", local)
        self.assertFalse(home.exists())
        self.assertEqual(economy._load_manifest(self.base / "manifest.json")[0]["profiles"], {})
        self.assertEqual(economy._resolve_manifest_path(base=self.base), path)
        self.assertEqual(economy._resolve_manifest_path(str(self.base / "manifest.json"), self.base), self.base / "manifest.json")

    def test_init_refuses_relative_home_and_existing_file(self):
        with self.assertRaisesRegex(economy.EconomyError, "absolute"):
            economy._init_manifest("relative/home", "main", self.base)
        home = self.base / "profiles" / "main" / ".codex"
        local = self.base / "manifest.local.json"
        local.write_text("sentinel", encoding="utf-8")
        with self.assertRaisesRegex(economy.EconomyError, "already exists"):
            economy._init_manifest(str(home), "main", self.base)
        self.assertEqual(local.read_text(encoding="utf-8"), "sentinel")
        self.assertFalse(home.exists())

    def test_init_refuses_local_manifest_symlink(self):
        local = self.base / "manifest.local.json"
        target = self.base / "absent.json"
        try:
            os.symlink(target, local)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink creation unavailable: {exc}")
        home = self.base / "profiles" / "main" / ".codex"
        with self.assertRaisesRegex(economy.EconomyError, "already exists"):
            economy._init_manifest(str(home), "main", self.base)
        self.assertFalse(target.exists())

    def test_empty_targets_and_missing_worker_fail_cleanly(self):
        with self.assertRaisesRegex(economy.EconomyError, "init --home"):
            economy._profile_paths(self.manifest, None, None)
        malformed = copy.deepcopy(self.manifest)
        del malformed["agents"]["worker"]
        path = self.base / "malformed.json"
        path.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaisesRegex(economy.EconomyError, "worker role"):
            economy._load_manifest(path)
        err = StringIO()
        with redirect_stderr(err):
            code = economy.main(["plan", "--manifest", str(self.base / "manifest.json")])
        self.assertEqual(code, 2)
        self.assertIn("init --home", err.getvalue())

    def test_observer_uninitialized_and_home_override(self):
        args = argparse.Namespace(account=None, home=None, manifest=str(self.base / "manifest.json"),
                                  root=None, since=None, ids=None, as_json=True)
        with self.assertRaisesRegex(economy.EconomyError, "init --home"):
            observe.observe(args)
        args.home = str(self.base / "profiles" / "main" / ".codex")
        result = observe.observe(args)
        self.assertEqual(result["account"], "custom")
        self.assertEqual(result["state_db_error"], "state_5.sqlite is absent")

    def test_smoke_help_does_not_need_profile(self):
        result = subprocess.run([sys.executable, str(ROOT / "smoke.py"), "--help"],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--workspace", result.stdout)


if __name__ == "__main__":
    unittest.main()
