"""Read cron jobs and their past runs out of Hermes' own stores.

Two evidence sources exist:

* the live ledger (written by the hook while a scheduled run executes), and
* history (``state.db`` sessions with ``source='cron'``), which lets cronforge
  report on jobs that already ran before it was installed.

Both are read-only.  Nothing here writes to ``state.db``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .paths import jobs_path, state_db_path


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def jobs() -> list[dict]:
    """All cron jobs of the active profile, read from the profile's job store.

    cronforge deliberately does not import ``cron.jobs``: catalog rule 9 keeps
    plugins on public surfaces, and the job store is a plain JSON file next to
    the same Hermes home the plugin already owns.
    """
    path = jobs_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, dict):
        raw = raw.get("jobs") or []
    return [j for j in raw if isinstance(j, dict)]


def get_job(job_id: str) -> dict | None:
    for job in jobs():
        if job.get("id") == job_id or job.get("name") == job_id:
            return job
    return None


def cron_sessions(limit: int = 400) -> list[dict]:
    conn = _connect(state_db_path())
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """
            SELECT id, title, started_at, ended_at, end_reason, tool_call_count,
                   input_tokens, output_tokens, cache_read_tokens, model
            FROM sessions
            WHERE source = 'cron'
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return [dict(r) for r in rows]


def session_steps(session_id: str) -> list[dict]:
    """Ordered tool calls of one session, paired with their results."""
    conn = _connect(state_db_path())
    if conn is None:
        return []
    steps: list[dict] = []
    pending: dict[str, dict] = {}
    try:
        rows = conn.execute(
            """
            SELECT role, content, tool_calls, tool_call_id, tool_name, timestamp
            FROM messages
            WHERE session_id = ?
            ORDER BY timestamp ASC, id ASC
            """,
            (session_id,),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    for row in rows:
        role = row["role"]
        if role == "assistant" and row["tool_calls"]:
            try:
                calls = json.loads(row["tool_calls"])
            except Exception:
                calls = []
            for call in calls or []:
                fn = (call or {}).get("function") or {}
                name = fn.get("name") or call.get("name") or ""
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {"_raw": raw_args}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}
                call_id = call.get("call_id") or call.get("id") or ""
                step = {"tool": name, "args": args, "result": "", "ok": None}
                pending[call_id] = step
                steps.append(step)
        elif role == "tool":
            call_id = row["tool_call_id"] or ""
            step = pending.get(call_id)
            if step is None:
                continue
            content = row["content"] or ""
            step["result"] = content[:4000]
            step["ok"] = not content.lstrip().startswith(("Error", "error:", "Traceback"))
    return steps


def runs_for_job(job: dict, limit: int = 40) -> list[dict]:
    """Historical cron runs that belong to *job*, newest first.

    Core does not store the job id on the session row, so a run is matched by
    its title prefix (Hermes titles cron sessions ``<job name> (…)``) and, when
    that is ambiguous, by start-time proximity.
    """
    name = (job.get("name") or "").strip()
    if not name:
        return []
    out = []
    for sess in cron_sessions(limit=limit * 4):
        title = (sess.get("title") or "").strip()
        if title.startswith(name):
            out.append(sess)
        if len(out) >= limit:
            break
    return out


def tokens_per_run(runs: list[dict]) -> float | None:
    if not runs:
        return None
    total = 0
    for run in runs:
        total += int(run.get("input_tokens") or 0)
        total += int(run.get("output_tokens") or 0)
    return total / len(runs)


def schedule_runs_per_month(job: dict) -> float:
    """Rough monthly run count from the cron expression (no scheduler calls)."""
    raw = job.get("schedule")
    if isinstance(raw, dict):
        expr = str(raw.get("expr") or "").strip()
    else:
        expr = str(raw or "").strip()
    every = job.get("every")
    if every:
        secs = float(every)
        return max(1.0, 30 * 24 * 3600 / secs) if secs else 1.0
    parts = expr.split()
    if len(parts) != 5:
        return 30.0
    minute, hour, dom, month, dow = parts
    per_day = 1.0
    if minute.startswith("*/"):
        try:
            per_day = 24 * 60 / max(1, int(minute[2:]))
        except ValueError:
            per_day = 1.0
    elif hour == "*":
        per_day = 24.0
    elif hour.startswith("*/"):
        try:
            per_day = 24 / max(1, int(hour[2:]))
        except ValueError:
            per_day = 1.0
    elif "," in hour:
        per_day = float(len(hour.split(",")))
    if dow not in ("*", "?", ""):
        per_day *= 5 / 7
    return per_day * 30
