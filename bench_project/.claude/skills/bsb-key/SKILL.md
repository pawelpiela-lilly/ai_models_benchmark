---
description: Generate a T3_code or T7_agentic item's answer key by executing the reference solution in the pinned environment
argument-hint: <item-id>
allowed-tools: Bash(Rscript *), Bash(module load R *), Read, Write(_keys/*), Bash(git status *)
disable-model-invocation: true
---

## Context

- Item ID: `$1`
- Working tree state: !`git status --short`

## Task

Generate the answer key for item `$1` by **executing** its reference solution, never by
typing a value. This is CLAUDE.md rule 2: a hand-entered numeric key for a code item is
unverifiable and will eventually be wrong.

1. Confirm `$1` exists in `items/seed_items.yaml` and its `type` is `T3_code` or
   `T7_agentic`. Refuse for any other type — those items are scored deterministically or
   by judge, not by execution.
2. Refuse if `$1` is already retired (`retired.is_retired: true`).
3. Locate the reference solution. By convention it lives at
   `analysis/reference_solutions/<item-id>.R` (or `.py`/`.sas` matching the item's
   `prompt.attachments`). If it does not exist yet, stop and say so — do not write one
   yourself as part of key generation; authoring the reference solution and generating
   the key from it are different tasks with different review requirements.
4. Run the reference solution in the pinned environment:
   `module load R/4.5.2 && Rscript analysis/reference_solutions/<item-id>.R` (or the
   equivalent for the solution's language). Capture stdout and any numeric result.
5. Record the environment identity alongside the value: `R.version.string`,
   `installed.packages()` versions for any package the solution loads, and the git commit
   hash of the reference solution file at the time it was run.
6. Write the result to `_keys/<item-id>.yaml` — never back into `items/seed_items.yaml`
   directly. The key file's `derivation` field must name the reference solution path, the
   exact command run, and the environment hash from step 5, so a reviewer can re-run it.
7. Report the generated value and ask for confirmation before anything reads it back for
   scoring — a silently-accepted key is exactly the failure mode this command exists to
   prevent.

Never read `_keys/` or `items/private/` for any purpose other than writing the new key
file in step 6. Never consult an existing key file to sanity-check a new one against a
"round number" instinct — the execution result is the key, full stop.
