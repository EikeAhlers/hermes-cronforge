"""Tool handlers.  Each returns a compact JSON string for the model."""

from __future__ import annotations

import json

from . import analyze, history, ops


def _dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=1)


def scan(args: dict | None = None, **kwargs) -> str:
    args = args or {}
    target = (args.get("job_id") or "").strip()
    if target:
        job = history.get_job(target)
        if not job:
            return _dump({"ok": False, "error": "unknown job: %s" % target})
        report = analyze.public(analyze.analyze(job))
        report["activated"] = bool(ops.receipts().get(job.get("id") or "", {}).get("script"))
        return _dump({"ok": True, "jobs": [report]})
    return _dump({"ok": True, **ops.scan()})


def compile_job(args: dict, **kwargs) -> str:
    job_id = (args or {}).get("job_id") or ""
    return _dump(ops.compile_job(job_id))


def verify(args: dict, **kwargs) -> str:
    job_id = (args or {}).get("job_id") or ""
    timeout = int((args or {}).get("timeout") or 1800)
    return _dump(ops.verify(job_id, timeout=timeout))


def activate(args: dict, **kwargs) -> str:
    job_id = (args or {}).get("job_id") or ""
    return _dump(ops.activate(job_id))


def rollback(args: dict, **kwargs) -> str:
    job_id = (args or {}).get("job_id") or ""
    return _dump(ops.rollback(job_id, reason="manual"))
