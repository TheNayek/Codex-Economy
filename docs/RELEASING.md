# Release checklist

Run offline checks from a clean source checkout:

```sh
python -m unittest discover -s tests -v
python economy.py self-test
python build_public.py --check
python build_public.py --output dist/release
```

The exporter uses a reviewed file allowlist, replaces local configuration with
empty-account public templates, and writes PUBLIC-EXPORT.json with SHA-256
hashes. It refuses existing destinations. Use a fresh destination for each build.
It includes no Git history, local manifest, credentials, or runtime evidence.

Before tagging:

1. Confirm the public template and policy match the intended release.
2. Review every distributed file. The path/email/token scan is heuristic, not
   a comprehensive secret audit. Use synthetic fixtures, never raw user reports.
3. Confirm CI passes on Windows, Linux and macOS for the exact commit being tagged.
4. Verify the agent runbook and plan/sync/verify/rollback flow in a disposable home.
5. Publish source only, with the MIT license and honest validation scope.

Use a prerelease while client compatibility and live routing coverage are
limited. Offline installation tests, live routing, and measured quota savings
are three different claims. Never label one as proof of another.

Set a public Git author/committer identity before committing. If a repository
originates from a private workspace, publish a clean export with new history;
ignore rules do not remove personal information from earlier commits.

Keep local manifests and economy-backups out of archives. Perform verification
in a disposable copy when preparing a pristine source ZIP, so generated caches
and temporary test files do not enter the archive.
