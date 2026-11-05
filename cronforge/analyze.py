"""Decide whether a cron job is deterministic enough to become a script.

The verdict is deliberately strict: cronforge only says "compilable" when at
least two independent runs produced the *same* ordered tool sequence and every
argument matched apart from recognised volatile spans (dates, timestamps, pids).
Anything the model had to judge blocks compilation — a script cannot judge.
"""

from __future__ import annotations

import json
from typing import Any

from . import history, ledger
from .policy import VOLATILE_SPANS, split_commands, tool_class

MIN_RUNS = 2
MAX_RUNS = 8


def _args(step: dict) -> dict:
    args = step.get("args")
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            loaded = json.loads(args)
            return loaded if isinstance(loaded, dict) else {"_raw": args}
        except Exception:
            return {"_raw": args}
    return {}


def _flatten(steps: list[dict]) -> list[dict]:
    """One entry per atomic command, so grouping quirks don't look like chaos."""
    out: list[dict] = []
    for step in steps:
        tool = (step.get("tool") or "").split(":", 1)[-1]
        args = _args(step)
        if tool == "terminal":
            for command in split_commands(args.get("command") or args.get("cmd") or ""):
                out.append({"tool": "terminal", "args": {"command": command}})
        else:
            out.append({"tool": tool, "args": args})
    return out


def _canon(value: Any) -> tuple[str, list[str]]:
    """Stable text for an argument value, plus the placeholders it contains."""
    if not isinstance(value, str):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        text = value
    used: list[str] = []
    for name, pattern in VOLATILE_SPANS:
        if pattern.search(text):
            used.append(name)
            text = pattern.sub("<%s>" % name, text)
    return text, sorted(set(used))


def _step_key(step: dict) -> str:
    tool = (step.get("tool") or "").split(":", 1)[-1]
    canon, _ = _canon(_args(step))
    return tool + "\x00" + canon


def _signature(steps: list[dict]) -> str:
    return "|".join((s.get("tool") or "").split(":", 1)[-1] for s in steps)


def _from_ledger(job_id: str, max_runs: int) -> list[list[dict]]:
    grouped = ledger.executions(job_id)
    ordered = sorted(grouped.items(), key=lambda kv: min((r.get("ts") or 0) for r in kv[1]))
    runs = []
    for _exec_id, steps in ordered[-max_runs:]:
        runs.append(steps)
    return runs


def _from_history(job: dict, max_runs: int) -> list[list[dict]]:
    runs = []
    for sess in history.runs_for_job(job, limit=max_runs):
        steps = history.session_steps(sess["id"])
        if steps:
            runs.append(steps)
    return runs


def collect_runs(job: dict, max_runs: int = MAX_RUNS) -> tuple[list[list[dict]], str]:
    job_id = job.get("id") or ""
    runs = _from_ledger(job_id, max_runs)
    if len(runs) >= MIN_RUNS:
        return runs, "ledger"
    hist = _from_history(job, max_runs)
    if len(hist) > len(runs):
        return hist, "history"
    return runs, "ledger" if runs else "none"


def analyze(job: dict, force: bool = False) -> dict:
    import json as _json

    runs, source = collect_runs(job)
    report: dict[str, Any] = {
        "job_id": job.get("id"),
        "job_name": job.get("name"),
        "schedule": job.get("schedule"),
        "mode": "script" if job.get("no_agent") else "agent",
        "evidence_source": source,
        "runs_found": len(runs),
        "compilable": False,
        "reasons": [],
        "blocking_tools": [],
        "volatile_slots": [],
        "steps": 0,
        "tokens_per_run": None,
        "projected_monthly_runs": round(history.schedule_runs_per_month(job), 1),
        "projected_monthly_tokens_saved": None,
    }

    if job.get("no_agent") and not force:
        report["reasons"].append("already runs as a script (no_agent) — nothing to compile")
        return report

    hist_runs = history.runs_for_job(job, limit=MAX_RUNS)
    avg_tokens = history.tokens_per_run(hist_runs)
    report["tokens_per_run"] = round(avg_tokens) if avg_tokens else None
    if avg_tokens:
        report["projected_monthly_tokens_saved"] = int(avg_tokens * report["projected_monthly_runs"])

    if not runs:
        report["reasons"].append("no run history yet — cronforge compiles from observed runs")
        return report

    runs = [_flatten(r) for r in runs]
    runs = [r for r in runs if r]

    signatures: dict[str, list[list[dict]]] = {}
    for steps in runs:
        signatures.setdefault(_signature(steps), []).append(steps)
    if not signatures:
        report["reasons"].append("no replayable shell steps found in the recorded runs")
        return report
    modal_sig, modal_runs = max(signatures.items(), key=lambda kv: len(kv[1]))
    report["runs_used"] = len(modal_runs)
    if len(signatures) > 1:
        report["reasons"].append(
            "tool sequence differs between runs (%d distinct sequences) — not deterministic"
            % len(signatures)
        )
    if len(modal_runs) < MIN_RUNS:
        report["reasons"].append("need at least %d matching runs, found %d" % (MIN_RUNS, len(modal_runs)))
        return report

    reference = modal_runs[-1]
    report["steps"] = len(reference)

    blocking = sorted({(s.get("tool") or "").split(":", 1)[-1] for s in reference if tool_class(s.get("tool") or "") == "blocking"})
    report["blocking_tools"] = blocking
    if blocking:
        report["reasons"].append("contains model-only tools (judgement work): " + ", ".join(blocking))

    volatile: set[str] = set()
    unstable = []
    for idx, step in enumerate(reference):
        keys = []
        for other in modal_runs:
            if idx >= len(other):
                unstable.append(idx)
                continue
            keys.append(_step_key(other[idx]))
        if len(set(keys)) > 1:
            unstable.append(idx)
        _, used = _canon(_args(step))
        volatile.update(used)

    if unstable:
        report["reasons"].append(
            "arguments change in non-volatile ways at step(s) %s — a literal replay would be wrong"
            % ", ".join(str(i + 1) for i in unstable[:6])
        )

    report["volatile_slots"] = sorted(volatile)

    if not report["reasons"]:
        report["compilable"] = True
    report["_reference"] = reference
    return report


def public(report: dict) -> dict:
    """Report without the internal reference run."""
    return _strip(report)


def _strip(report: dict) -> dict:
    return {k: v for k, v in report.items() if not k.startswith("_")}
