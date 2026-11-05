"""Append-only observation ledger.

Every tool call that happens inside a scheduled cron run is written here by the
``post_tool_call`` hook, tagged with the real job/execution id that core hands
out via ``ctx.current_cron_execution()``.  The ledger is the primary evidence
cronforge compiles from: it is written by Hermes itself, never by the model.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Iterable

from .policy import redact
from .paths import events_path, ledger_path

_LOCK = threading.Lock()
_MAX_BLOB = 4000


def _truncate(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    return value[:_MAX_BLOB]


def _append_line(path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    with _LOCK:
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())


def record_tool(
    *,
    job_id: str,
    job_name: str,
    execution_id: str,
    session_id: str,
    tool: str,
    args: Any,
    result: Any,
    duration_ms: int | None = None,
    ok: bool | None = None,
) -> None:
    _append_line(
        ledger_path(),
        {
            "kind": "tool",
            "ts": time.time(),
            "job_id": job_id,
            "job_name": job_name,
            "execution_id": execution_id,
            "session_id": session_id,
            "tool": tool,
            "args": redact(_truncate(args)),
            "result": redact(_truncate(result)),
            "duration_ms": duration_ms,
            "ok": ok,
        },
    )


def record_event(kind: str, **fields: Any) -> None:
    payload = {"kind": kind, "ts": time.time()}
    payload.update({k: v for k, v in fields.items() if v is not None})
    _append_line(events_path(), payload)


def _iter(path) -> Iterable[dict]:
    if not path.exists():
        return []
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return []
    return out


def records(job_id: str | None = None) -> list[dict]:
    rows = [r for r in _iter(ledger_path()) if r.get("kind") == "tool"]
    if job_id:
        rows = [r for r in rows if r.get("job_id") == job_id]
    return rows


def events() -> list[dict]:
    return _iter(events_path())


def executions(job_id: str | None = None) -> dict[str, list[dict]]:
    """Group ledger tool calls into executions, ordered by observation time."""
    grouped: dict[str, list[dict]] = {}
    for row in records(job_id):
        exec_id = row.get("execution_id") or "unknown"
        grouped.setdefault(exec_id, []).append(row)
    for steps in grouped.values():
        steps.sort(key=lambda r: r.get("ts") or 0)
    return grouped
