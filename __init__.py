"""cronforge plugin entry point.

Registers five tools plus the two hooks that make the whole thing honest:
``post_tool_call`` records what a scheduled run actually executed (tagged with
the real cron execution id core hands out), and ``on_session_end`` runs the
rollback guard after a cron run finishes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import schemas  # noqa: E402
from cronforge import ledger, ops, tools  # noqa: E402

TOOLSET = "cronforge"


def register(ctx):
    for name, schema, handler in (
        ("cronforge_scan", schemas.SCAN, tools.scan),
        ("cronforge_compile", schemas.COMPILE, tools.compile_job),
        ("cronforge_verify", schemas.VERIFY, tools.verify),
        ("cronforge_activate", schemas.ACTIVATE, tools.activate),
        ("cronforge_rollback", schemas.ROLLBACK, tools.rollback),
    ):
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler)

    def on_post_tool_call(tool_name, args, result, task_id=None, duration_ms=None, **kwargs):
        try:
            run = ctx.current_cron_execution()
        except Exception:
            run = None
        if run is None:
            return None
        try:
            ledger.record_tool(
                job_id=getattr(run, "job_id", "") or "",
                job_name=getattr(run, "job_name", "") or "",
                execution_id=getattr(run, "execution_id", "") or "",
                session_id=str(task_id or ""),
                tool=tool_name,
                args=args,
                result=result,
                duration_ms=duration_ms if isinstance(duration_ms, int) else None,
            )
        except Exception:
            return None
        return None

    def on_session_end(**_kwargs):
        try:
            run = ctx.current_cron_execution()
        except Exception:
            run = None
        if run is None:
            return None
        try:
            ops.guard(
                getattr(run, "job_id", "") or "",
                getattr(run, "job_name", "") or "",
            )
        except Exception:
            return None
        return None

    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("on_session_end", on_session_end)
