---
description: Run a BioStatBench evaluation sweep over the Claude Code surface
argument-hint: <split> <models, comma-separated> [replicates]
allowed-tools: Bash(python3 harness/cc_runner.py *), Read, Bash(ls *), Bash(git status *)
disable-model-invocation: true
---

## Context

- Claude Code version: !`claude --version`
- Working tree state: !`git status --short`

## Task

Run the evaluation sweep. Arguments: split `$1`, models `$2`, replicates `${3:-5}`.

Before invoking anything:

1. Refuse if the working tree is dirty. A scored run against uncommitted items is not
   re-derivable, and "I'll commit after" never survives contact with a result you like.
2. Refuse if `$1` is `private` and `BSB_NO_RETENTION` is not set in the environment.
3. Run a **20-run pilot first** with `--replicates 1` on a subset, read the observed
   `cost_usd` column, and report extrapolated cost and wall-clock time for the full sweep.
   Wait for confirmation before launching the full run.

Then invoke, substituting the comma-separated models as space-separated arguments:

```
python3 harness/cc_runner.py \
  --items items/seed_items.yaml --split $1 \
  --models <models> --replicates ${3:-5} \
  --work-root /tmp/bsb_work --keys-dir ./_keys \
  --max-turns 30 --max-budget-usd 0.75 --concurrency 6 \
  --out results/cc_$1_$(date +%Y%m%dT%H%M%S).csv
```

Afterwards report exactly three numbers before anything else: the count of
`model_mismatch=1` rows, the count of `claimed_without_execution=1` rows, and the count of
non-`ok` statuses. Mismatched rows are excluded from the primary analysis, not averaged in.
Do not summarise accuracy until those three counts are stated.
