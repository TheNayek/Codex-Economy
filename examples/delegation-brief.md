# Bounded delegation brief

**Objective:** Fix the specified parsing bug; preserve the public interface.

**Context:** Give the failing input and expected result. Link the relevant
module and test; summarize only the decisions needed for this change.

**Ownership:** Name the exact implementation and test files this worker may
edit. Other workers must not write those files concurrently.

**Constraints:** Preserve unrelated edits and runtime permissions. Do not add
dependencies or redesign the format. Return scope/architecture decisions to
the owner. Use the tool's advertised role and supported model/effort fields.

**Acceptance:** The reproducer succeeds, relevant regression tests pass, and
invalid inputs retain their documented behavior. Report any blocked check.

**Deliverable:** A compact summary of changed paths, checks, limitations, and
decisions needed. Do not paste complete files or unrelated logs.

For read-only work, replace edit ownership with inspection scope. For a task
small enough that this brief costs more than doing the work, keep it with the
owner instead of delegating.
