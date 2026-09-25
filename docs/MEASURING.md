# Measure completed work, not just token counts

The hypothesis is that bounded tasks can use smaller models and less context
without losing acceptance quality. More capable models can avoid expensive
retries on difficult work. Both are hypotheses to test on your workload.
This project has no controlled evidence for a percentage reduction in quota.

## Worked estimates: what could model selection change?

These are **credit-cost calculations for assumed workloads, not measured quota
savings or predictions of your account's remaining hours**. They help explain
why matching the model to the task can matter.

Official Standard-speed rates, checked **2026-09-25**, in credits per million
tokens:

| Model | Uncached input | Cached input | Output |
| --- | ---: | ---: | ---: |
| GPT-6 Astra | 250 | 25 | 1,250 |
| GPT-6 Sol | 50 | 5 | 250 |
| GPT-6 Luna | 2.5 | 0.25 | 12.5 |

Source: [OpenAI Codex pricing](https://learn.chatgpt.com/docs/pricing#token-rates).
Credit prices alone do not determine included subscription usage. API-key
billing uses separate API prices; some Enterprise agreements use other rate
cards. Recheck the source before using these figures for a spending decision.

Suppose a task consumes **20,000 uncached input + 80,000 cached input + 10,000
output tokens**, including billable reasoning output where applicable. These
are illustrative inputs, not a typical-task measurement. Cached input is a
subset of total input: the example has 100,000 input tokens in total, not
180,000. At Standard speed, with no extra tool/service charges:

```text
credits = (uncached_input * input_rate
         + cached_input * cached_rate
         + output * output_rate) / 1,000,000
```

| Model for that token workload | Calculated credits |
| --- | ---: |
| Astra | 19.500 |
| Sol | 3.900 |
| Luna | 0.195 |

The same token workload is a simplifying assumption, **not equal capability or
equal work completed**. Effort, context, caching, tools, delegation and retries
can all change the token totals. There is no fixed high-to-medium reasoning
discount to apply on top of this formula.

For a batch of ten tasks, assume each successful task uses the token quantities
above and satisfies the same acceptance criteria:

| Hypothetical change | Before: credits | After: credits | Calculated change |
| --- | ---: | ---: | ---: |
| All 10 on Astra → 5 Luna, 3 Sol, 2 Astra | 195.000 | 51.675 | 73.5% lower credit cost |
| All 10 on Sol → 5 Luna, 5 Sol | 39.000 | 20.475 | 47.5% lower credit cost |
| Previous mixed batch needs 2 additional full Astra rescue runs | 39.000 | 59.475 | 52.5% higher credit cost |

These are selected arithmetic examples, **not an expected savings range**. The
last row counts all original attempts plus the rescue runs; failed work is not
free. The mix must come from actual task suitability, not a target percentage.
If the current workflow is already efficient, savings may be small or absent.
More manual corrections can also make a lower-credit workflow a worse choice.

Use these examples to form a hypothesis, then replace the inputs with observed
usage across the owner, subagents, review and retries. Do not advertise these
percentages as "Codex Economy saves X% of your quota."

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
