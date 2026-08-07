#!/usr/bin/env python3
"""
BioStatBench evaluation harness.

Emits a long-format CSV with exactly the columns that analysis/scoring_and_inference.R
expects, so the analysis is never coupled to the harness internals.

Design commitments, each of which exists to prevent a specific failure:

  * Every call is cached on a hash of (model_id, prompt, sampling params, replicate).
    Re-running must not silently re-bill or, worse, silently re-sample and change
    published numbers.
  * Run metadata records the provider, the pinned model version string, and whether
    the endpoint was covered by a no-training / zero-retention agreement. Sending
    private-split items to a retaining endpoint is a form of publication; if you
    cannot record that field, you do not know whether your private split is
    still private.
  * `--split private` refuses to run against a provider not marked no_retention.
  * Deterministic grading is preferred wherever the item type allows it. Only
    T4/T5/T6 fall through to the judge, and judge output must be passed through
    the Rogan-Gladen correction in the R layer before reporting.
  * Code items execute in a subprocess with a wall-clock timeout. THIS IS NOT A
    SANDBOX. Before running untrusted model-generated code, put this behind a
    container with no network and a read-only mount. See run_code().

Usage
    python run_bench.py --items ../items/seed_items.yaml --split public \
        --models dryrun:baseline dryrun:strong --replicates 5 --out results.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

import yaml

SCHEMA_VERSION = "0.1"


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

@dataclass
class ProviderSpec:
    """Everything that must be recorded for a run to be reproducible and auditable."""
    name: str
    model_version: str          # pinned, dated string - never an alias like "latest"
    endpoint: str
    no_retention: bool          # covered by a zero-data-retention / no-training agreement
    temperature: float = 1.0
    top_p: float | None = None
    max_tokens: int = 4096
    extra: dict[str, Any] = field(default_factory=dict)


class Provider:
    def __init__(self, spec: ProviderSpec):
        self.spec = spec

    def complete(self, system: str | None, prompt: str, seed: int) -> tuple[str, dict]:
        raise NotImplementedError


class DryRunProvider(Provider):
    """Deterministic stand-in so the harness can be exercised without API keys.

    `skill` in [0, 1] controls the chance of returning the keyed answer. This exists
    to dry-run the pipeline and the SAP, not to simulate model behaviour.
    """

    def __init__(self, spec: ProviderSpec, skill: float, key_hint: dict | None = None):
        super().__init__(spec)
        self.skill = skill
        self.key_hint = key_hint or {}

    def complete(self, system, prompt, seed):
        rng = random.Random(hashlib.sha256(f"{self.spec.name}|{prompt}|{seed}".encode()).hexdigest())
        good = rng.random() < self.skill
        hint = self.key_hint
        if hint.get("kind") == "mcq":
            opts = hint["all_options"]
            ans = hint["correct"] if good else rng.sample(opts, k=min(2, len(opts)))
            return "Answer: " + ", ".join(sorted(ans)), {"dry_run": True}
        if hint.get("kind") == "numeric":
            v = hint["value"]
            v = v if good else v * (1 + rng.choice([-0.4, 0.4]))
            return f"The estimate is {v:.6g}.", {"dry_run": True}
        if hint.get("kind") == "abstain":
            if good:
                return ("I cannot compute this as posed. The following inputs are missing: "
                        + "; ".join(hint["missing"]) + "."), {"dry_run": True}
            return "You need about 250 patients per arm.", {"dry_run": True}
        return ("[dry-run free-text response]" if good else "[dry-run poor response]"), {"dry_run": True}


class AnthropicProvider(Provider):
    """Thin wrapper. Kept minimal on purpose: the harness must not depend on SDK
    surface that changes between versions."""

    def complete(self, system, prompt, seed):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise SystemExit("pip install anthropic") from e
        client = anthropic.Anthropic()
        kwargs: dict[str, Any] = dict(
            model=self.spec.model_version,
            max_tokens=self.spec.max_tokens,
            temperature=self.spec.temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        if system:
            kwargs["system"] = system
        if self.spec.top_p is not None:
            kwargs["top_p"] = self.spec.top_p
        t0 = time.time()
        r = client.messages.create(**kwargs)
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        meta = {
            "latency_s": round(time.time() - t0, 3),
            "input_tokens": r.usage.input_tokens,
            "output_tokens": r.usage.output_tokens,
            "stop_reason": r.stop_reason,
        }
        return text, meta


# Add further providers here. Each must populate ProviderSpec honestly, including
# no_retention - do not default it to True.


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class Cache:
    def __init__(self, path: Path):
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)

    def _key(self, spec: ProviderSpec, prompt: str, system: str | None, replicate: int) -> str:
        payload = json.dumps(
            {
                "model_version": spec.model_version,
                "endpoint": spec.endpoint,
                "temperature": spec.temperature,
                "top_p": spec.top_p,
                "max_tokens": spec.max_tokens,
                "system": system,
                "prompt": prompt,
                "replicate": replicate,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, spec, prompt, system, replicate):
        f = self.path / f"{self._key(spec, prompt, system, replicate)}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def put(self, spec, prompt, system, replicate, value):
        f = self.path / f"{self._key(spec, prompt, system, replicate)}.json"
        f.write_text(json.dumps(value))


# ---------------------------------------------------------------------------
# Deterministic graders
# ---------------------------------------------------------------------------

_LETTER = re.compile(r"\b([A-H])\b")


def grade_mcq(response: str, key: dict) -> tuple[int, dict]:
    """Exact set match. Partial credit is deliberately NOT given: on a multi-select
    item about estimand strategies, selecting a superset that includes a wrong
    option is a substantive error, not a near miss."""
    tail = response[-400:] if len(response) > 400 else response
    picked = set(_LETTER.findall(tail.upper()))
    valid = set(key["mcq_options"].keys())
    picked &= valid
    correct = set(key["mcq_correct"])
    return int(picked == correct), {"picked": sorted(picked), "expected": sorted(correct)}


_NUM = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


def grade_numeric(response: str, key: dict) -> tuple[int, dict]:
    """Match against the LAST numeric token, after stripping thousands separators.

    Known limitation, stated rather than hidden: last-number extraction fails when
    a model writes a summary sentence after its answer. Prefer requiring a fenced
    final line (`ANSWER: <value>`) in the prompt template and parse that; fall back
    to this only for legacy items.
    """
    spec = key["numeric"]
    if spec is None or spec.get("value") is None:
        return -1, {"reason": "key not populated - generate it from the reference solution"}
    m = re.search(r"ANSWER:\s*(" + _NUM.pattern + ")", response)
    cand = m.group(1) if m else (_NUM.findall(response.replace(",", "")) or [None])[-1]
    if cand is None:
        return 0, {"reason": "no number found"}
    val = float(str(cand).replace(",", ""))
    tol_a = spec.get("tolerance_abs")
    tol_r = spec.get("tolerance_rel")
    ok = False
    if tol_a is not None:
        ok = ok or abs(val - spec["value"]) <= tol_a
    if tol_r is not None:
        ok = ok or abs(val - spec["value"]) <= tol_r * abs(spec["value"])
    return int(ok), {"parsed": val, "expected": spec["value"]}


_ABSTAIN = re.compile(
    r"(cannot|can't|unable to|not possible to)\s+(compute|calculate|determine|answer|provide)"
    r"|(missing|not specified|unspecified|need(?:ed)?|require[sd]?|would need)\b",
    re.I,
)


def grade_abstention(response: str, key: dict) -> tuple[int, dict]:
    """Credit requires BOTH signalling underspecification AND naming the specific
    missing inputs. A vague 'it depends' is not the target behaviour."""
    missing = key.get("missing_information", [])
    signalled = bool(_ABSTAIN.search(response))
    low = response.lower()
    hits = [m for m in missing if any(tok in low for tok in _keywords(m))]
    frac = len(hits) / len(missing) if missing else 0.0
    return int(signalled and frac >= 0.5), {
        "signalled": signalled,
        "identified": hits,
        "fraction_identified": round(frac, 3),
    }


def _keywords(phrase: str) -> list[str]:
    stop = {"the", "and", "for", "of", "in", "or", "a", "an", "to", "is", "by", "any", "its"}
    return [w for w in re.findall(r"[a-z]{4,}", phrase.lower()) if w not in stop][:3]


def run_code(response: str, key: dict, lang: str = "R", timeout: int = 120) -> tuple[int, dict]:
    """Extract a fenced code block, write it out, run the item's test script.

    SECURITY: this executes model-generated code. Run the whole harness inside a
    container with no network egress and no mounts you care about, or replace this
    with a call to a proper sandbox service. Do not run it on a workstation with
    credentials in the environment.
    """
    blocks = re.findall(r"```(?:r|R|python|py)?\s*\n(.*?)```", response, re.S)
    if not blocks:
        return 0, {"reason": "no fenced code block"}
    code = max(blocks, key=len)
    test_script = key.get("test_script")
    if not test_script or not Path(test_script).exists():
        return -1, {"reason": "no executable test script for this item"}
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / ("solution.R" if lang == "R" else "solution.py")
        src.write_text(code)
        cmd = (["Rscript", test_script, str(src)] if lang == "R"
               else [sys.executable, test_script, str(src)])
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                               env={"PATH": os.environ.get("PATH", ""), "HOME": td})
        except subprocess.TimeoutExpired:
            return 0, {"reason": "timeout"}
    return int(p.returncode == 0), {"stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:]}


DETERMINISTIC: dict[str, Callable] = {
    "T1_mcq": grade_mcq,
    "T2_numeric": grade_numeric,
    "T6_underspecified": grade_abstention,
}


# ---------------------------------------------------------------------------
# Judge (stub - deliberately thin)
# ---------------------------------------------------------------------------

JUDGE_TEMPLATE = """You are grading one response against one criterion. Answer with a single word.

CRITERION ({severity}, {kind}): {criterion}

RESPONSE UNDER REVIEW:
---
{response}
---

Is the criterion met? Reply exactly MET or NOT_MET."""


def judge_criteria(judge: Provider, response: str, rubric: list[dict]) -> dict[str, bool]:
    """One call per criterion. More expensive than a single batched call, and worth it:
    batched grading lets the judge's assessment of one criterion contaminate another,
    and the per-criterion form is what the calibration set measures.

    The judge must not see the model identity. Do not pass model names in here.
    """
    out = {}
    for c in rubric:
        prompt = JUDGE_TEMPLATE.format(
            severity=c["severity"], kind=c["kind"], criterion=c["text"], response=response
        )
        txt, _ = judge.complete(None, prompt, seed=0)
        out[c["criterion_id"]] = "MET" in txt.upper() and "NOT_MET" not in txt.upper()
    return out


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def resolve_answer_key(item: dict, items_path: Path) -> None:
    """Populate item['answer_key'] from answer_key_ref if present, in place.

    answer_key_ref is written repo-root-relative (e.g. "_keys/D2-GSD-012.yaml"),
    matching the CLAUDE.md layout where _keys/ is a sibling of items/.
    """
    ref = item.get("answer_key_ref")
    if not ref:
        return
    key_path = (items_path.parent.parent / ref).resolve()
    if not key_path.is_file():
        raise SystemExit(f"{item['id']}: answer_key_ref {ref} does not exist "
                          f"(resolved to {key_path})")
    item["answer_key"] = yaml.safe_load(key_path.read_text()) or {}


def load_items(path: Path, split: str | None) -> list[dict]:
    doc = yaml.safe_load(path.read_text())
    items = [i for i in doc["items"] if not (i.get("retired") or {}).get("is_retired")]
    if split:
        items = [i for i in items if i.get("split") == split]
    for it in items:
        resolve_answer_key(it, path)
    return items


def dry_hint(item: dict) -> dict:
    ak = item.get("answer_key") or {}
    if item["type"] == "T1_mcq" and ak.get("mcq_correct"):
        return {"kind": "mcq", "correct": ak["mcq_correct"],
                "all_options": list(ak["mcq_options"].keys())}
    if item["type"] == "T2_numeric" and (ak.get("numeric") or {}).get("value") is not None:
        return {"kind": "numeric", "value": ak["numeric"]["value"]}
    if item["type"] == "T6_underspecified":
        return {"kind": "abstain", "missing": ak.get("missing_information", [])}
    return {}


def build_provider(spec_str: str, item_hint: dict) -> Provider:
    """`dryrun:<skill_label>` or `anthropic:<pinned-version>`."""
    kind, _, rest = spec_str.partition(":")
    if kind == "dryrun":
        skill = {"baseline": 0.45, "mid": 0.65, "strong": 0.82}.get(rest, 0.6)
        return DryRunProvider(
            ProviderSpec(name=spec_str, model_version=f"dryrun-{rest}",
                         endpoint="local", no_retention=True),
            skill=skill, key_hint=item_hint)
    if kind == "anthropic":
        return AnthropicProvider(
            ProviderSpec(name=spec_str, model_version=rest,
                         endpoint="api.anthropic.com",
                         no_retention=bool(int(os.environ.get("BSB_NO_RETENTION", "0")))))
    raise SystemExit(f"unknown provider: {spec_str}")


FIELDS = ["item_id", "domain", "type", "difficulty", "split", "family_id",
          "post_cutoff_source", "perturbation_kind", "model", "model_version",
          "replicate", "correct", "score", "s1_free", "verification",
          "grader_detail", "latency_s", "input_tokens", "output_tokens"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", required=True, type=Path)
    ap.add_argument("--split", default=None, choices=["public", "private", "calibration", "human_anchor"])
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--replicates", type=int, default=5)
    ap.add_argument("--judge", default=None, help="provider spec for the judge; must differ from every subject")
    ap.add_argument("--cache", type=Path, default=Path(".bsb_cache"))
    ap.add_argument("--out", type=Path, default=Path("results.csv"))
    ap.add_argument("--include-perturbations", action="store_true")
    a = ap.parse_args(argv)

    items = load_items(a.items, a.split)
    if not items:
        raise SystemExit("no items matched")
    if a.judge and a.judge in a.models:
        raise SystemExit("the judge must not also be a graded subject")

    cache = Cache(a.cache)
    rows: list[dict] = []
    run_meta: dict[str, Any] = {"schema": SCHEMA_VERSION, "started": time.strftime("%FT%TZ", time.gmtime()),
                                "split": a.split, "replicates": a.replicates, "providers": {}}

    for spec_str in a.models:
        prov0 = build_provider(spec_str, {})
        if a.split == "private" and not prov0.spec.no_retention:
            raise SystemExit(
                f"refusing to send private-split items to {spec_str}: endpoint is not "
                f"recorded as zero-retention. Set BSB_NO_RETENTION=1 only if a "
                f"no-training agreement is actually in force.")
        run_meta["providers"][spec_str] = asdict(prov0.spec)

        for item in items:
            variants = [("original", item["prompt"]["text"])]
            if a.include_perturbations:
                variants += [(p["kind"], p["text"]) for p in item.get("perturbations", [])]

            for pkind, ptext in variants:
                key = item.get("answer_key") or {}
                prov = build_provider(spec_str, dry_hint(item))
                system = item["prompt"].get("system")

                for r in range(1, a.replicates + 1):
                    hit = cache.get(prov.spec, ptext, system, r)
                    if hit is None:
                        text, meta = prov.complete(system, ptext, seed=r)
                        cache.put(prov.spec, ptext, system, r, {"text": text, "meta": meta})
                    else:
                        text, meta = hit["text"], hit["meta"]

                    grader = DETERMINISTIC.get(item["type"])
                    if grader and pkind == "original":
                        correct, detail = grader(text, key)
                    elif grader:
                        # perturbed variants need their own key; skip until populated
                        correct, detail = -1, {"reason": "perturbation key not populated"}
                    else:
                        correct, detail = -1, {"reason": "requires judge or execution"}

                    rows.append({
                        "item_id": item["id"], "domain": item["domain"], "type": item["type"],
                        "difficulty": item["difficulty"], "split": item["split"],
                        "family_id": item.get("family_id") or "",
                        "post_cutoff_source": item["authoring"].get("post_cutoff_source", False),
                        "perturbation_kind": pkind, "model": spec_str,
                        "model_version": prov.spec.model_version, "replicate": r,
                        "correct": correct, "score": correct if correct >= 0 else "",
                        "s1_free": "", "verification": item["verification"],
                        "grader_detail": json.dumps(detail)[:500],
                        "latency_s": meta.get("latency_s", ""),
                        "input_tokens": meta.get("input_tokens", ""),
                        "output_tokens": meta.get("output_tokens", ""),
                    })

    with a.out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    a.out.with_suffix(".meta.json").write_text(json.dumps(run_meta, indent=2))

    graded = [r for r in rows if r["correct"] >= 0]
    ungraded = len(rows) - len(graded)
    print(f"wrote {len(rows)} rows to {a.out} ({ungraded} awaiting judge/execution)")
    for m in a.models:
        g = [r["correct"] for r in graded if r["model"] == m]
        if g:
            print(f"  {m}: deterministic accuracy {sum(g)/len(g):.3f} over n={len(g)}")
    print(f"run metadata -> {a.out.with_suffix('.meta.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
