# Codex Economy

**Codex burning through your quota? Give it a better way to choose models and spend effort.**

[![Offline checks](https://github.com/TheNayek/Codex-Economy/actions/workflows/ci.yml/badge.svg)](https://github.com/TheNayek/Codex-Economy/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[Español](README.es.md)

Codex Economy is an installable toolkit for people who use Codex but don't know
when to delegate bounded work, when to spend more reasoning, or when to run a
task directly on a smaller model. An Astra owner can retain task-level judgment
while Luna or Sol workers handle well-defined pieces. Hand this repository to Codex: it inspects your setup,
adapts the presets, installs them, and verifies the result.

## Give this to Codex

```text
Install Codex Economy using
https://github.com/TheNayek/Codex-Economy/blob/main/INSTALL.md

Inspect my current setup and available models. Adapt the DEFAULT, QUICK, DEEP,
DIRECT, and CONTINUITY presets to my account. Preserve my permissions, integrations, and existing
preferences. Apply and verify the configuration, then explain how to use each
profile and how to undo the installation.
```

[INSTALL.md](INSTALL.md) is the agent runbook. It calls the included installer
and checks; it does not ask the agent to invent configuration or copy a stranger's
home directory. No API key, background service, or Python dependencies required.

## What changes in everyday use?

| Your task | Starting profile | Why |
| --- | --- | --- |
| Open-ended feature or investigation with independent bounded pieces | **DEFAULT** | Astra high owns decisions and delegates suitable work |
| Lighter work that still benefits from an Astra owner | **QUICK** | Astra medium retains task-level judgment |
| Fully bounded, verifiable task | **DIRECT** | Luna high can finish the whole task |
| Known-scope complex work or valuable Sol context | **CONTINUITY** | Sol medium can own the task |
| Specific need for more reasoning or demonstrated benefit | **DEEP** | Astra xhigh is opt-in |

The bundled example uses Astra high as the ordinary owner, with Luna roles for
bounded work and Sol medium for complex implementation. These are starting hypotheses, **not a universal
ranking or a savings guarantee**. Your Codex adapts the model IDs and supported
efforts to what your account actually offers.
DEEP xhigh is only usable when the selected model and account support it;
otherwise choose a supported effort in the local manifest.

The installed policy also asks Codex to keep delegation bounded, avoid filling
agent slots by habit, preserve useful context, and count retries and review as
part of the cost. It can recommend the right profile for the next task; it
cannot silently switch the model running the current turn. Sol high and Astra
xhigh need a concrete reason; neither is the ordinary default.

## What's inside?

- **Native profiles and roles:** regular Codex configuration, no external router.
- **Transactional installer:** plan, collision checks, recovery, and rollback.
- **Read-only doctor:** checks configuration readiness without spending quota.
- **Local observer:** separates configured models from attributable execution.
- **Offline tests:** synthetic homes and fixtures, no credentials or model calls.

There is no promise of “50% less quota.” A smaller model that requires repeated
corrections can be the expensive choice. Use the [measurement protocol](docs/MEASURING.md)
to compare accepted work, total effort, failures, and human rework.

## An idea of the potential difference

For an **assumed identical workload** of 20k uncached input, 80k cached input,
and 10k output tokens at Standard speed, the official rates checked on
2026-09-25 give:

| Model | Calculated credits for those tokens |
| --- | ---: |
| GPT-6 Astra | 19.500 |
| GPT-6 Sol | 3.900 |
| GPT-6 Luna | 0.195 |

These are credit calculations, **not measured savings, equal-quality results,
or a conversion to subscription quota**. The model may need different token
totals to finish the task. See [the source, formula and workload scenarios](docs/MEASURING.md#worked-estimates-what-could-model-selection-change), including a case where retries make the change more expensive.

## Prefer the terminal?

Python **3.11+** and a compatible Codex client are required. Use `python3` if
your system names Python that way.

```sh
git clone https://github.com/TheNayek/Codex-Economy.git
cd Codex-Economy
python -m unittest discover -s tests -q
python economy.py self-test
```

Initialize **one** home with either shell:

```powershell
# Windows PowerShell
python economy.py init --home "$env:USERPROFILE\.codex"
```

```sh
# macOS / Linux
python economy.py init --home "$HOME/.codex"
```

This only writes ignored `manifest.local.json` beside the script. Review it and
confirm the models/efforts exist in your account before applying anything.
For an entirely disposable trial, use an absolute workspace directory instead.

```sh
python economy.py doctor --account main
python economy.py plan --account main
python economy.py sync --account main
python economy.py verify --account main
```

Start a new task so the client reloads custom roles. In a CLI using that home:

```sh
codex --profile QUICK
codex --profile DEFAULT
codex --profile DEEP
codex --profile DIRECT
codex --profile CONTINUITY
```

In Desktop, choose the matching model/effort in the composer. CLI profile names
are not extra Desktop buttons, and old tasks can retain old settings.

## What does installation own?

Ordinary `sync` manages five profile files, seven agent definitions, four
`[agents]` configuration fields, and the marked Economy block in `AGENTS.md`.
It preserves root model preferences, sandbox/approval settings, web search,
writable roots, and unrelated integrations. It never copies auth credentials.

Applying DEFAULT as your root preference is a **separate explicit operation**:

```sh
python economy.py plan --account main --include-defaults
python economy.py set-defaults --account main
python economy.py verify --account main --include-defaults
```

The local manifest takes precedence over the bundled public template.
Installer commands accept `--manifest PATH`; its policy file must be beside it.
Unowned files cause a collision instead of being overwritten. Concurrent
changes that prevent a safe update or rollback are rejected.

## Undo and update

```sh
# Recover an interrupted transaction.
python economy.py recover --account main
# Undo the latest completed transaction.
python economy.py rollback --account main
```

Backups live in the selected home's `economy-backups`. To uninstall, roll back
completed transactions in reverse order to the pre-install state, then verify
your config. Keep backups until done. Deleting this checkout does not uninstall.

For updates, preserve your local manifest, review changes to the template and
policy, then plan/sync/verify again. New routes are not automatically merged
into your local manifest. See [compatibility](docs/COMPATIBILITY.md).

## Evidence, not hype

```sh
python observe.py --account main --root TASK_ID --json
```

Reports omit conversation bodies but may contain private metadata. Review before
sharing. Observation depends on internal client formats and is not independent
backend attestation. The optional live smoke consumes quota and checks routing,
not whether a model is better or cheaper. [Details](docs/MEASURING.md).

Want two independent Codex accounts side by side? That's a separate product:
[**Codex Dual**](https://github.com/TheNayek/Codex-Dual). Economy works with one account.

[Contribute](CONTRIBUTING.md) · [Security](SECURITY.md) · [Changelog](CHANGELOG.md) ·
[MIT license](LICENSE). Unofficial; not affiliated with OpenAI.
