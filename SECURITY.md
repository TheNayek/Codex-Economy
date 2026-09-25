# Security and privacy

Codex Economy edits local configuration; treat its code and policy as trusted
input. Review a plan before applying it. The installer owns a limited set of
configuration fields and marked files, records transactions, and refuses
unowned collisions. A rollback refuses incompatible concurrent changes rather
than overwriting them. Transactions are per home, not atomic across accounts.

`sync` changes agent defaults, profiles, and the managed AGENTS.md block.
The public preset preserves sandbox mode and web search. It preserves unrelated
configuration, including the root model preference, approval/reviewer settings,
writable roots, private desktop, and MCP integrations. Legacy runtime overrides
apply only when deliberately added to the local manifest. `set-defaults` explicitly
changes the root model, effort, and service tier. This tool does not install
command allow rules or copy credentials between homes.

Explorer/researcher instructions and their requested read-only mode are not a
guarantee of isolation: parent runtime overrides may apply. Configure and
verify the effective session boundary. Workspace temporary files inherit the
workspace permissions; they are for synthetic tests, not a secret vault.

The observer reads local SQLite/JSONL state. It omits conversation bodies and
tool arguments/results, but reports can still contain paths, identifiers,
timestamps, and tool names. Do not post reports without reviewing them.
No telemetry is sent by the installer, exporter, or observer. The optional
`smoke.py` launches Codex and incurs real model usage under your account.

Private source installations may include historical evidence and personal Git
history. `.gitignore` does not remove already tracked data. Publish only a
reviewed public export as a new repository, never a copy of the private `.git`.
The exporter uses an explicit file allowlist, empty public account defaults,
and a heuristic content check. That check is not a comprehensive secret audit.

Report vulnerabilities using GitHub's **Report a vulnerability** feature when
enabled. If unavailable, open an issue requesting a private contact without
including exploit details, secrets, or private data. There is no guaranteed
response time. Only the current public source is maintained; upgrades of
Codex may require compatibility fixes before this toolkit is usable.
