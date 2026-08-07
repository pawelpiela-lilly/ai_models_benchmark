#!/usr/bin/env python3
"""
BioStatBench - Claude Code runner.

Runs each item as a headless Claude Code session in an isolated workspace and emits
rows compatible with analysis/scoring_and_inference.R, plus the agentic-conduct
columns that only exist on this surface.

WHAT THIS MEASURES

Not "the model". A `claude -p` run is model + agent loop + tool set + permission
mode + effort level + whatever context was auto-discovered. Treat the whole thing as
the unit under test and pin every component. See protocol/ADDENDUM_A_claude_code.md.

FIVE THINGS THIS FILE DEFENDS AGAINST

1. Answer-key leakage. Claude Code has Bash. `cwd` is not a security boundary -
   `cat ../../keys.yaml` works. Keys and tests are never placed inside the item
   workspace, and --keys-dir is asserted to be outside --work-root. Real isolation
   is a container with only the workspace mounted; this runner checks the weaker
   invariant and records it.
2. Silent model substitution. --fallback-model is never passed, and the model
   reported in the system/init event is compared against the requested model. A
   mismatch marks the row model_mismatch=1 and it must be excluded from the
   primary analysis, not silently averaged in.
3. Retrieval instead of reasoning. WebSearch and WebFetch are denied by default.
   With web access on, any public-split item can be looked up, and the benchmark
   measures retrieval. Enable it only as a declared factor (--allow-web).
4. Configuration drift between machines. --bare by default: no CLAUDE.md, hooks,
   skills, plugins, MCP servers or auto-memory. Docs note --bare will become the
   -p default in a future release, so scripting to it now is forward-compatible.
5. Fabricated success. The transcript is parsed for evidence that the agent
   actually executed a verification command. "Tests pass" with no Bash call is a
   critical error, and on this surface it is detectable.

USAGE

    # verify command construction and the parser without spending anything
    python3 cc_runner.py --self-test

    # real run
    export ANTHROPIC_API_KEY=...        # --bare does not use subscription login
    python3 cc_runner.py \
        --items ../items/seed_items.yaml --split public \
        --models claude-sonnet-5 claude-opus-5 \
        --effort medium --replicates 5 \
        --work-root /tmp/bsb_work --keys-dir ../items/_keys \
        --max-budget-usd 0.75 --max-turns 30 --concurrency 6 \
        --out cc_results.csv
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

RUNNER_VERSION = "0.1"

# Aliases resolve to "the latest" and therefore drift. Pin full names and record
# `claude --version` with every run. Verify the current lineup against
# https://code.claude.com/docs/en/model-config before a scored run.
KNOWN_MODELS = {
    "opus-latest": "opus",
    "sonnet-latest": "sonnet",
    "haiku-latest": "haiku",
}

# Pinned full names only. Used solely to warn on a scored run; NOT the same set as
# KNOWN_MODELS above, which exists to resolve *aliases* for mismatch detection. If
# this warning used KNOWN_MODELS's keys instead, passing an alias would never
# trigger it - the alias would be "known" and therefore silently accepted.
PINNED_MODELS = {
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "claude-fable-5",
}

# Denied on every scored run. WebSearch/WebFetch would let the agent retrieve
# public-split items; mcp__* keeps the tool surface pinned.
DEFAULT_DENY = ["WebSearch", "WebFetch", "mcp__*"]

# Structured output beats regex-scraping a free-text answer. --json-schema makes
# the agent emit a validated object into `.structured_output`.
ANSWER_SCHEMAS: dict[str, dict] = {
    "T1_mcq": {
        "type": "object",
        "properties": {
            "selected_options": {"type": "array", "items": {"type": "string"}},
            "reasoning": {"type": "string"},
        },
        "required": ["selected_options"],
        "additionalProperties": False,
    },
    "T2_numeric": {
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "units": {"type": "string"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["value"],
        "additionalProperties": False,
    },
    "T6_underspecified": {
        "type": "object",
        "properties": {
            "answerable": {"type": "boolean"},
            "missing_information": {"type": "array", "items": {"type": "string"}},
            "answer": {"type": ["string", "null"]},
        },
        "required": ["answerable", "missing_information"],
        "additionalProperties": False,
    },
    "T4_defect": {
        "type": "object",
        "properties": {
            "defects": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string"},
                        "defect": {"type": "string"},
                        "why": {"type": "string"},
                        "remedy": {"type": "string"},
                    },
                    "required": ["location", "defect"],
                },
            }
        },
        "required": ["defects"],
        "additionalProperties": False,
    },
}

# Commands that count as the agent verifying its own work.
VERIFY_RE = re.compile(
    r"\b(Rscript|R\s+-e|R\s+CMD\s+check|testthat|pytest|python3?\s+-m\s+pytest"
    r"|python3?\s+.*test|make\s+test|npm\s+test)\b"
)


# ---------------------------------------------------------------------------
# Configuration of a single arm of the design
# ---------------------------------------------------------------------------

@dataclass
class Arm:
    model: str                      # full pinned name
    effort: str | None = None       # low|medium|high|xhigh|max (availability is model-dependent)
    bare: bool = True
    tools: str | None = None        # e.g. "Bash,Read,Edit"; "" disables all tools
    allowed_tools: list[str] = field(default_factory=lambda: ["Bash", "Read", "Edit", "Write", "Grep", "Glob"])
    denied_tools: list[str] = field(default_factory=lambda: list(DEFAULT_DENY))
    permission_mode: str = "acceptEdits"
    max_turns: int = 30
    max_budget_usd: float = 1.0
    allow_web: bool = False

    @property
    def arm_id(self) -> str:
        bits = [self.model, self.effort or "default", "bare" if self.bare else "full",
                "web" if self.allow_web else "noweb", self.permission_mode]
        return "|".join(bits)


def build_command(arm: Arm, item: dict, prompt_path: Path, session_id: str) -> list[str]:
    """Construct the `claude -p` invocation.

    Deliberately absent: --fallback-model (silent model substitution destroys
    attribution) and --dangerously-skip-permissions (an explicit permission mode
    plus an allow list is auditable; a blanket bypass is not).
    """
    cmd = ["claude", "-p", f"@{prompt_path.name}",
           "--model", arm.model,
           "--output-format", "stream-json",
           "--verbose",
           "--permission-mode", arm.permission_mode,
           "--max-turns", str(arm.max_turns),
           "--max-budget-usd", f"{arm.max_budget_usd:.2f}",
           "--session-id", session_id,
           "--no-session-persistence",
           "--strict-mcp-config"]
    if arm.bare:
        cmd.append("--bare")
    if arm.effort:
        cmd += ["--effort", arm.effort]
    if arm.tools is not None:
        cmd += ["--tools", arm.tools]
    if arm.allowed_tools:
        cmd += ["--allowedTools", ",".join(arm.allowed_tools)]
    deny = [d for d in arm.denied_tools if not (arm.allow_web and d in ("WebSearch", "WebFetch"))]
    if deny:
        cmd += ["--disallowedTools", ",".join(deny)]
    schema = ANSWER_SCHEMAS.get(item["type"])
    if schema:
        cmd += ["--json-schema", json.dumps(schema, separators=(",", ":"))]
    return cmd


# ---------------------------------------------------------------------------
# Transcript parsing
# ---------------------------------------------------------------------------

@dataclass
class Transcript:
    init_model: str | None = None
    tools_available: list[str] = field(default_factory=list)
    plugin_errors: list[dict] = field(default_factory=list)
    mcp_server_errors: list[dict] = field(default_factory=list)
    tool_calls: list[tuple[str, str]] = field(default_factory=list)  # (tool, short repr of input)
    api_retries: list[str] = field(default_factory=list)             # error categories
    result_text: str = ""
    structured_output: Any = None
    session_id: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    is_error: bool = False
    result_subtype: str | None = None

    @property
    def executed_verification(self) -> bool:
        return any(t == "Bash" and VERIFY_RE.search(s) for t, s in self.tool_calls)

    @property
    def n_bash(self) -> int:
        return sum(1 for t, _ in self.tool_calls if t == "Bash")

    @property
    def n_edits(self) -> int:
        return sum(1 for t, _ in self.tool_calls if t in ("Edit", "Write", "MultiEdit", "NotebookEdit"))


def parse_stream_json(lines: list[str]) -> Transcript:
    """Parse newline-delimited stream-json into the fields the analysis needs.

    Tolerant by design: unknown event types are skipped rather than raising, because
    new event types appear with point releases and a benchmark run must not die on one.
    """
    t = Transcript()
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        etype, sub = ev.get("type"), ev.get("subtype")
        if etype == "system" and sub == "init":
            t.init_model = ev.get("model")
            t.tools_available = ev.get("tools", []) or []
            t.plugin_errors = ev.get("plugin_errors", []) or []
            t.mcp_server_errors = ev.get("mcp_server_errors", []) or []
            t.session_id = ev.get("session_id") or t.session_id
        elif etype == "system" and sub == "api_retry":
            t.api_retries.append(str(ev.get("error")))
        elif etype == "assistant":
            for blk in (ev.get("message", {}) or {}).get("content", []) or []:
                if blk.get("type") == "tool_use":
                    inp = blk.get("input", {}) or {}
                    short = inp.get("command") or inp.get("file_path") or inp.get("pattern") or ""
                    t.tool_calls.append((blk.get("name", "?"), str(short)[:400]))
        elif etype == "result":
            t.result_subtype = sub
            t.result_text = ev.get("result") or ""
            t.structured_output = ev.get("structured_output")
            t.cost_usd = ev.get("total_cost_usd")
            t.duration_ms = ev.get("duration_ms")
            t.num_turns = ev.get("num_turns")
            t.is_error = bool(ev.get("is_error"))
            t.session_id = ev.get("session_id") or t.session_id
    return t


# ---------------------------------------------------------------------------
# Workspace isolation
# ---------------------------------------------------------------------------

def assert_key_isolation(work_root: Path, keys_dir: Path) -> None:
    wr, kd = work_root.resolve(), keys_dir.resolve()
    if kd == wr or wr in kd.parents or kd in wr.parents:
        raise SystemExit(
            f"--keys-dir ({kd}) must not be inside or contain --work-root ({wr}).\n"
            "Claude Code has Bash; the working directory is not a boundary. Keys must live\n"
            "on a path the run cannot reach, ideally outside the container entirely.")


def prepare_workspace(item: dict, items_path: Path, work_root: Path, run_key: str) -> Path:
    """One ephemeral directory per run, containing only the task inputs.

    Nothing about the answer, the rubric, the severity weights, or sibling items
    goes in here.
    """
    ws = work_root / run_key
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    prompt = item["prompt"]["text"]
    if item["prompt"].get("system"):
        prompt = item["prompt"]["system"] + "\n\n" + prompt
    (ws / "TASK.md").write_text(prompt)
    for att in item["prompt"].get("attachments", []) or []:
        src = (items_path.parent / att["path"]).resolve()
        if src.exists():
            dst = ws / Path(att["path"]).name
            dst.write_bytes(src.read_bytes())
    return ws


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def run_one(arm: Arm, item: dict, items_path: Path, work_root: Path,
            replicate: int, timeout_s: int, dry_run: bool) -> dict:
    run_key = f"{item['id']}__{re.sub(r'[^A-Za-z0-9]+', '-', arm.arm_id)}__r{replicate}"
    ws = prepare_workspace(item, items_path, work_root, run_key)
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, run_key))
    cmd = build_command(arm, item, ws / "TASK.md", session_id)

    row: dict[str, Any] = {
        "item_id": item["id"], "domain": item["domain"], "type": item["type"],
        "difficulty": item["difficulty"], "split": item["split"],
        "family_id": item.get("family_id") or "",
        "post_cutoff_source": item["authoring"].get("post_cutoff_source", False),
        "perturbation_kind": "original",
        "surface": "claude_code", "model": arm.model, "arm_id": arm.arm_id,
        "effort": arm.effort or "default", "bare": int(arm.bare),
        "allow_web": int(arm.allow_web), "permission_mode": arm.permission_mode,
        "replicate": replicate, "runner_version": RUNNER_VERSION,
        "workspace": str(ws), "cmd": " ".join(cmd),
    }

    if dry_run:
        row.update(status="dry_run", correct="", model_mismatch=0)
        return row

    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=timeout_s)
        stdout, stderr, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        stdout = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = "TIMEOUT"
        rc = -9
    wall = round(time.time() - t0, 2)

    tr = parse_stream_json(stdout.splitlines())
    (ws / "_transcript.jsonl").write_text(stdout)
    if stderr:
        (ws / "_stderr.txt").write_text(stderr[-20000:])

    mismatch = int(bool(tr.init_model) and tr.init_model != arm.model
                   and KNOWN_MODELS.get(arm.model) != tr.init_model)
    row.update({
        "status": "ok" if rc == 0 and not tr.is_error else f"fail_rc{rc}",
        "exit_code": rc, "wall_s": wall,
        "init_model": tr.init_model or "", "model_mismatch": mismatch,
        "num_turns": tr.num_turns if tr.num_turns is not None else "",
        "cost_usd": tr.cost_usd if tr.cost_usd is not None else "",
        "duration_ms": tr.duration_ms if tr.duration_ms is not None else "",
        "result_subtype": tr.result_subtype or "",
        "n_tool_calls": len(tr.tool_calls), "n_bash": tr.n_bash, "n_edits": tr.n_edits,
        "executed_verification": int(tr.executed_verification),
        "api_retries": ";".join(tr.api_retries),
        "plugin_errors": json.dumps(tr.plugin_errors) if tr.plugin_errors else "",
        "mcp_server_errors": json.dumps(tr.mcp_server_errors) if tr.mcp_server_errors else "",
        "structured_output": json.dumps(tr.structured_output) if tr.structured_output is not None else "",
        "result_chars": len(tr.result_text),
    })

    # Agentic-conduct flag that only exists on this surface: asserting verification
    # happened without any evidence of executing anything. On COU B this is critical.
    claims = re.search(r"\b(tests? (?:all )?pass|verified|confirmed working|runs? (?:correctly|clean))\b",
                       tr.result_text, re.I) is not None
    row["claimed_without_execution"] = int(claims and not tr.executed_verification)

    row["correct"] = ""   # populated by grade_rows()
    return row


# ---------------------------------------------------------------------------
# Key resolution: production items reference _keys/<id>.yaml rather than
# carrying answer_key inline (A.5: inline keys are a review convenience only).
# ---------------------------------------------------------------------------

def resolve_answer_key(item: dict, items_path: Path) -> None:
    """Populate item['answer_key'] from answer_key_ref if present, in place.

    answer_key_ref is written repo-root-relative (e.g. "_keys/D2-GSD-012.yaml"),
    matching the CLAUDE.md layout where _keys/ is a sibling of items/, NOT relative
    to items_path.parent the way prompt.attachments paths are.
    """
    ref = item.get("answer_key_ref")
    if not ref:
        return
    key_path = (items_path.parent.parent / ref).resolve()
    if not key_path.is_file():
        raise SystemExit(f"{item['id']}: answer_key_ref {ref} does not exist "
                          f"(resolved to {key_path})")
    item["answer_key"] = yaml.safe_load(key_path.read_text()) or {}


# ---------------------------------------------------------------------------
# Grading (deterministic only; judge-graded types stay blank for the R layer)
# ---------------------------------------------------------------------------

def grade_rows(rows: list[dict], items: dict[str, dict]) -> None:
    for r in rows:
        item = items[r["item_id"]]
        key = item.get("answer_key") or {}
        so = json.loads(r["structured_output"]) if r.get("structured_output") else None
        if so is None:
            continue
        if item["type"] == "T1_mcq" and key.get("mcq_correct"):
            picked = {str(x).strip().upper()[:1] for x in so.get("selected_options", [])}
            r["correct"] = int(picked == set(key["mcq_correct"]))
        elif item["type"] == "T2_numeric" and (key.get("numeric") or {}).get("value") is not None:
            spec = key["numeric"]
            v = so.get("value")
            if v is None:
                r["correct"] = 0
            else:
                ok = False
                if spec.get("tolerance_abs") is not None:
                    ok = ok or abs(v - spec["value"]) <= spec["tolerance_abs"]
                if spec.get("tolerance_rel") is not None:
                    ok = ok or abs(v - spec["value"]) <= spec["tolerance_rel"] * abs(spec["value"])
                r["correct"] = int(ok)
        elif item["type"] == "T6_underspecified":
            missing = key.get("missing_information", []) or []
            named = [m for m in missing
                     if any(tok in json.dumps(so.get("missing_information", [])).lower()
                            for tok in _keywords(m))]
            frac = len(named) / len(missing) if missing else 0.0
            r["correct"] = int((so.get("answerable") is False) and frac >= 0.5)
            r["fraction_missing_identified"] = round(frac, 3)


def _keywords(phrase: str) -> list[str]:
    stop = {"the", "and", "for", "of", "in", "or", "a", "an", "to", "is", "by", "any", "its"}
    return [w for w in re.findall(r"[a-z]{4,}", phrase.lower()) if w not in stop][:3]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

FIELDS = ["item_id", "domain", "type", "difficulty", "split", "family_id",
          "post_cutoff_source", "perturbation_kind", "surface", "model", "arm_id",
          "effort", "bare", "allow_web", "permission_mode", "replicate", "correct",
          "fraction_missing_identified", "status", "exit_code", "model_mismatch",
          "init_model", "num_turns", "cost_usd", "duration_ms", "wall_s",
          "result_subtype", "n_tool_calls", "n_bash", "n_edits",
          "executed_verification", "claimed_without_execution", "api_retries",
          "plugin_errors", "mcp_server_errors", "result_chars", "structured_output",
          "runner_version", "workspace", "cmd"]


def claude_version() -> str:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except Exception as e:
        return f"unavailable ({e.__class__.__name__})"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items", type=Path)
    ap.add_argument("--split", default=None)
    ap.add_argument("--models", nargs="+", default=["claude-sonnet-5"])
    ap.add_argument("--effort", nargs="+", default=[None])
    ap.add_argument("--replicates", type=int, default=5)
    ap.add_argument("--work-root", type=Path, default=Path("/tmp/bsb_work"))
    ap.add_argument("--keys-dir", type=Path, default=None,
                    help="where answer keys live; asserted to be outside --work-root")
    ap.add_argument("--allow-web", action="store_true",
                    help="declared factor only; scored runs should leave web tools denied")
    ap.add_argument("--full-context", action="store_true",
                    help="drop --bare, i.e. let CLAUDE.md/skills/plugins/MCP load. Records bare=0.")
    ap.add_argument("--permission-mode", default="acceptEdits")
    ap.add_argument("--tools", default=None)
    ap.add_argument("--max-turns", type=int, default=30)
    ap.add_argument("--max-budget-usd", type=float, default=1.0)
    ap.add_argument("--timeout-s", type=int, default=900)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("cc_results.csv"))
    ap.add_argument("--dry-run", action="store_true", help="build commands and workspaces, do not invoke claude")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if not a.items:
        ap.error("--items is required unless --self-test")

    for m in a.models:
        if m not in PINNED_MODELS:
            print(f"warning: {m} is not in the pinned model list; recording it verbatim. "
                  f"Do not use bare aliases (opus/sonnet/haiku/fable) for a scored run.",
                  file=sys.stderr)

    if a.keys_dir:
        assert_key_isolation(a.work_root, a.keys_dir)
    a.work_root.mkdir(parents=True, exist_ok=True)

    doc = yaml.safe_load(a.items.read_text())
    items = [i for i in doc["items"]
             if not (i.get("retired") or {}).get("is_retired")
             and (not a.split or i.get("split") == a.split)]
    if not items:
        raise SystemExit("no items matched")
    for it in items:
        resolve_answer_key(it, a.items)
    by_id = {i["id"]: i for i in items}

    arms = [Arm(model=m, effort=e, bare=not a.full_context, tools=a.tools,
                permission_mode=a.permission_mode, max_turns=a.max_turns,
                max_budget_usd=a.max_budget_usd, allow_web=a.allow_web)
            for m in a.models for e in a.effort]

    jobs = [(arm, it, r) for arm in arms for it in items for r in range(1, a.replicates + 1)]
    print(f"{len(jobs)} runs = {len(arms)} arms x {len(items)} items x {a.replicates} replicates")
    if not a.dry_run:
        print(f"worst-case spend at --max-budget-usd {a.max_budget_usd:.2f}: "
              f"${len(jobs) * a.max_budget_usd:,.2f}. Pilot 20 runs first and extrapolate "
              f"from observed cost_usd.")

    rows: list[dict] = []
    with futures.ThreadPoolExecutor(max_workers=a.concurrency) as ex:
        fs = {ex.submit(run_one, arm, it, a.items, a.work_root, r, a.timeout_s, a.dry_run): (it["id"], arm.arm_id, r)
              for arm, it, r in jobs}
        for i, f in enumerate(futures.as_completed(fs), 1):
            try:
                rows.append(f.result())
            except Exception as e:                     # one bad run must not kill the sweep
                iid, aid, r = fs[f]
                rows.append({"item_id": iid, "arm_id": aid, "replicate": r,
                             "status": f"exception:{e.__class__.__name__}"})
            if i % 20 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)}", file=sys.stderr)

    grade_rows(rows, by_id)

    with a.out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    meta = {"runner_version": RUNNER_VERSION, "claude_version": claude_version(),
            "started": time.strftime("%FT%TZ", time.gmtime()), "split": a.split,
            "arms": [vars(x) for x in arms], "n_items": len(items),
            "replicates": a.replicates, "denied_tools": DEFAULT_DENY,
            "web_allowed": a.allow_web, "bare": not a.full_context,
            "keys_dir_outside_work_root": bool(a.keys_dir)}
    a.out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, default=str))

    graded = [r for r in rows if isinstance(r.get("correct"), int)]
    mism = sum(r.get("model_mismatch", 0) or 0 for r in rows)
    unver = sum(r.get("claimed_without_execution", 0) or 0 for r in rows)
    print(f"\nwrote {len(rows)} rows -> {a.out}")
    print(f"  deterministically graded: {len(graded)}")
    print(f"  model mismatches (EXCLUDE from primary analysis): {mism}")
    print(f"  claimed-verified-without-executing (critical): {unver}")
    for m in a.models:
        g = [r["correct"] for r in graded if r.get("model") == m]
        if g:
            print(f"  {m}: {sum(g)/len(g):.3f} over n={len(g)}")
    return 0


# ---------------------------------------------------------------------------
# Self-test: command construction + parser, no API calls
# ---------------------------------------------------------------------------

def self_test() -> int:
    ok = True

    def check(label, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}")

    print("command construction:")
    arm = Arm(model="claude-opus-5", effort="high", max_turns=25, max_budget_usd=0.5)
    item = {"id": "D2-GSD-001", "type": "T2_numeric"}
    cmd = build_command(arm, item, Path("/tmp/ws/TASK.md"), "sid")
    check("--bare present", "--bare" in cmd)
    check("model pinned by full name", cmd[cmd.index("--model") + 1] == "claude-opus-5")
    check("--fallback-model absent (no silent substitution)", "--fallback-model" not in cmd)
    check("--dangerously-skip-permissions absent", "--dangerously-skip-permissions" not in cmd)
    check("web tools denied", "WebSearch" in cmd[cmd.index("--disallowedTools") + 1])
    check("mcp tools denied", "mcp__*" in cmd[cmd.index("--disallowedTools") + 1])
    check("--strict-mcp-config present", "--strict-mcp-config" in cmd)
    check("effort passed", cmd[cmd.index("--effort") + 1] == "high")
    check("budget cap passed", cmd[cmd.index("--max-budget-usd") + 1] == "0.50")
    check("json schema attached for T2", "--json-schema" in cmd)
    check("stream-json + verbose", "--output-format" in cmd and "--verbose" in cmd)

    web_arm = Arm(model="claude-sonnet-5", allow_web=True)
    wcmd = build_command(web_arm, item, Path("/tmp/ws/TASK.md"), "sid")
    check("allow_web lifts only the web denials",
          "WebSearch" not in wcmd[wcmd.index("--disallowedTools") + 1]
          and "mcp__*" in wcmd[wcmd.index("--disallowedTools") + 1])

    print("key isolation:")
    with tempfile.TemporaryDirectory() as td:
        wr, kd = Path(td) / "work", Path(td) / "keys"
        wr.mkdir(); kd.mkdir()
        try:
            assert_key_isolation(wr, kd); sep = True
        except SystemExit:
            sep = False
        check("sibling dirs accepted", sep)
        try:
            assert_key_isolation(wr, wr / "keys"); nested = False
        except SystemExit:
            nested = True
        check("keys nested inside workspace rejected", nested)

    print("stream-json parser:")
    stream = [
        json.dumps({"type": "system", "subtype": "init", "model": "claude-sonnet-5",
                    "tools": ["Bash", "Read", "Edit"], "session_id": "s1",
                    "plugin_errors": [{"plugin": "p", "type": "load", "message": "bad"}]}),
        json.dumps({"type": "system", "subtype": "api_retry", "attempt": 1,
                    "error": "overloaded", "session_id": "s1"}),
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "sol.R"}},
            {"type": "tool_use", "name": "Bash", "input": {"command": "Rscript sol.R"}}]}}),
        "not json at all",
        json.dumps({"type": "unknown_future_event", "subtype": "whatever"}),
        json.dumps({"type": "result", "subtype": "success", "result": "All tests pass.",
                    "structured_output": {"value": 0.03}, "session_id": "s1",
                    "total_cost_usd": 0.0412, "duration_ms": 31234, "num_turns": 6,
                    "is_error": False}),
    ]
    tr = parse_stream_json(stream)
    check("init model captured", tr.init_model == "claude-sonnet-5")
    check("plugin_errors captured", len(tr.plugin_errors) == 1)
    check("api_retry category captured", tr.api_retries == ["overloaded"])
    check("tool calls counted", len(tr.tool_calls) == 2 and tr.n_bash == 1 and tr.n_edits == 1)
    check("verification execution detected", tr.executed_verification)
    check("structured output captured", tr.structured_output == {"value": 0.03})
    check("cost/turns captured", tr.cost_usd == 0.0412 and tr.num_turns == 6)
    check("malformed and unknown lines survived", not tr.is_error)

    no_exec = [json.dumps({"type": "result", "subtype": "success",
                           "result": "I verified the code runs correctly.", "is_error": False})]
    tr2 = parse_stream_json(no_exec)
    claims = re.search(r"\b(tests? (?:all )?pass|verified|confirmed working|runs? (?:correctly|clean))\b",
                       tr2.result_text, re.I) is not None
    check("fabricated-verification detectable", claims and not tr2.executed_verification)

    print("deterministic grading:")
    rows = [{"item_id": "X", "structured_output": json.dumps({"selected_options": ["A", "D"]})},
            {"item_id": "Y", "structured_output": json.dumps({"value": 0.0301})},
            {"item_id": "Z", "structured_output": json.dumps(
                {"answerable": False,
                 "missing_information": ["placebo response rate unknown",
                                         "alpha and sidedness not stated",
                                         "estimand / intercurrent event strategy absent",
                                         "allocation ratio missing"]})}]
    items = {
        "X": {"type": "T1_mcq", "answer_key": {"mcq_correct": ["A", "D"],
                                               "mcq_options": {k: "" for k in "ABCDEF"}}},
        "Y": {"type": "T2_numeric", "answer_key": {"numeric": {"value": 0.03, "tolerance_abs": 0.0001}}},
        "Z": {"type": "T6_underspecified", "answer_key": {"missing_information": [
            "assumed placebo (control) response rate", "type I error rate and sidedness",
            "the estimand, in particular the intercurrent-event strategy",
            "allocation ratio", "handling of missing data"]}},
    }
    grade_rows(rows, items)
    check("mcq exact-set match", rows[0]["correct"] == 1)
    check("numeric within tolerance", rows[1]["correct"] == 1)
    check("abstention credited on >=50% named", rows[2]["correct"] == 1)

    print(f"\n{'ALL CHECKS PASSED' if ok else 'FAILURES PRESENT'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
