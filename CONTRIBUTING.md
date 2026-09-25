# Contributing

Useful contributions improve completed work per unit of effort: clearer setup,
reproducible compatibility reports, safer configuration edits, and measurements
that include failed attempts and human rework. English and Spanish are welcome.

Use Python 3.11 or newer. There are no third-party Python dependencies.

```sh
python -m unittest discover -s tests -v
python economy.py self-test
python build_public.py --check
```

Tests use synthetic data in temporary directories inside the checkout. They do
not need Codex, credentials, network access, or model calls. Never run the live
smoke as part of CI. Symlink tests may skip on hosts without that capability;
report skips rather than counting them as passes.

Keep one concern per PR. Describe the problem, resulting behavior, and checks.
For installer changes, cover preservation of unrelated data, failure handling,
and rollback. Never loosen runtime permissions to make a test pass. Model
recommendations need a reproducible evaluation with acceptance criteria and
failures included; a successful routing smoke is insufficient.

Do not attach raw rollouts, account configuration, auth files, personal paths,
or historical backups. Use synthetic reproducers and the compatibility issue
template. Read [SECURITY.md](SECURITY.md) for vulnerability reporting.

Before release, follow [the release procedure](docs/RELEASING.md). Contributions
are provided under the repository's MIT license. Keep acknowledgements and
third-party notices when adding external material.
