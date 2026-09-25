# Measure completed work, not just token counts

The hypothesis is that bounded tasks can use smaller models and less context
without losing acceptance quality. More capable models can avoid expensive
retries on difficult work. Both are hypotheses to test on your workload.
This project has no controlled evidence for a percentage reduction in quota.

## A small comparison you can reproduce

1. Pick representative tasks with fixed inputs and explicit acceptance checks:
   a mechanical edit, a bounded bug, and a feature needing design judgment.
2. Use the same repository commit and a fresh workspace for each run. Record
   Codex version, available models, selected effort, service tier, enabled
   tools, account plan, and whether caches/context were already warm.
3. Compare your normal workflow with one chosen Economy profile. Alternate run
   order and repeat each condition; do not select only successful runs.
4. Evaluate the same checks, preferably without knowing which condition produced
   the change. Record human correction time and rejected output too.
5. Include owner, children, review, retries, and recovery in the totals. Report
   sample size, spread, failures, and unverified observations alongside averages.

Use [the CSV template](../examples/measurement.csv). One row is one attempted
task under one condition; use pseudonymous run IDs. Empty token/quota fields
mean unknown, not zero. `accepted` is `yes` or `no`. `total_seconds` includes
review and retries; `human_rework_minutes` records manual correction separately.

Useful comparisons are acceptance rate, time per accepted task, and observed
tokens per accepted task. Include unsuccessful attempts in the numerator.
Never divide by zero when no run was accepted. Compare token counts across
models cautiously: they are not equal to price, credits, or subscription quota.
Account usage windows can also include other concurrent tasks and reset during
a test, so a before/after percentage is not isolated evidence by itself.

## Inspect a local task

```sh
python observe.py --account main --root TASK_ID --json
```

The observer distinguishes configured settings, inherited prefixes, attributable
turns, and terminal state. It deduplicates response usage where identifiable.
Unsupported schema, missing attribution, and missing usage remain uncertainty;
do not silently treat them as successful zero-cost runs. Internal client state
is version-sensitive and is not independent backend attestation.

An optional routing check, after installing and verifying the selected profile:

```sh
python smoke.py --account main --mode QUICK --workspace ./work/routing-smoke
```

This starts a real Codex task and consumes quota. To test delegation, add
`--roles explorer,tester --fork-turns none` and choose an appropriate owner
profile. Use a disposable workspace. It validates routing and completion, not
relative quality or savings. A timeout may leave children to inspect in Codex.
