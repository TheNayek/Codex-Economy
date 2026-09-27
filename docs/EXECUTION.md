# Finish against evidence

Economy separates model selection from execution. `manifest.json` selects models,
efforts and roles. The marked execution section of `policy.md` tells the owner
how to establish completion and respond to evidence; normal sync installs it in
`AGENTS.md`. Codex owns the active Goal, its budget and lifecycle in the chat.
This guide explains that policy; it is not another runtime configuration.

## Choose the smallest useful workflow

| Situation | Execution | Completion evidence |
| --- | --- | --- |
| Small, well-defined task | Ordinary request, with proportionate checking | Relevant diff, test or artifact inspection |
| Important unresolved scope or design choice | Brief planning; native `/plan` when plan-only work is wanted | Decisions and a usable plan, not a claim of implementation |
| A verifiable target needs investigation across turns | Explicitly activated native `/goal` | The agreed checks and resulting behavior |

These are not new Economy profiles. `DIRECT` remains Luna High and can perform
several repair/check steps; a Goal can use DIRECT, CONTINUITY or another existing
model choice. There is no automatic conversion of normal requests into Goals.
Ordinary coding work still includes finding and repairing in-scope failures.
A planning step inside an implementation request does not end that request.
When the user requested only a plan, implementation requires further direction.

Do not activate a Goal merely because a task is long or its prompt mentions
Goals. The user must request activation. If Goals are unavailable, explain that
limitation and perform authorized ordinary work; do not emulate continuation
with a shell loop, hook, scheduled task, or a succession of new chats.

## A compact completion contract

For iterative work, establish these from the request and repository before
editing. Ask only for missing decisions that materially change the outcome.
Keep the contract in the chat or an existing task document; no new ledger is
required for every task.

- **Outcome:** observable behavior or final artifact, not just "improve it."
- **Evidence:** existing tests, build, typecheck, lint, benchmark protocol,
  artifact inspection, or a named human judgment criterion.
- **Scope and constraints:** allowed work, invariants, baseline failures, and
  actions that need approval.
- **Limits and blockers:** user-specified budgets or boundaries, and the input
  needed if progress becomes impossible.

Example request for native activation, with repository-specific placeholders:

```text
/goal Fix [reproducible failure] in [scope]. Completion requires [regression
command] and [relevant integration command] to pass on the final changes, with
[invariant] preserved. Establish the baseline, repair and recheck. Record only
the hypothesis, result and next useful step. If input or access is missing,
report the blocker; preserve existing approvals and do not publish changes.
```

Replace placeholders before activation. A token budget is optional and must be
explicitly requested by the user and supported by the current client. Do not
invent one, translate it into guaranteed Pro quota, or replace the objective to
reset accounting. A time/attempt limit in prose is an instruction, not a new
hard enforcement mechanism supplied by Economy.

## Work, verify, correct

1. Inspect the relevant baseline. Separate pre-existing failures from regressions.
2. Choose a hypothesis and make a scoped change.
3. Run the cheapest check that can distinguish success from failure. Expand to
   required integration checks when the local behavior is correct.
4. Inspect the actual result. A successful command is insufficient if it ran no
   relevant tests, skipped required checks, or tested an earlier revision.
5. Fix discoverable in-scope failures and rerun affected checks. Once the agreed
   outcome is verified, finish; do not keep rerunning green suites without cause.

Do not remove assertions, relax thresholds, or disable checks merely to get a
pass. Fixing an invalid test needs evidence that the intended behavior is intact.
For flaky tests, establish a reproduction or repeated-run protocol; one lucky
green run is not proof of a fix. Benchmarks need comparable inputs and conditions.
Research and visual artifacts may require source comparison or inspection rather
than tests. Label approximation and missing evidence explicitly.

Use LLM review only for a specific residual judgment question, such as an API
tradeoff not captured by tests. Avoid mandatory reviewer agents or a judge that
simply approves the author's answer. Include review, retries and corrections in
the task's total cost.

## Let progress inform model selection

Retain useful context while the current owner is progressing. A failed check
does not itself prove that a larger model is needed. Two attempts that add no
meaningful evidence trigger a strategy review, not a hard attempt cap and not
automatic escalation. A changed hypothesis can justify another attempt.

| Evidence | Response |
| --- | --- |
| A check identifies a local defect | Repair with the current owner or an existing bounded worker |
| Input, dependency, network access or approval is missing | Diagnose the environment/boundary and request the needed input when necessary |
| Different approaches leave a reasoning question unresolved | Consider one bounded, better-capable worker or a native owner-model switch |
| The bounded escalation also fails | Reassess the evidence, scope and blocker; do not climb a fixed model ladder |

Use the installed roles and supported model/effort pairs. A child may use a
different model while the parent retains its current model. Pass the failure,
attempted hypotheses, acceptance checks and necessary context, not the whole
history by default. Reuse useful worker context and keep one writer per file.
Go directly to Astra when the initial evidence already warrants it; deliberately
failing with a cheaper model is not a prerequisite.

A native root-model change needs a supported control such as the CLI `/model`
or the desktop composer, and verification of the effective selection. If that
control is unavailable to the agent, report the limitation and recommend the
selection; do not claim to switch by editing config or changing prose. Economy
does not implement an automatic per-iteration model router. Keep the completion
contract and available budget/accounting intact across any supported handoff.

## Native lifecycle and stopping

Use `/goal` to inspect the objective, and the current client's controls for
pause, resume, edit and clear. Runtime instructions and actual tool schemas take
precedence over this guide and over older documentation examples.

- Create a native Goal only on an explicit activation request. Reuse an existing
  matching Goal rather than duplicating or replacing it.
- Mark complete only when the agreed end state is evidenced. A blocked check,
  exhausted budget, partial result or `task_complete` event is not success.
- Pause only at the user's explicit request. Do not autonomously resume a pause
  or extend/reset budgets. On a budget stop, report progress and the remaining
  work; for completed budgeted Goals, report the native usage returned by Codex.
- The current tool contract permits `blocked` only after the same blocker has
  recurred for at least three consecutive goal turns, including the initial
  turn, and no meaningful independent work remains. A resumed blocked Goal starts
  a fresh audit. Do not equate this with the two-attempt strategy review. Do not
  fabricate activity or repeated tests while waiting to meet a lifecycle rule.
- Preserve user steering, scope and approvals. A Goal never grants deployment,
  publication, sensitive writes or broader access beyond existing authorization.

The final report should connect each important criterion to its result, then
state any unverified requirement and what would unlock it. No special report
format or persistent progress database is required.

## Compatibility and validation

Policy sync installs this guidance through the existing marked-block mechanism;
verification detects policy drift and normal rollback restores the previous
block. No manifest schema, model route, agent role, runtime feature flag, CLI
wrapper, scheduler or dependency is added. Existing root preferences survive sync.
Public templates preserve the user's runtime settings, including Goals being
disabled. Do not enable Goals silently during an Economy update.

On clients that do not expose Goals, consult the current native feature controls
and documentation before any requested enablement. Feature visibility, configured
availability and a successful end-to-end Goal are different claims.

Deterministic regression checks cover policy-only upgrade, preservation of native
settings/state, idempotence and rollback. They do not prove an LLM will follow the
policy, that automatic continuation works in every client, or that quota improves.
The [measurement guide](MEASURING.md) still applies: count accepted outcomes,
retries, all participating models and human corrections. Native Goal state is
not parsed or modified by Economy's observer as part of this integration.

Official references checked for this integration:

- [Goals cookbook](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex): persisted objectives, evidence and continuation.
- [Follow a goal](https://learn.chatgpt.com/use-cases/follow-goals): user activation and feature availability.
- [Developer commands](https://learn.chatgpt.com/docs/developer-commands): `/plan`, `/goal` and `/model` controls.
- [App-server goal state](https://learn.chatgpt.com/docs/app-server#manage-a-thread-goal): native state and budget accounting; changing objectives can reset usage.
