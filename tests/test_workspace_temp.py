import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workspace_temp import WorkspaceTempDirectory


class WorkspaceTempDirectoryTests(unittest.TestCase):
    def test_cleanup_removes_nested_workspace_contents(self):
        temp = WorkspaceTempDirectory(ROOT, prefix="temp-nested-")
        path = Path(temp.name)
        nested = path / "one" / "two"
        nested.mkdir(parents=True)
        (nested / "payload.txt").write_text("workspace-only", encoding="utf-8")

        temp.cleanup()

        self.assertFalse(path.exists())

    def test_changed_ownership_marker_refuses_cleanup(self):
        temp = WorkspaceTempDirectory(ROOT, prefix="temp-marker-")
        marker = temp.path / ".workspace-temp-owner"
        payload = temp.path / "preserve.txt"
        payload.write_text("keep", encoding="utf-8")
        marker.write_text("changed", encoding="ascii")
        try:
            with self.assertRaisesRegex(RuntimeError, "ownership marker"):
                temp.cleanup()
            self.assertEqual("keep", payload.read_text(encoding="utf-8"))
        finally:
            marker.write_text(temp.token, encoding="ascii")
            temp.cleanup()
        self.assertFalse(temp.path.exists())

    def test_external_symlink_is_removed_without_deleting_target(self):
        target = WorkspaceTempDirectory(ROOT, prefix="temp-target-")
        payload = Path(target.name) / "outside.txt"
        payload.write_text("preserve", encoding="utf-8")
        owner = WorkspaceTempDirectory(ROOT, prefix="temp-link-")
        link = owner.path / "external"
        try:
            try:
                os.symlink(target.name, link, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlinks unavailable: {exc}")
            owner.cleanup()
            self.assertEqual("preserve", payload.read_text(encoding="utf-8"))
        finally:
            owner.cleanup()
            target.cleanup()

    def test_replaced_owned_child_reparse_point_is_refused(self):
        target = WorkspaceTempDirectory(ROOT, prefix="temp-target-")
        (target.path / "outside.txt").write_text("preserve", encoding="utf-8")
        owner = WorkspaceTempDirectory(ROOT, prefix="temp-replaced-")
        moved = owner.path.with_name(owner.path.name + "-saved")
        owner.path.rename(moved)
        try:
            try:
                os.symlink(target.name, owner.path, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                moved.rename(owner.path)
                self.skipTest(f"directory symlinks unavailable: {exc}")
            with self.assertRaisesRegex(RuntimeError, "became a reparse point"):
                owner.cleanup()
            self.assertTrue((target.path / "outside.txt").is_file())
        finally:
            if owner.path.is_symlink():
                owner.path.unlink()
            if moved.exists():
                moved.rename(owner.path)
            owner.cleanup()
            target.cleanup()


if __name__ == "__main__":
    unittest.main()
