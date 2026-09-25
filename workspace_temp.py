"""Workspace-scoped temporary directories with inherited Windows ACLs."""
from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path

_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_MARKER = ".workspace-temp-owner"


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _remove_node(path: Path, root: Path) -> None:
    """Remove a child without traversing symlinks or Windows reparse points."""
    if not _inside(path, root):
        raise RuntimeError("temporary cleanup escaped its workspace root")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    reparse = stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)
    if reparse:
        if stat.S_ISDIR(info.st_mode):
            os.rmdir(path)  # removes a junction itself; never walks its target
        else:
            path.unlink()
        return
    if stat.S_ISDIR(info.st_mode):
        # Recheck immediately before enumeration in case the entry was swapped.
        if _is_reparse(path):
            raise RuntimeError("temporary directory changed to a reparse point during cleanup")
        with os.scandir(path) as entries:
            for entry in entries:
                _remove_node(path / entry.name, root)
        os.rmdir(path)
    else:
        path.unlink()


class WorkspaceTempDirectory:
    """Create a unique temporary directory below a trusted workspace root."""

    def __init__(self, root: Path, prefix: str = "workspace-temp-") -> None:
        if not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", prefix):
            raise ValueError("workspace temporary prefix must be a simple name")
        candidate = Path(root).absolute()
        if _is_reparse(candidate):
            raise ValueError("workspace temporary root cannot be a reparse point")
        resolved = candidate.resolve(strict=True)
        if os.path.normcase(str(candidate)) != os.path.normcase(str(resolved)):
            raise ValueError("workspace temporary root must not use a symlink or junction")
        if not resolved.is_dir():
            raise ValueError("workspace temporary root must be a directory")
        self.root = resolved
        self.prefix = prefix
        self.token = secrets.token_hex(16)
        self.path = self.root / f"{self.prefix}{self.token}"
        self.name = str(self.path)
        self._created = False
        self.path.mkdir(mode=0o755)  # inherit the workspace ACL; avoid tempfile's private ACL
        self._created = True
        try:
            marker = self.path / _MARKER
            with marker.open("x", encoding="ascii", newline="") as handle:
                handle.write(self.token)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            _remove_node(self.path, self.root)
            self._created = False
            raise

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        if not self._created:
            return
        if _is_reparse(self.root) or self.root.resolve(strict=True) != self.root:
            raise RuntimeError("workspace root changed during temporary directory lifetime")
        if (self.path.parent != self.root or self.path.name != f"{self.prefix}{self.token}"
                or not _TOKEN.fullmatch(self.token)):
            raise RuntimeError("temporary cleanup target is not the owned workspace child")
        if _is_reparse(self.path):
            raise RuntimeError("temporary cleanup target became a reparse point")
        marker = self.path / _MARKER
        if _is_reparse(marker) or not marker.is_file() or marker.read_text(encoding="ascii") != self.token:
            raise RuntimeError("temporary cleanup ownership marker is missing or changed")
        _remove_node(self.path, self.root)
        self._created = False
