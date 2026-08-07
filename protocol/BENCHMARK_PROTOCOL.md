# BioStatBench: Protocol for a Benchmark of AI Model Performance in Drug-Development Biostatistics

**Document status:** Draft v0.1 (design protocol, pre-item-authoring)
**Analogous document class:** treat this as the *protocol*; `analysis/scoring_and_inference.R` is the *SAP*; the item bank is the *CRF*.

---

## 1. Purpose, question of interest, and context of use

The FDA's draft AI guidance and the joint FDA–EMA principles both anchor evaluation to a stated **context of use (COU)** rather than to a general claim of model quality. A benchmark inherits that discipline: "which model is best at biostatistics" is not an answerable question, and a benchmark that tries to answer it will produce a number that supports no decision.

State the COU first, then design backwards from it. Three plausible COUs, which imply *different benchmarks*:

| COU | Decision the benchmark supports | Consequence of error | Design implication |
|---|---|---|---|
| **A. Model selection for an internal statistical-assistant tool** (draft SAP text, retrieve methodology, explain methods to non-statisticians) | Which model to deploy; whether any is adequate | Low–moderate; human review always downstream | Ecological validity matters most; rubric-graded long-form dominates |
| **B. Model selection for code generation in a validated workflow** (derivations, TFL programs, QC) | Which model to permit under multi-use-code validation procedures | High; defects can propagate to submission outputs | Execution-verified items dominate; **critical-error-free rate** is the endpoint, not mean accuracy |
| **C. Capability tracking over model generations** | Research/strategy; when to revisit workflows | Low | Contamination resistance and longitudinal comparability dominate |

**Recommendation:** build for **B** as the primary COU with **A** as a secondary stratum. B is where a benchmark actually changes a decision, and it is the only COU where you can defend the effort to a quality function. Declare the COU in the item metadata so that a future reader can tell which items support which claim.

**Out of scope (state explicitly):** the benchmark does not qualify any model for GxP use. It provides one component of credibility evidence (per the FDA draft framework's Steps 4–6: credibility assessment plan, execution, and reporting). Fitness for a specific programming task is established by the applicable validation procedure, not by a benchmark score.

---

## 2. Prior art: what already exists, and what it does not cover

Do not rebuild these. Do reuse their harness code and, where licences allow, their items as a *calibration* stratum.

| Benchmark | Coverage | Why it is insufficient for this COU |
|---|---|---|
| **StatQA** (Zhu et al., NeurIPS 2024; 11,623 items) | Method selection and applicability assessment across descriptive statistics, correlation, contingency tables, distribution and variance tests | Elementary methods only; no estimands, no survival, no regulatory context. Best frontier performance was ~65%, so it retains headroom, but on the wrong construct |
| **StatLLM** (Song et al., *Sci Data* 2026) | Statistical programming, primarily SAS | Right modality, wrong domain depth; no clinical-trial methodology |
| **StatEval / StatABench / QRData / DSBench / InfiAgent-DABench** | Statistical reasoning, quantitative reasoning over data, data-science agents | General data science; no regulatory or trial-design content |
| **CTBench** (2024) | Predicting baseline features from trial metadata | One narrow trial-design sub-task |
| **HealthBench / HealthBench Professional** (OpenAI, 2025–26) | Open-ended clinical conversations, 48,562 physician-written rubric criteria | Clinical, not statistical. **But its rubric architecture is the right template** and should be copied |
| **CSEDB** (npj Digital Medicine, 2025) | Clinical safety/effectiveness, severity-weighted, 2,069 expert-written items | Clinical. **Its weighted-consequence scoring and the observed 13.3% performance drop in high-risk scenarios is the design pattern to replicate**: report high-consequence strata separately |

**The genuine gap:** no public benchmark covers estimands and intercurrent events, group-sequential and adaptive design, missing-data sensitivity analysis, multiplicity strategy, safety analytics and structured benefit–risk, immunogenicity, CDISC-conformant derivation, or regulatory citation fidelity. That gap is your contribution, and it is defensible in a publication.

---

## 3. Four design decisions that determine whether this is worth doing

These are the failure modes that sink most domain benchmarks. Decide them before authoring a single item.

### 3.1 Contamination: publication destroys the benchmark

Any item bank released openly is absorbed into subsequent pretraining corpora, and the literature is unambiguous that memorisation inflates scores — larger models show higher n-gram overlap with test sets, and perturbation experiments show factual-recall items are the most fragile (entity swaps producing accuracy drops of ~22 percentage points in one three-tier contamination study). HealthBench Professional explicitly asks users not to post examples in plain text for this reason.

**Consequences you must accept:**

1. **Split 50/50 public / private from the start.** The public half is the published artefact and the recruitment tool; the private half is the one whose numbers you trust. Never merge them.
2. **Sending an item to a model API is a form of publication.** Use zero-data-retention endpoints or enterprise agreements with no-training terms for the private set, and record the endpoint terms in the run metadata. Without that, the private set decays with every evaluation cycle. This is the single most commonly overlooked point in benchmark operations.
3. **Rotate.** Retire ~25% of the private set per annual cycle and replace with newly authored items. Budget the authoring cost as recurring, not one-off.
4. **Instrument contamination rather than assume its absence** — see §8.

### 3.2 Construct validity: exam performance is not workflow performance

The NEJM AI script-concordance study found that models matching or exceeding medical-student performance on multiple-choice benchmarks fell well short of senior residents and attending physicians when tested on reasoning tasks — the MCQ format was measuring something other than the intended construct. Expect the same here. A model that scores 90% on estimand MCQs may still produce an SAP section that a regulator would question.

**Mitigation:** cap multiple-choice at ≤20% of scored weight. Anchor the blueprint (§4) to a documented frequency analysis of tasks actually performed by the target function, not to intuition about topic importance. Retain a small **human-anchor stratum** (~30 items) attempted by 3–5 qualified statisticians under the same conditions, so scores are reported relative to human performance rather than on an uninterpretable 0–1 scale.

### 3.3 Plural correct answers

Much of this domain has no unique right answer. "Hypothetical or treatment-policy estimand?" is a design choice; "Fine–Gray or cause-specific hazard?" depends on the question. A key that punishes a defensible alternative measures conformity, not competence.

**Mitigation:** score **justified defensibility**, not answer-matching. Every non-deterministic item carries a rubric with:
- `required` criteria (omission is an error regardless of the choice made),
- `acceptable_alternatives` (an enumerated set of defensible positions, each with its own required justification),
- `disqualifying` criteria (statements that are wrong under any framing).

This is the HealthBench rubric pattern with an added alternatives layer. It also makes disagreement between item authors visible at authoring time, which is when you want to find it.

### 3.4 Judge measurement error is not a nuisance — it biases the estimate

If you grade free text with an LLM judge and report the raw proportion judged correct, your estimate is biased, and the bias direction depends on the true accuracy. With judge sensitivity \(q_1\) and specificity \(q_0\),

$$E[\hat p] = \theta + (2-q_0-q_1)\left(\frac{1-q_0}{2-q_0-q_1} - \theta\right)$$

so the naive proportion overstates low true accuracy and understates high true accuracy. This is exactly the misclassification-bias problem from diagnostic-test epidemiology, and the fix is the Rogan–Gladen prevalence correction with a confidence interval that propagates uncertainty from both the test set and the human-labelled calibration set.

**We simulated the consequence** (10,000 replicates per scenario, \(q_0=0.70\), \(q_1=0.90\), \(n=1000\) test items, \(m_0=m_1=250\) calibration items):

| True accuracy θ | Coverage of naive 95% CI | Coverage of Rogan–Gladen-adjusted 95% CI |
|---|---|---|
| 0.30 | 0.000 | 0.948 |
| 0.50 | 0.000 | 0.952 |
| 0.70 | 0.690 | 0.958 |
| 0.85 | 0.092 | 0.951 |

The naive interval essentially never covers the truth. Any leaderboard built on uncorrected judge output is reporting a number with unknown sign of bias. Implementation is in `analysis/scoring_and_inference.R::rg_adjust()`.

**Requirement:** every judge-graded stratum needs a human-labelled calibration subset. Allocate it asymmetrically — with \(\kappa=(1-q_0)/(1-q_1)\), interval length is minimised near \(\tilde m_0 \approx (1/\tilde p - 1)\sqrt{\kappa}\,\tilde m_1\). Increasing the number of *judged items* does not help once \(n\) is large; only the calibration set narrows the interval.

---

## 4. Content blueprint

Weights are the proportion of total scored weight, not item counts. **Populate the "observed task frequency" column from your own function's work log before finalising** — the weights below are a starting proposal, and their provenance is the main content-validity argument you will have to defend.

| ID | Domain | Weight | Representative content |
|---|---|---|---|
| D1 | Estimands & intercurrent events | 12% | ICH E9(R1) five attributes; five ICE strategies; alignment of estimand ↔ estimator ↔ sensitivity analysis; principal stratum identifiability |
| D2 | Design & sample size | 12% | Power/precision; non-inferiority margin justification; group-sequential and α-spending; adaptive and sample-size re-estimation; dose-finding (BOIN/CRM/mTPI); bioequivalence; external controls |
| D3 | Longitudinal data & missing data | 10% | MMRM vs LMM vs GEE; MI (including reference-based); MNAR sensitivity, tipping-point, δ-adjustment; monotone vs non-monotone patterns |
| D4 | Time-to-event & recurrent events | 10% | Non-proportional hazards; RMST; win ratio; competing risks (Fine–Gray vs cause-specific); LWYY/Andersen–Gill vs negative binomial; informative censoring by death |
| D5 | Multiplicity & inferential strategy | 8% | Graphical approaches; closed testing; gatekeeping; α-reallocation arithmetic; FDR in safety screening; estimation vs testing |
| D6 | Safety analytics, benefit–risk, immunogenicity | 15% | EAIR and exposure adjustment; MedDRA hierarchy; rare-event estimation with zero cells; Bayesian hierarchical AE screening; disproportionality metrics; structured benefit–risk; treatment-emergent ADA definitions and titre handling |
| D7 | Regulatory documentation & citation fidelity | 12% | SAP/CSR/protocol drafting; responses to information requests; correct and precise citation of ICH/FDA/EMA documents; **detection of fabricated citations** |
| D8 | Statistical programming & reproducibility | 15% | R and SAS; CDISC SDTM→ADaM derivation; define.xml; TFL production; seeds and MI reproducibility; environment pinning; QC/double programming; edge cases (zero cells, singular design matrices, ties) |
| D9 | Interpretation, critique & communication | 6% | Detecting flawed reasoning in a draft; p-value and CI misinterpretation; post-hoc subgroup overreach; explaining to non-statisticians without loss of accuracy |
| — | **Underspecification & abstention** | *(cross-cutting, ≥8% of items across D1–D9)* | Items that cannot be answered as posed; correct behaviour is to identify the missing estimand attribute / assumption, not to invent one |

The abstention stratum is not optional. In a regulated workflow, a model that confidently fills in an unstated variability assumption is more dangerous than one that scores lower but asks.

---

## 5. Item taxonomy and verification tiers

### 5.1 Item types

| Type | Description | Verification | Target share of weight |
|---|---|---|---|
| T1 | Hard multiple-select MCQ (≥5 options, includes "none of these", no single-best-answer cueing) | Deterministic | ≤20% |
| T2 | Numeric with declared tolerance (α-boundaries, sample size, RD and its CI, EAIR) | Deterministic | 15% |
| T3 | Executable code (R/Python; optionally SAS if a licensed runner exists) checked against unit tests and a reference estimate within tolerance | Deterministic (sandboxed execution) | 25% |
| T4 | Defect detection: an excerpt with *k* planted defects; scored by precision/recall against a reference defect list, severity-weighted | Semi-deterministic (matched against reference list by judge, calibrated) | 10% |
| T5 | Long-form artefact (SAP section, IR response, DMC charter section) graded against a weighted binary rubric | Judge + calibration | 20% |
| T6 | Underspecified/unanswerable; correct response identifies what is missing | Deterministic (abstention detected) or judge | 5% |
| T7 | Multi-step agentic task (SDTM → ADaM → TFL against a reference output) | Deterministic (artefact diff) | 5% |

**Design rule: ≥60% of scored weight must be deterministically verifiable.** This is the cheapest possible defence against judge bias, and it is what makes the primary endpoint credible without a large human-grading budget.

### 5.2 Severity classification (drives the primary endpoint)

Every rubric criterion and every defect carries a severity:

- **S1 Critical** — would invalidate an analysis or mislead a regulatory decision if undetected: wrong estimand–estimator pairing, misapplied multiplicity, a fabricated or misattributed citation, a derivation error that changes an efficacy conclusion, an undisclosed post-hoc analysis.
- **S2 Major** — materially wrong but likely caught in QC: inefficient or non-preferred method, missing sensitivity analysis, incomplete assumption statement.
- **S3 Minor** — imprecision, style, incompleteness with no inferential consequence.

Mean-score leaderboards hide S1 events. Report them as a separate endpoint.

---

## 6. Statistical analysis plan (summary; implementation in `analysis/scoring_and_inference.R`)

### 6.1 Endpoints

- **Primary: S1-free response rate** — proportion of items where the response contains zero critical errors, estimated per model with judge-misclassification correction where applicable. This is the endpoint that maps to the COU.
- **Co-primary: severity-weighted score** — \(\sum_c w_c \mathbb{1}[\text{criterion met}] / \sum_c w_c\), blueprint-weighted across domains.
- **Secondary:** per-domain scores (partially pooled, see §6.3); abstention accuracy on the underspecification stratum; fabricated-citation rate; **worst-case reliability** (5th percentile of per-item score, following HealthBench's observation that frontier models' remaining weakness is concentrated in worst-case behaviour rather than mean performance); cost per item; latency; within-item self-consistency across replicates.
- **Tertiary:** performance relative to the human-anchor stratum.

### 6.2 Replication and non-determinism

Run \(k \ge 5\) independent replicates per model × item at the temperature and settings intended for deployment. Do **not** assume `temperature=0` yields determinism — batching and mixture-of-experts routing make production endpoints non-deterministic in practice. Measure it: report the within-item, between-replicate variance component. If it is non-trivial, single-run leaderboards are not reproducible and should not be published as point estimates.

### 6.3 Primary model

Fit a generalised linear mixed model on the item-level binary outcome:

$$\text{logit}\,\Pr(Y_{mir}=1) = \alpha_m + b_i + c_{d(i)},\qquad b_i \sim N(0,\sigma^2_{\text{item}}),\ c_d \sim N(0,\sigma^2_{\text{domain}})$$

with **model as a fixed effect** and item and domain as random effects. Marginal accuracies and contrasts are obtained by integrating over the random effects.

**Push-back on the obvious alternative:** it is tempting to reach for item response theory here, and it is the wrong tool. IRT treats examinees as the sampled units; you will have 8–15 models, which is far too few to estimate item difficulty and discrimination stably. Treating the \(k\) replicates as additional "examinees" is worse — they are not independent draws from a population of examinees and would badly understate uncertainty. The GLMM above *is* a Rasch model with fixed examinee effects, which is identifiable at this scale and honest about what is being estimated. Reserve 2PL/3PL for the situation where you have accumulated dozens of models across releases; then item discrimination becomes estimable and useful for pruning uninformative items.

**Prune uninformative items.** Items answered correctly (or incorrectly) by every model at every replicate contribute nothing to discrimination and dilute the score. Flag and report them, retain them for longitudinal tracking, and exclude them from the discrimination-weighted secondary score.

### 6.4 Pairwise model comparison

Item-level responses are paired across models, so use paired inference. Primary: exact conditional McNemar on discordant items, with Holm adjustment across the \(m(m-1)/2\) comparisons. For an overall ranking with simultaneous intervals, fit a Bradley–Terry model on item-level win/loss.

### 6.5 Precision and item-bank size — read this before setting scope

Paired power depends on the **discordance rate** \(\psi = \pi_{01}+\pi_{10}\), not on the accuracies themselves. Using the standard paired-binary formula (verified against exact conditional simulation, which runs 2–3 percentage points conservative relative to the asymptotic approximation), the minimum detectable difference at 80% power, two-sided α=0.05:

| Scored items *n* | ψ = 0.10 | ψ = 0.20 | ψ = 0.30 |
|---|---|---|---|
| 200 | 6.2 pp | 8.8 pp | 10.8 pp |
| 400 | 4.4 pp | 6.2 pp | 7.6 pp |
| 800 | 3.1 pp | 4.4 pp | 5.4 pp |

**The implication is uncomfortable and worth stating in the eventual publication:** a 400-item benchmark cannot distinguish models separated by less than about 6 percentage points. Most published leaderboard gaps are smaller than that. Either size the bank to the difference you need to detect, or report intervals and decline to rank.

**Clustering makes this worse.** If items are authored in families sharing a stem (a common efficiency shortcut), the effective sample size shrinks by the design effect \(1+(m-1)\rho\). We simulated 60 families of 5 items with \(\rho=0.4\): design effect 2.6, and naive 95% intervals covered only 77.5% of the time versus 93.9% for cluster-robust intervals. **Recommendation: avoid item families in the scored set**, or record `family_id` and use it as the bootstrap resampling unit throughout.

**Scope recommendation:** v1.0 at 400 scored items (200 public + 200 private) accepting ~6 pp resolution, with the resolution stated prominently. Reaching 3 pp requires roughly 1,600 items and is a multi-year programme.

### 6.6 Generalisability theory (recommended, rarely done)

The score depends on which items you happened to write, which judges you happened to use, and which replicate you happened to draw. Decompose:

$$\sigma^2_{\text{total}} = \sigma^2_{\text{model}} + \sigma^2_{\text{item}} + \sigma^2_{\text{judge}} + \sigma^2_{\text{model}\times\text{item}} + \sigma^2_{\text{replicate}} + \sigma^2_{e}$$

and report the dependability index Φ (absolute decisions — appropriate here, since you care whether a model clears a threshold, not only how it ranks). Then run a D-study to determine how many items and judges are needed for Φ ≥ 0.80. This answers "how big must the bank be" from measurement theory rather than from convention, and it is the argument that will persuade a statistically literate reviewer.

### 6.7 Judge validation

- Ensemble of ≥2 judge models from different families; report agreement.
- Blind the judge to model identity; randomise response order; strip stylistic tells where feasible.
- Human-labelled calibration subset per graded stratum, sized per §3.4, refreshed whenever the judge model or rubric changes.
- Report judge–human agreement using a chance-corrected coefficient robust to marginal imbalance (Gwet's AC₁ is preferable to Cohen's κ here, since criterion prevalence is often extreme). HealthBench's meta-evaluation is the precedent: they reported grader macro-F1 ≈ 0.71 against physician labels and compared it to physician–physician agreement — reporting judge reliability *relative to human inter-rater reliability* is the right frame, because it sets the ceiling.
- **Never use the same model as both a graded subject and its own judge** in the same stratum.

---

## 7. Rubric authoring standard

Each T4/T5 item requires:

1. **Reference response** written by a qualified statistician (not model-generated; model-generated references import the judge's blind spots).
2. **Weighted binary criteria**, each independently checkable, tagged `required` / `alternative` / `disqualifying`, each with a severity.
3. **Two independent authors + adjudication.** HealthBench Professional adjudicated every criterion across three or more physicians; that is the standard to aim for, and disagreement rate at authoring is itself a useful item-quality metric — items where authors cannot agree are usually bad items, not hard items.
4. **Provenance:** the guidance section, publication, or SOP that establishes the criterion. An unsourced criterion is an opinion.
5. **`source_date`** and whether the item's underlying source post-dates candidate models' training cutoffs (needed for §8).

---

## 8. Contamination and robustness module

Report these alongside every score. They are diagnostics, not endpoints.

1. **Temporal partition.** Tag items whose sources post-date each model's stated training cutoff. A large pre/post gap for one model and not others is evidence of contamination, not capability.
2. **Perturbation suite.** For each scored item, generate variants: numeric substitution, entity/drug-name swap, logical inversion (negate the condition and invert the key), option shuffling, and distractor injection — the perturbation strategies used by StatABench and by the contamination literature. Report Δaccuracy (original → perturbed) as a **memorisation index**. A model whose score collapses under numeric substitution has memorised, not reasoned.
3. **Paraphrase-invariance.** Report mean and range across ≥3 prompt templates. A model whose rank changes across templates is not robustly better; prompt sensitivity is a reportable property, not noise to average away.
4. **Canary GUID** in every public item file, with the caveat that canaries are increasingly weak evidence and are not a substitute for a private split.
5. **n-gram overlap** against accessible corpora where feasible; acknowledge it misses paraphrased contamination.

---

## 9. Data governance — resolve before authoring

This will be the fastest route to the project being stopped, so handle it first.

- **No proprietary, unblinded, or patient-level sponsor data in prompts.** Sending trial data to an external inference endpoint is a disclosure. Build items on: the CDISC SDTM/ADaM pilot submission package, public R datasets (`survival::pbc`, `lung`, `colon`; `mmrm::fev_data`), published summary tables from peer-reviewed papers, and synthetic ADaM (e.g. `random.cdisc.data`).
- **De-identify and generify item stems.** Realism does not require a real study code. Replace compound and study identifiers with synthetic ones.
- **Licensing:** do not default to a permissive open-source licence for an internally authored item bank. The public split, the private split, and the harness code may need different licences, and internal proprietary or GxP-adjacent content requires legal review before release. Decide licence *before* the first commit, not before the first publication.
- **Endpoint terms:** record, per run, the provider, endpoint, retention terms, and whether the traffic was covered by a no-training agreement. This is part of the run's audit trail.
- **Reproducibility artefacts:** pin the environment (`renv.lock` for R, lockfile for Python), record model version strings and dates rather than aliases (`claude-…-YYYYMMDD`, not "latest"), record all sampling parameters, and version the item bank semantically. A benchmark result that cannot be re-derived is an anecdote.

---

## 10. Phased build plan

| Phase | Duration | Deliverable | Gate to proceed |
|---|---|---|---|
| **0. Scoping** | 2 weeks | COU statement; blueprint weights justified by a task-frequency analysis; data-governance and licence sign-off | Weights defensible to someone outside the authoring team |
| **1. Pilot (n≈60)** | 4–6 weeks | 60 items spanning all types, ~7 per domain; harness end-to-end; 3 models | Inter-author agreement measured; ≥80% of items discriminate (not all-pass/all-fail); harness reproducible |
| **2. Judge calibration** | 3 weeks | Human labels on ~200 judged responses; \(\hat q_0,\hat q_1\); calibration-size plan | Judge sensitivity and specificity both ≥0.85, or the stratum is rebuilt around deterministic verification |
| **3. Bank build to n=400** | 4–6 months | 200 public + 200 private; perturbation variants; human-anchor stratum | G-study Φ ≥ 0.80 for the intended number of items |
| **4. First full evaluation** | 3 weeks | All models × 400 items × 5 replicates; full SAP executed; report | Pre-registered analysis; no post-hoc endpoint changes |
| **5. Publication + maintenance** | ongoing | Public split released; private split retained; annual 25% rotation | — |

Phase 1 is the decision point. If pilot items do not discriminate between models, the blueprint is wrong (usually too easy, or measuring recall rather than judgement) and no amount of scaling will fix it.

---

## 11. Known limitations to state in any report

1. Benchmark performance is not evidence of fitness for a validated workflow; it is one input to a credibility assessment.
2. Rubric weights and blueprint weights embed the authoring team's judgement; a different team would produce different weights and different rankings. Report a sensitivity analysis over plausible weightings.
3. Judge-graded strata carry residual misclassification even after correction, because \(\hat q_0,\hat q_1\) are themselves estimated.
4. The public split degrades on release. Numbers from it after the first model generation are not comparable to numbers before it.
5. Items are static; deployed models operate with tools, retrieval, and multi-turn human correction. Single-turn scores are a lower bound on assisted performance and an upper bound on unsupervised reliability.
6. Model versions change under a fixed alias. Any longitudinal claim requires pinned version strings.

---

## 12. References

Regulatory
- FDA. *Considerations for the Use of Artificial Intelligence To Support Regulatory Decision-Making for Drug and Biological Products.* Draft guidance, January 2025 (docket FDA-2024-D-4689; comment period closed 7 April 2025; still in draft status as of mid-2026). https://www.fda.gov/media/184830/download — seven-step, risk-based credibility assessment framework; model risk defined by model influence × decision consequence; framework informed by ASME V&V 40.
- FDA & EMA. *Guiding Principles of Good AI Practice in Drug Development.* January 2026 (10 principles: human oversight, risk-based approach, standards, context of use, multidisciplinary expertise, data governance, model design, risk-based performance assessment, life-cycle management, essential information). https://www.fda.gov/media/189581/download ; https://www.ema.europa.eu/en/documents/other/guiding-principles-good-ai-practice-drug-development_en.pdf
- ICH E9(R1). *Addendum on Estimands and Sensitivity Analysis in Clinical Trials.* Step 4, November 2019. FDA adopted May 2021 — cite the FDA adoption as *FDA E9(R1) (2021)*, not as *ICH E9(R1), 2021*.

Benchmark and evaluation methodology
- Miller, E. *Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations.* arXiv:2411.00640, 2024 — super-population framing, paired comparison, cluster-adjusted standard errors (reported up to ~3× naive), power analysis for evals.
- Lee, C., Zeng, T., Jeong, J., Sohn, J., Lee, K. *How to Correctly Report LLM-as-a-Judge Evaluations.* arXiv:2511.21140, 2025 — bias-adjusted estimator and confidence interval propagating calibration uncertainty; adaptive calibration allocation. Code: https://github.com/UW-Madison-Lee-Lab/LLM-judge-reporting
- Rogan, W.J., Gladen, B. *Estimating prevalence from the results of a screening test.* Am J Epidemiol 1978;107:71–76.
- Lang, Z., Reiczigel, J. *Confidence limits for prevalence of disease adjusted for estimated sensitivity and specificity.* Prev Vet Med 2014;113:13–22.
- Agresti, A., Caffo, B. *Simple and effective confidence intervals for proportions...* Am Stat 2000;54:280–288.
- OpenAI. *HealthBench* (May 2025) and *HealthBench Professional* (2026) — rubric architecture, model-grader meta-evaluation against physician labels, worst-case reliability as a reported axis.
- *Clinical Safety-Effectiveness Dual-Track Benchmark (CSEDB).* npj Digital Medicine, 2025 — weighted-consequence metrics; 13.3% performance drop in high-risk scenarios.
- Zhu et al. *Are Large Language Models Good Statisticians? (StatQA).* NeurIPS 2024 Datasets & Benchmarks. https://statqa.github.io/
- Song et al. *StatLLM: a dataset for evaluating LLM performance in statistical analysis.* Scientific Data, 2026.
- *StatABench.* arXiv:2606.22977 — perturbation strategies: rewriting, logical inversion, distractor injection and shuffling.
- *CTBench.* arXiv:2406.17888.
- NEJM AI. *Assessment of Large Language Models in Clinical Reasoning: A Novel Benchmarking Study* — script-concordance testing; MCQ benchmarks overstate reasoning ability relative to expert comparators.
- *A Survey on Data Contamination for Large Language Models.* arXiv:2502.14425.
- Yang et al. *Rethinking Benchmark and Contamination for Language Models with Rephrased Samples.* arXiv:2311.04850.

Numbers in §3.4 and §6.5 were derived by Monte Carlo simulation for this protocol; the simulation code is in `analysis/scoring_and_inference.R` (R port) with the verification harness described in the accompanying README.
