# BioStatBench — starter kit

A benchmark for AI performance in drug-development biostatistics, built to the standard you
would apply to a trial rather than the standard typical of AI leaderboards. Targeted at
**Anthropic models on the Claude Code surface**, with a bare-API surface retained for contrast.

## What is here

| File | Role | Status |
|---|---|---|
| `protocol/BENCHMARK_PROTOCOL.md` | Protocol: context of use, prior art, blueprint, item taxonomy, SAP, contamination, governance, phased plan | Draft for review |
| `protocol/ADDENDUM_A_claude_code.md` | **Amends the protocol for the agentic surface**: configuration-as-unit-under-test, model matrix, confounds, key-leakage, agentic conduct (D10), staged design | Draft for review |
| `harness/cc_runner.py` | Claude Code runner: `claude -p` invocations, isolated workspaces, stream-json parsing, model-identity verification, conduct signals | Self-test passes (26 checks) |
| `harness/run_bench.py` | Bare Messages API runner | Dry-run passes; **not usable on this gateway** (needs `ANTHROPIC_API_KEY` directly, no `lilly-code` path) |
| `bench_project/` | Claude Code project scaffolding: `CLAUDE.md`, deny rules, `/bsb-run` `/bsb-key` `/bsb-score`, `bsb-item-author` skill, `bsb-adversary` subagent | Ready to copy to a repo root |
| `items/seed_items.yaml` | 13 active worked items (1 retired), 6 of 7 types (no `T5_longform` yet), 9 of 9 domains, derivations for every numeric key | Needs a second author + adjudication |
| `schema/item_schema.json` | Item schema enforcing provenance, severity, split, contamination fields | Complete |
| `analysis/scoring_and_inference.R` | SAP as code: scoring, judge-misclassification correction, GLMM, paired McNemar, Bradley–Terry, G-study, power | Written, **not executed** (no R here) |
| `analysis/factorial_analysis.R` | Agentic surface: factorial GLMM, censoring rules, variance decomposition, conduct endpoints, cost frontier, surface gap | Written, **not executed** |
| `analysis/verify_keys.py` | Derives the numeric answer keys in the seed items | Executed |
| `analysis/verify_design_numbers.py` | Derives the coverage, power and clustering figures in the protocol | Executed |

## Quick start

```bash
# 1. verify the runner without spending anything
python3 harness/cc_runner.py --self-test

# 2. dry-run a factorial sweep (builds commands + workspaces, invokes nothing)
python3 harness/cc_runner.py --items items/seed_items.yaml --split public \
  --models opus-latest sonnet-latest --effort medium high --replicates 2 \
  --work-root /tmp/bsb_work --keys-dir ./_keys --dry-run --out /tmp/dry.csv

# 3. real pilot. No ANTHROPIC_API_KEY on this gateway - cc_runner.py authenticates
#    every --bare session through the already-authorized `lilly-code token` CLI via
#    --settings apiKeyHelper, so nothing further needs to be exported. Confirmed:
#    the gateway silently substitutes requests for public full model names (e.g.
#    claude-sonnet-5 -> claude-opus-4-6 with a warning) but honours these three
#    aliases cleanly, each resolving to a distinct model - use them as --models.
python3 harness/cc_runner.py --items items/seed_items.yaml --split public \
  --models sonnet-latest --effort medium --replicates 1 \
  --work-root /tmp/bsb_work --keys-dir ./_keys \
  --max-turns 30 --max-budget-usd 0.75 --concurrency 4 --out results/pilot.csv

# 4. operate it from inside Claude Code
cp -r bench_project/. /path/to/repo/ && cd /path/to/repo && claude
#   /bsb-key D3-MMR-007        generate a code item's key by execution
#   /bsb-run public opus-latest,sonnet-latest 5
#   /bsb-score results/pilot.csv
```

`harness/run_bench.py` (the bare Messages API surface) is NOT usable in this environment: it
authenticates via `anthropic.Anthropic()`, which requires `ANTHROPIC_API_KEY` directly and has
no equivalent to the `lilly-code token` path. Everything here runs on the Claude Code surface.

In R: `source("analysis/scoring_and_inference.R"); validate_rg_coverage(); validate_mcnemar_power(); demo()`
then `source("analysis/factorial_analysis.R"); demo_agentic()`.

## The five things that decide whether this works

1. **You are benchmarking a configuration, not a model.** A `claude -p` run is model × agent
   loop × tools × permission mode × effort × auto-discovered context. `arm_id` is the
   grouping variable; pin `--model` to `opus-latest`/`sonnet-latest`/`haiku-latest` on this
   gateway (not the public full names - see Quick start §3) and record `claude --version`.
2. **Answer-key leakage.** Claude Code has Bash; `cwd` is not a boundary and `--bare` ignores
   `.claude/settings.json`. Isolation is a container that does not mount the keys.
3. **Silent model substitution.** Never pass `--fallback-model`. The runner cross-checks the
   model in `system/init` and flags mismatches for exclusion - this gateway silently
   substitutes public full model names, which is exactly what this check exists to catch.
4. **Judge measurement error.** Simulated coverage of the naive 95% interval on judge-graded
   accuracy: 0.00–0.69 versus 0.95 for the Rogan–Gladen-corrected interval.
5. **Precision.** At a discordance rate of 0.20, 400 items resolve ~6 percentage points at
   80% power. State the resolution before ranking anything.

## Next three actions

1. Run the 20-run pilot (keys are already split into `_keys/` — see `items/seed_items.yaml`'s
   `answer_key_ref` fields) and replace every cost and wall-clock figure in Addendum A §A.7
   with measurements.
2. Run `bsb-adversary` against `D1-EST-003` and `D4-REC-005`. Both are multi-select items
   where a defensible alternative reading is the most likely defect.
3. Decide the truncation rule (`censor` vs `incorrect`) and write it into the SAP before the
   first scored run, not after seeing which arms truncate. `D8-ADY-008` already truncates on
   a real run (its `data/synthetic_ae.csv` / `data/synthetic_adsl.csv` attachments don't exist
   yet — generate them, or the item can't score above 0 turns spent looking for the files).

