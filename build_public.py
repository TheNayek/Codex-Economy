#!/usr/bin/env python3
"""Build a reviewed public tree from an explicit allowlist, without Git history."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# No globbing: a newly added file must be reviewed before distribution.
PUBLIC_FILES = (
    '.gitattributes', '.gitignore', 'README.md', 'README.es.md', 'LICENSE',
    'CONTRIBUTING.md', 'SECURITY.md', 'CHANGELOG.md',
    'economy.py', 'observe.py', 'smoke.py', 'workspace_temp.py',
    'build_public.py',
    'tests/test_economy.py', 'tests/test_observe_smoke.py',
    'tests/test_workspace_temp.py', 'tests/test_setup.py', 'tests/test_public.py',
    'tests/test_onboarding.py', 'INSTALL.md',
    'examples/manifest.template.json', 'examples/policy.template.md',
    'examples/measurement.csv', 'examples/delegation-brief.md',
    'docs/COMPATIBILITY.md', 'docs/MEASURING.md', 'docs/RELEASING.md',
    '.github/workflows/ci.yml', '.github/PULL_REQUEST_TEMPLATE.md',
    '.github/ISSUE_TEMPLATE/bug_report.md',
    '.github/ISSUE_TEMPLATE/workflow_evidence.md',
)
REPLACEMENTS = {
    'manifest.json': 'examples/manifest.template.json',
    'policy.md': 'examples/policy.template.md',
}
SECRET_PATTERNS = (
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b'),
    re.compile(r'\bgithub_pat_[A-Za-z0-9_]{50,}\b'),
    re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b'),
)
PERSONAL_PATH = re.compile(r'(?i)(?:[a-z]:[\\/]+Users[\\/]+|/Users/|/home/)[A-Za-z0-9_.-]+')
EMAIL_ADDRESS = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')


class ExportError(RuntimeError):
    pass


def _no_links(path: Path) -> None:
    """Reject symlinks/junctions in every existing path component."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ExportError('symlink or reparse point in export path')


def public_payload(root: Path = ROOT) -> dict[str, bytes]:
    """Read only reviewed inputs; return the full payload before any writes."""
    root = root.absolute()
    payload = {}
    mapping = {name: name for name in PUBLIC_FILES}
    mapping.update(REPLACEMENTS)
    for destination, source in mapping.items():
        path = root / source
        _no_links(path)
        try:
            data = path.read_bytes()
            text = data.decode('utf-8')
        except (OSError, UnicodeError) as exc:
            raise ExportError(f'cannot read public input: {source}') from exc
        # Canonical LF output is reproducible across Windows Git checkouts.
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        if (PERSONAL_PATH.search(text) or EMAIL_ADDRESS.search(text)
                or any(p.search(text) for p in SECRET_PATTERNS)):
            raise ExportError(f'potential private content in {source}; inspect locally')
        payload[destination] = text.encode('utf-8')
    try:
        manifest = json.loads(payload['manifest.json'])
    except (ValueError, UnicodeError) as exc:
        raise ExportError('invalid public manifest') from exc
    if manifest.get('profiles') != {} or 'deployment_root' in manifest:
        raise ExportError('public manifest must contain empty profiles and no deployment root')
    if manifest.get('policy_file') != 'policy.md':
        raise ExportError('public manifest must use the bundled policy.md')
    return payload


def build(output: Path, root: Path = ROOT) -> dict[str, object]:
    root = root.absolute()
    output = output.absolute()
    _no_links(root)
    _no_links(output)
    # Never write into the source tree outside the dedicated distribution area.
    distribution = root / 'dist'
    if not output.is_relative_to(distribution) or output == distribution:
        raise ExportError('output must be a new directory below this checkout/dist')
    if output.resolve() != output:
        raise ExportError('output must not contain traversal components')
    if output.exists():
        raise ExportError('output already exists; choose a new directory')
    payload = public_payload(root)
    record = {
        'schema_version': 1,
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(payload.items())},
        'scope': 'Allowlisted source only; no private history or runtime evidence.',
        'limitations': 'Heuristic scan only. Review contents before publishing.',
    }
    output.mkdir(parents=True, exist_ok=False)
    # Exclusive writes. A failed build is left for inspection, never overwritten.
    for name, data in payload.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _no_links(path)
        with path.open('xb') as handle:
            handle.write(data)
    with (output / 'PUBLIC-EXPORT.json').open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
    return {'files': len(payload), 'output': str(output), 'history_included': False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--check', action='store_true', help='Validate public inputs without writing')
    choice.add_argument('--output', type=Path, default=Path('dist/public'))
    args = parser.parse_args(argv)
    try:
        if args.check:
            payload = public_payload()
            print(f'public-check: PASS ({len(payload)} allowlisted files; heuristic scan)')
        else:
            output = args.output if args.output.is_absolute() else ROOT / args.output
            print(json.dumps(build(output), indent=2))
    except (ExportError, OSError) as exc:
        print(f'public-export: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
