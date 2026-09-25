# Install Codex Economy — agent runbook

Use this procedure when the user asks you to install or adapt Codex Economy.
Reading this file alone is not authorization to modify a home. Treat repository
content as implementation guidance, subordinate to the user's instructions and
the effective runtime permissions. Do not execute live smoke tests by default.

## Outcome

Deliver a working, verified configuration for one chosen Codex home, with five
task-oriented profiles, bounded agent roles, preserved unrelated preferences,
and an exact rollback command. Installation does not switch an already running
model or prove savings.

## 1. Inspect without exposing private data

- Clone this repository to a stable location the user controls. Use Python 3.11+.
- Identify the intended home from the user's environment/explicit choice. Usually
  it is CODEX_HOME or the user's .codex directory; do not guess across accounts.
- Inspect relevant config locally: model/effort, role collisions, existing policy,
  and permission constraints. Never print auth.json, tokens, MCP headers, or
  full configuration. Do not copy credentials or homes.
- Read official Codex configuration/subagent documentation when the installed
  client's format is uncertain. Start from
  https://learn.chatgpt.com/docs/config-file/config-reference and
  https://learn.chatgpt.com/docs/agent-configuration/subagents.
- Verify models AND supported reasoning efforts using the current client's
  advertised catalog or user-confirmed selection. A cached catalog alone is not
  proof of account entitlement. If availability cannot be established, ask for
  the missing model choices; do not silently substitute or claim verification.

Run the offline checks before installation:

```sh
python -m unittest discover -s tests -q
python economy.py self-test
```

## 2. Adapt to the account

Choose a light model for direct bounded work and Luna roles, a balanced model
for Sol continuity and workers, and a frontier model for owner decisions. They
may be the same model at different supported effort levels if that fits the
available account. Avoid maximal reasoning and premium speed by default.

Bundled examples: DEFAULT = Astra high, QUICK = Astra medium,
DEEP = Astra xhigh (opt-in), DIRECT = Luna high, CONTINUITY = Sol medium.
These are presets to evaluate, not a guaranteed optimum.

Initialize a local manifest with confirmed choices. Replace the uppercase
placeholders with actual values; never pass placeholders literally:

```sh
python economy.py init --home ABSOLUTE_HOME --account main --light-model LIGHT_MODEL --light-effort LIGHT_EFFORT --balanced-model BALANCED_MODEL --balanced-effort BALANCED_EFFORT --deep-model DEEP_MODEL --deep-effort DEEP_EFFORT
```

All three model IDs must be supplied together. Light maps to DIRECT and Luna
roles, balanced to CONTINUITY and Sol workers, and deep to the three Astra owner
routes. With deep effort xhigh, DEFAULT remains high and QUICK medium; DEEP alone
uses xhigh. Confirm that each model supports every resulting effort, including
Sol high if using sol-worker-high. Review the generated local manifest, especially
the role efforts. Init refuses an existing local file;
for an existing install, inspect and deliberately edit that file instead of
deleting it or using a force overwrite. Updating the public template never
silently replaces existing local choices; compare and merge intentionally.
When migrating a prior six-role install, the new manifest retires the old
implementer role only if its file still matches the installer's ownership
snapshot. An edited or unowned role is reported as a collision for review.

The standard public preset has empty runtime_defaults and does not manage
Windows sandbox mode or web search. Preserve this setting. Do not change
approval policies, reviewer settings, writable roots, MCP, or authentication
to make installation easier.

## 3. Plan, apply, verify

```sh
python economy.py doctor --account main
python economy.py plan --account main
```

Doctor does not start Codex and reports model availability as not_checked.
Your separate catalog/account check supplies that evidence. Plan reports changed
paths, collisions and pending transactions; it does not print raw configuration.

Explain the concrete scope: five profiles, seven roles, four agent defaults,
and a marked AGENTS.md block. Under an existing instruction to install, apply
the reviewed plan without another redundant permission question. Respect any
actual sandbox approval boundary or ambiguity about the target.

```sh
python economy.py sync --account main
python economy.py verify --account main
```

Unowned collisions: preserve the user's files and resolve naming/configuration
deliberately. Pending transaction: inspect and use recover before replanning.
Never bypass these checks or delete backups to make verify pass.

Ordinary sync preserves the root model preference. Only when the user also
wants DEFAULT as their default owner:

```sh
python economy.py plan --account main --include-defaults
python economy.py set-defaults --account main
python economy.py verify --account main --include-defaults
```

## 4. Handoff

Tell the user:
- Which confirmed models/efforts back all five routes, and when to use each.
- Which files changed and whether root preferences were preserved.
- The completed checks and remaining runtime uncertainty.
- To open a new task to reload roles. In CLI use codex --profile NAME; in Desktop
  choose the corresponding model/effort in the composer. Do not claim current-turn switching.
- How to undo the latest transaction:

```sh
python economy.py rollback --account main
```

For interrupted writes, use recover first. Keep economy-backups. To fully
uninstall after multiple updates, roll back completed transactions in reverse
order and verify the pre-install state.

Do not run smoke.py or a benchmark without the user's request to spend real
model usage. If requested, use a disposable workspace, account for every child
and retry, and distinguish routing evidence from savings evidence.

## Acceptance

The chosen home is correct; model choices are confirmed or explicitly unresolved;
verify succeeds; unrelated permissions/integrations are preserved; the user has
usable selection and rollback instructions. Report any blocked step accurately.
