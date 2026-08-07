---
description: Score a BioStatBench results CSV using the SAP-as-code in analysis/scoring_and_inference.R and analysis/factorial_analysis.R
argument-hint: <results.csv>
allowed-tools: Bash(module load R *), Bash(Rscript *), Read
disable-model-invocation: true
---

## Context

- Results file: `$1`

## Task

Score `$1` and report results, in this order and no other:

1. Detect which harness produced the file. A `harness/run_bench.py` (bare API) CSV has a
   `latency_s` column and no `arm_id`; a `harness/cc_runner.py` (Claude Code) CSV has
   `arm_id`, `num_turns`, `executed_verification`. Load with the matching R layer:
   `analysis/scoring_and_inference.R` for the API surface, or
   `source("analysis/scoring_and_inference.R"); source("analysis/factorial_analysis.R")`
   for the agentic surface (the latter's `read_cc_results()` expects the former's helpers).
2. On the agentic surface, call `read_cc_results("$1")`, then `apply_truncation_rule()`
   with the truncation rule this run pre-specified (check `results/*.meta.json` or ask if
   it isn't recorded — do not silently pick one; §A.7 of the addendum is explicit that this
   must be pre-specified before the run, not chosen after seeing which arms truncate).
3. Report exactly three counts **before** any accuracy number, unmissable at the top of the
   report — this mirrors `/bsb-run`'s own convention and exists for the same reason: an
   accuracy figure that includes excluded or censored rows is not the accuracy figure.
   - count of `model_mismatch == 1` rows (excluded from every step below, not averaged in)
   - count of `claimed_without_execution == 1` rows (the S1 agentic-conduct violation)
   - count of non-`ok` / truncated statuses (censored, not scored as incorrect, unless the
     pre-specified rule says otherwise)
4. Then run, and report:
   - `s1_free_rate()` per arm (the primary endpoint on the agentic surface) with its 95% CI
   - `conduct_summary()` per arm, including the Wilson interval on
     `claimed_without_execution`
   - `cost_frontier()` — report the whole frontier, not a single ranking
   - On the API surface (or if both surfaces are present): `blueprint_score()`,
     `pairwise_mcnemar()` between any two arms present, and the Rogan-Gladen-corrected
     interval from `rg_ci()` if any item type in this file is judge-graded — never report
     naive judge-graded accuracy without the correction; the naive interval's coverage was
     measured at 0.00-0.69 in `validate_rg_coverage()`.
5. If fewer than 2 replicates per item x arm are present, note explicitly that the
   variance-decomposition and effective-n figures in the protocol do not apply to this run.

Do not editorialise about which arm "won" beyond what the numbers above directly support.
State the resolution (minimum detectable difference at this n) before ranking anything —
this is the project's own §5 rule, restated here because it is the rule most tempting to
skip when a result looks clean.
