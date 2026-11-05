"""Emit a standalone replay script from a proven run.

The generated file is deliberately boring: plain Python, no third-party
imports, every command visible, every exit code asserted.  It prints one JSON
line so the cron delivery stays readable.
"""

from __future__ import annotations

import json
import pprint
from typing import Any

from .policy import VOLATILE_SPANS


def _templatize(command: str, slots: set[str]) -> str:
    """Replace only the volatile spans that really varied between runs."""
    out = command
    for name, pattern in VOLATILE_SPANS:
        if name not in slots:
            continue
        out = pattern.sub("{{%s}}" % name, out)
    return out


def build_steps(report: dict, slots: set[str]) -> list[dict]:
    steps = []
    for index, step in enumerate(report["_reference"], start=1):
        args = step.get("args") or {}
        command = args.get("command") or args.get("cmd") or ""
        if not command:
            raise ValueError("step %d has no shell command" % index)
        if "\x00" in command:
            raise ValueError("step %d contains a NUL byte" % index)
        steps.append(
            {
                "n": index,
                "command": _templatize(command, slots),
                "workdir": args.get("workdir") or args.get("cwd") or None,
                "timeout": int(args.get("timeout") or 1800),
            }
        )
    return steps


_TEMPLATE = '''#!/usr/bin/env python3
"""cronforge replay script - generated, do not edit by hand.

job:      __JOB_NAME__ (__JOB_ID__)
schedule: __SCHEDULE__
evidence: __RUNS__ matching runs, source=__SOURCE__
steps:    __STEP_COUNT__
created:  __CREATED__

Every command below was executed by the agent in those runs and is replayed
verbatim, apart from volatile slots (dates, timestamps, pids) which are
recomputed at run time.  Exit codes are asserted; any deviation makes the job
fail loudly, and cronforge hands the job back to the agent.
"""

import datetime
import json
import os
import re
import subprocess
import sys
import time
import uuid

JOB_ID = __JOB_ID_LITERAL__
JOB_NAME = __JOB_NAME_LITERAL__
STATE_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cronforge"))

STEPS = __STEPS_LITERAL__
ORIGINAL_PROMPT = __ORIGINAL_PROMPT__
ROLLBACK_AFTER_FAILURES = 2

SLOTS = {
    "today": lambda: datetime.date.today().isoformat(),
    "now_iso": lambda: datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    "clock": lambda: datetime.datetime.now().strftime("%H:%M:%S"),
    "epoch": lambda: str(int(time.time())),
    "uuid4": lambda: str(uuid.uuid4()),
    "pid": lambda: str(os.getpid()),
}


def render(command):
    def sub(match):
        fn = SLOTS.get(match.group(1))
        return fn() if fn else match.group(0)

    return re.sub(r"\\{\\{([a-z0-9_]+)\\}\\}", sub, command)


def record(kind, **fields):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        fields.update({"kind": kind, "ts": time.time(), "job_id": JOB_ID, "job_name": JOB_NAME})
        with open(os.path.join(STATE_DIR, "events.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fields, ensure_ascii=False) + "\\n")
    except Exception:
        pass


def shell_argv(command):
    """Commands are written for the agent's shell; use the same one here."""
    import shutil

    if os.name == "nt":
        for name in ("bash", "bash.exe"):
            binary = shutil.which(name)
            if binary:
                return [binary, "-lc", command], False
    return command, True


def self_heal():
    """Hand the job back to the agent after repeated failures.

    Counts *consecutive* failures: any successful run resets the counter, so a
    one-off glitch does not steal the job from the script.

    No watcher, no daemon: the failure count is read from cronforge's own event
    log and the job is restored through the public CLI, exactly like a human
    would do it.
    """
    failures = 0
    try:
        with open(os.path.join(STATE_DIR, "events.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("job_id") != JOB_ID:
                    continue
                if rec.get("kind") == "script_success":
                    failures = 0
                elif rec.get("kind") == "script_failure":
                    failures += 1
    except OSError:
        return
    if failures < ROLLBACK_AFTER_FAILURES:
        return
    import shutil

    binary = shutil.which("hermes") or shutil.which("hermes.cmd") or "hermes"
    env = {
        k: v
        for k, v in os.environ.items()
        if "YOLO" not in k.upper() and "UNATTENDED" not in k.upper()
    }
    args = [binary, "cron", "edit", JOB_ID, "--agent"]
    if ORIGINAL_PROMPT:
        args += ["--prompt", ORIGINAL_PROMPT]
    try:
        subprocess.run(args, env=env, capture_output=True, text=True, timeout=180)
        record("rolled_back", reason="%d failing runs" % failures)
    except Exception:
        pass


def main():
    started = time.time()
    results = []
    for step in STEPS:
        command = render(step["command"])
        cwd = step.get("workdir")
        if cwd and not os.path.isdir(cwd):
            cwd = None
        argv, shell = shell_argv(command)
        try:
            proc = subprocess.run(
                argv,
                shell=shell,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=step["timeout"],
            )
            code = proc.returncode
            tail = (proc.stdout or "").strip().splitlines()[-5:]
        except subprocess.TimeoutExpired:
            code = -1
            tail = ["timeout after %ss" % step["timeout"]]
        results.append({"step": step["n"], "exit": code, "tail": tail})
        if code != 0:
            summary = {
                "ok": False,
                "job": JOB_NAME,
                "failed_step": step["n"],
                "command": command[:300],
                "exit": code,
                "tail": tail,
                "duration_s": round(time.time() - started, 2),
            }
            record("script_failure", step=step["n"], exit=code, tail="\\n".join(tail)[:1500])
            self_heal()
            print(json.dumps(summary, ensure_ascii=False))
            return 1

    record("script_success", steps=len(STEPS), duration=round(time.time() - started, 2))
    print(
        json.dumps(
            {
                "ok": True,
                "job": JOB_NAME,
                "steps": len(STEPS),
                "duration_s": round(time.time() - started, 2),
                "results": results,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def generate(report: dict, job: dict, created: str) -> str:
    slots = set(report.get("volatile_slots") or [])
    steps = build_steps(report, slots)
    out = _TEMPLATE
    replacements = {
        "__JOB_NAME__": job.get("name") or "job",
        "__JOB_ID__": job.get("id") or "",
        "__SCHEDULE__": str(job.get("schedule") or ""),
        "__RUNS__": str(report.get("runs_used") or report.get("runs_found")),
        "__SOURCE__": str(report.get("evidence_source")),
        "__STEP_COUNT__": str(len(steps)),
        "__CREATED__": created,
        "__JOB_ID_LITERAL__": json.dumps(job.get("id") or ""),
        "__JOB_NAME_LITERAL__": json.dumps(job.get("name") or ""),
        "__ORIGINAL_PROMPT__": json.dumps(job.get("prompt") or ""),
        "__STEPS_LITERAL__": pprint.pformat(steps, indent=4, sort_dicts=False, width=100),
    }
    for token, value in replacements.items():
        out = out.replace(token, value)
    return out


def estimate(report: dict) -> dict[str, Any]:
    return {
        "steps": report.get("steps"),
        "volatile_slots": report.get("volatile_slots") or [],
        "tokens_per_run": report.get("tokens_per_run"),
        "projected_monthly_tokens_saved": report.get("projected_monthly_tokens_saved"),
    }
