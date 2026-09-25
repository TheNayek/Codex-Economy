import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_public
from workspace_temp import WorkspaceTempDirectory


class PublicExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = WorkspaceTempDirectory(ROOT, prefix="test-public-")
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self._write_fixture()

    def tearDown(self):
        self.temp.cleanup()

    def _write_fixture(self):
        for name in build_public.PUBLIC_FILES:
            self._write(name, f"fixture: {name}\r\n")
        manifest = {
            "profiles": {},
            "policy_file": "policy.md",
            "fixture": "public template",
        }
        self._write("examples/manifest.template.json", json.dumps(manifest))
        self._write("examples/policy.template.md", "Bundled public policy.\r\n")

    def _write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")

    def test_allowlist_and_replacements_exclude_private_runtime_content(self):
        self._write("manifest.json", "private source manifest")
        self._write("policy.md", "private source policy")
        self._write("evidence/session.json", "synthetic evidence")
        self._write("history.txt", "synthetic history")
        self._write(".git/config", "synthetic git config")
        self._write("manifest.local.json", "synthetic local manifest")
        self._write("unlisted.txt", "not allowlisted")

        output = self.root / "dist" / "public"
        result = build_public.build(output, self.root)

        self.assertEqual(len(build_public.PUBLIC_FILES) + len(build_public.REPLACEMENTS), result["files"])
        copied = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
        self.assertEqual(set(build_public.PUBLIC_FILES) | {"manifest.json", "policy.md", "PUBLIC-EXPORT.json"}, copied)
        self.assertEqual(
            {"profiles": {}, "policy_file": "policy.md", "fixture": "public template"},
            json.loads((output / "manifest.json").read_text(encoding="utf-8")),
        )
        self.assertEqual("Bundled public policy.\n", (output / "policy.md").read_text(encoding="utf-8"))
        self.assertFalse((output / "evidence").exists())
        self.assertFalse((output / "history.txt").exists())
        self.assertFalse((output / ".git").exists())
        self.assertFalse((output / "manifest.local.json").exists())

    def test_output_and_hashes_are_deterministic_canonical_lf(self):
        first = build_public.build(self.root / "dist" / "one", self.root)
        second = build_public.build(self.root / "dist" / "two", self.root)
        self.assertEqual(first["files"], second["files"])
        manifest_one = json.loads((self.root / "dist/one/PUBLIC-EXPORT.json").read_text(encoding="utf-8"))
        manifest_two = json.loads((self.root / "dist/two/PUBLIC-EXPORT.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest_one, manifest_two)
        for name, digest in manifest_one["files"].items():
            data = (self.root / "dist/one" / name).read_bytes()
            self.assertNotIn(b"\r", data)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    def test_existing_destination_is_not_overwritten(self):
        output = self.root / "dist" / "existing"
        output.mkdir(parents=True)
        sentinel = output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(build_public.ExportError, "already exists"):
            build_public.build(output, self.root)
        self.assertEqual("keep", sentinel.read_text(encoding="utf-8"))

    def test_missing_input_leaves_no_output(self):
        (self.root / build_public.PUBLIC_FILES[0]).unlink()
        output = self.root / "dist" / "missing-input"
        with self.assertRaisesRegex(build_public.ExportError, "cannot read public input"):
            build_public.build(output, self.root)
        self.assertFalse(output.exists())

    def test_private_path_and_credential_patterns_are_rejected(self):
        private_path = "C:" + "\\" + "Users" + "\\" + "example-user" + "\\" + "data"
        credential = "ghp_" + "A" * 36
        email = "private.person" + "@" + "example.org"
        for kind, sample in (("path", private_path), ("credential", credential), ("email", email)):
            with self.subTest(sample_kind=kind):
                self._write("README.md", sample)
                with self.assertRaisesRegex(build_public.ExportError, "potential private content"):
                    build_public.public_payload(self.root)

    def test_invalid_public_manifest_is_rejected(self):
        self._write("examples/manifest.template.json", '{"profiles": {"personal": {}}, "policy_file": "policy.md"}')
        with self.assertRaisesRegex(build_public.ExportError, "public manifest"):
            build_public.public_payload(self.root)

    def test_symlinked_public_input_is_rejected_when_supported(self):
        source = self.root / "README.md"
        source.unlink()
        target = self.root / "replacement.txt"
        target.write_text("safe fixture", encoding="utf-8")
        try:
            os.symlink(target, source)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"file symlinks unavailable: {exc}")
        with self.assertRaisesRegex(build_public.ExportError, "symlink or reparse point"):
            build_public.public_payload(self.root)

    def test_output_outside_dist_is_rejected(self):
        output = self.root / "elsewhere" / "public"
        with self.assertRaisesRegex(build_public.ExportError, "below this checkout/dist"):
            build_public.build(output, self.root)
        self.assertFalse(output.exists())

    def test_output_path_traversal_is_rejected(self):
        output = self.root / "dist" / ".." / "escape"
        with self.assertRaises(build_public.ExportError):
            build_public.build(output, self.root)
        self.assertFalse((self.root / "escape").exists())

    def test_symlinked_output_is_rejected_when_supported(self):
        target = self.root / "target"
        target.mkdir()
        output = self.root / "dist" / "linked"
        output.parent.mkdir(parents=True)
        try:
            os.symlink(target, output, target_is_directory=True)
        except (NotImplementedError, OSError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        with self.assertRaisesRegex(build_public.ExportError, "symlink or reparse point"):
            build_public.build(output, self.root)
        self.assertTrue(target.is_dir())


if __name__ == "__main__":
    unittest.main()
