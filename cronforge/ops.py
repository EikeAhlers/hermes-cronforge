"""Compile, activate, verify and roll back — the stateful half of cronforge."""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from typing import Any

from . import analyze, cli, codegen, history, ledger
from .paths import receipts_path, scripts_dir

FAILURES_BEFORE_ROLLBACK = 2


# --- receipts --------------------------------------------------------------


def receipts() -> dict:
    path = receipts_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_receipts(data: dict) -> None:
    path = receipts_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --- read-only -------------------------------------------------------------


def scan() -> dict:
    reports = []
    for job in history.jobs():
        try:
            report = analyze.public(analyze.analyze(job))
        except Exception as exc:  # never let one odd job break the scan
            report = {
                "job_id": job.get("id"),
                "job_name": job.get("name"),
                "compilable": False,
                "reasons": ["analysis error: %s" % exc],
            }
        report["activated"] = bool(receipts().get(job.get("id") or "", {}).get("script"))
        reports.append(report)
    reports.sort(
        key=lambda r: (
            0 if r.get("compilable") else 1,
            -(r.get("projected_monthly_tokens_saved") or 0),
        )
    )
    return {
        "jobs": reports,
        "compilable": [r for r in reports if r.get("compilable")],
        "monthly_tokens_at_stake": sum(r.get("projected_monthly_tokens_saved") or 0 for r in reports if r.get("compilable")),
    }


def script_path(job_id: str):
    return scripts_dir() / ("cronforge_%s.py" % job_id)


# --- write -----------------------------------------------------------------


def compile_job(job_id: str) -> dict:
    job = history.get_job(job_id)
    if not job:
        return {"ok": False, "error": "unknown job: %s" % job_id}
    report = analyze.analyze(job, force=True)
    if not report.get("compilable"):
        return {"ok": False, "error": "not compilable", "report": analyze.public(report)}

    created = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    source = codegen.generate(report, job, created)
    path = script_path(job.get("id"))
    path.write_text(source, encoding="utf-8", newline="\n")

    py = sys.executable or "python3"
    check = subprocess.run([py, "-m", "py_compile", str(path)], capture_output=True, text=True)
    if check.returncode != 0:
        return {
            "ok": False,
            "error": "generated script does not compile",
            "detail": (check.stderr or "")[:1500],
        }

    ledger.record_event("compiled", job_id=job.get("id"), job_name=job.get("name"), script=str(path))
    return {
        "ok": True,
        "script": str(path),
        "script_name": path.name,
        "lines": source.count("\n") + 1,
        "estimate": codegen.estimate(report),
        "report": analyze.public(report),
    }


def activate(job_id: str) -> dict:
    job = history.get_job(job_id)
    if not job:
        return {"ok": False, "error": "unknown job: %s" % job_id}
    path = script_path(job.get("id"))
    if not path.exists():
        return {"ok": False, "error": "compile the job first (no script on disk)"}
    if job.get("no_agent"):
        return {"ok": False, "error": "job already runs without an agent"}

    data = receipts()
    entry = data.setdefault(job.get("id"), {})
    entry.setdefault(
        "original",
        {
            "prompt": job.get("prompt"),
            "payload": job.get("payload"),
            "no_agent": bool(job.get("no_agent")),
            "script": job.get("script"),
            "delivery": job.get("delivery"),
            "model": job.get("model"),
        },
    )
    entry.update(
        {
            "job_name": job.get("name"),
            "script": path.name,
            "activated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "failures": 0,
        }
    )
    _save_receipts(data)

    result = cli.hermes(["cron", "edit", job.get("id"), "--no-agent", "--script", path.name])
    if not result.get("ok"):
        return {"ok": False, "error": "hermes cron edit failed", "detail": result.get("output", "")[:800]}

    fresh = history.get_job(job.get("id")) or {}
    ledger.record_event("activated", job_id=job.get("id"), job_name=job.get("name"), script=path.name)
    return {
        "ok": bool(fresh.get("no_agent")),
        "job_id": job.get("id"),
        "script": path.name,
        "mode": "script" if fresh.get("no_agent") else "agent",
        "rollback_after_failures": FAILURES_BEFORE_ROLLBACK,
    }


def verify(job_id: str, timeout: int = 1800) -> dict:
    """Run the compiled script once, for real, and report what happened."""
    job = history.get_job(job_id)
    if not job:
        return {"ok": False, "error": "unknown job: %s" % job_id}
    path = script_path(job.get("id"))
    if not path.exists():
        return {"ok": False, "error": "no compiled script yet"}
    py = sys.executable or "python3"
    proc = subprocess.run([py, str(path)], capture_output=True, text=True, timeout=timeout)
    payload: Any
    try:
        payload = json.loads((proc.stdout or "").strip().splitlines()[-1])
    except Exception:
        payload = {"raw_stdout": (proc.stdout or "")[:2000]}
    return {
        "ok": proc.returncode == 0,
        "exit": proc.returncode,
        "stderr": (proc.stderr or "")[:800],
        "result": payload,
    }


def rollback(job_id: str, reason: str = "manual") -> dict:
    job = history.get_job(job_id)
    if not job:
        return {"ok": False, "error": "unknown job: %s" % job_id}
    data = receipts()
    entry = data.get(job.get("id"))
    if not entry:
        return {"ok": False, "error": "job was never activated by cronforge"}
    original = entry.get("original") or {}

    args = ["cron", "edit", job.get("id"), "--agent"]
    if original.get("prompt"):
        args += ["--prompt", str(original["prompt"])]
    if original.get("script"):
        args += ["--script", str(original["script"])]
    result = cli.hermes(args)
    if not result.get("ok"):
        return {"ok": False, "error": "hermes cron edit failed", "detail": result.get("output", "")[:800]}

    entry["rolled_back_at"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    entry["rollback_reason"] = reason
    entry["failures"] = 0
    _save_receipts(data)
    ledger.record_event("rolled_back", job_id=job.get("id"), job_name=job.get("name"), reason=reason)
    fresh = history.get_job(job.get("id")) or {}
    return {
        "ok": not bool(fresh.get("no_agent")),
        "job_id": job.get("id"),
        "reason": reason,
        "mode": "script" if fresh.get("no_agent") else "agent",
    }


# --- called from the cron hook --------------------------------------------


def guard(job_id: str, job_name: str | None = None, end_reason: str | None = None) -> dict | None:
    """Count a failing script run and hand the job back after repeated failures."""
    data = receipts()
    entry = data.get(job_id)
    if not entry or not entry.get("script") or entry.get("rolled_back_at"):
        return None

    failures = [e for e in ledger.events() if e.get("kind") == "script_failure" and e.get("job_id") == job_id]
    entry["failures"] = len(failures)
    _save_receipts(data)
    if len(failures) < FAILURES_BEFORE_ROLLBACK:
        return None
    return rollback(job_id, reason="script failed %d times" % len(failures))
