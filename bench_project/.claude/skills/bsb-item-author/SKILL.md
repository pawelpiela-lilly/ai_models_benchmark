---
name: bsb-item-author
description: Author or review a BioStatBench benchmark item to the house standard. Use when the user asks to write, draft, review, critique, or adjudicate a benchmark item, rubric, answer key, or perturbation variant for the biostatistics benchmark; or mentions item IDs of the form D1-EST-003, item types T1-T7, or severity tags S1/S2/S3.
---

# Authoring a BioStatBench item

A good item discriminates between systems that differ in competence. Most first drafts fail
because they discriminate between systems that differ in *recall*.

## Before writing, decide

1. **What would a competent statistician get wrong, and why?** If the answer is "nothing",
   the item is a lookup and belongs in no scored bank. If the answer is "they would need to
   look it up", it is testing retrieval.
2. **Is there more than one defensible answer?** Much of this domain has no unique right
   answer. If so the rubric needs an `alternative_group` enumerating the defensible
   positions, each with its own required justification. Scoring conformity instead of
   competence is the most common way these banks go wrong.
3. **Which verification tier?** Prefer deterministic. Reach for a judge only when the
   construct genuinely requires free text.

## Required structure

Validate against `schema/item_schema.json`. Every item needs:

- a `derivation` for any numeric key, re-runnable by a reviewer;
- rubric criteria that are independently checkable, each with a severity and a
  **provenance** (guidance section, publication, or SOP — not "standard practice");
- at least one `disqualifying` criterion naming the specific wrong behaviour the item
  exists to detect;
- at least two perturbation variants, normally numeric substitution and logical inversion,
  with the key delta recorded. An item with no perturbation cannot be checked for
  memorisation;
- an `authoring.source_date`, and whether the newest source post-dates model cutoffs.

## Severity, calibrated to a regulated workflow

- **S1** would invalidate an analysis or mislead a regulatory decision: wrong
  estimand–estimator pairing, misapplied multiplicity, fabricated citation, a derivation
  error that changes a conclusion, an undisclosed post-hoc analysis, or asserting that
  output was verified when nothing was executed.
- **S2** materially wrong but likely caught in QC.
- **S3** imprecision with no inferential consequence.

## Agentic-surface items

On the Claude Code surface, add criteria for conduct, not just for the answer:

- did it execute a verification command, or assert success without evidence;
- did it stay inside the task scope, or edit files it was not asked to touch;
- did it stop and ask when the task was underspecified;
- did it report a failure honestly when execution failed.

These are S1 in a validated workflow. An agent that edits a validated program uninstructed
is a deviation regardless of whether the edit was an improvement.

## Self-check before proposing the item

- Would every current frontier model get this right? → too easy, it will not discriminate.
- Would none get it right? → check the key is correct before assuming the item is hard.
- Could the answer be produced by pattern-matching the phrasing without the reasoning? →
  add a numeric-substitution perturbation and see.
- Does the key encode a house convention rather than a defensible position? → convert to
  an `alternative_group`.
- Does the stem leak the answer through its framing? Multi-select stems in particular tend
  to signal how many options are correct.

## When reviewing someone else's item

Review adversarially and independently. Write your own answer *before* reading the key, then
compare. Disagreement between authors is the signal to watch: an item two competent
statisticians answer differently is usually underspecified rather than hard, and should be
rewritten or converted to an alternatives rubric. Record the initial disagreement rate in
`authoring.author_disagreement`; it is a useful item-quality metric in its own right.

Never consult `_keys/` while authoring or reviewing.
