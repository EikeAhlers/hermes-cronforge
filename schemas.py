"""JSON schemas for cronforge's five tools."""

from __future__ import annotations

JOB_ID = {
    "type": "string",
    "description": "Cron job id or exact job name (see `hermes cron list`).",
}

SCAN = {
    "name": "cronforge_scan",
    "description": (
        "Inspect every cron job of this profile and report which ones are proven deterministic "
        "enough to run as a zero-token script, with the evidence (matching runs, volatile slots) "
        "and the tokens currently burned per month. Read-only."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "Optional: restrict the report to one job (id or exact name).",
            }
        },
        "additionalProperties": False,
    },
}

COMPILE = {
    "name": "cronforge_compile",
    "description": (
        "Generate a replay script for one job from its proven run history. Writes the script under "
        "the Hermes scripts directory and byte-compiles it. Does NOT change the job — activation is a "
        "separate tool so the user sees the code first."
    ),
    "parameters": {
        "type": "object",
        "properties": {"job_id": JOB_ID},
        "required": ["job_id"],
        "additionalProperties": False,
    },
}

VERIFY = {
    "name": "cronforge_verify",
    "description": (
        "Execute the compiled script once, right now, and report its exit code and JSON summary. "
        "This runs the job's commands for real — use it as the pre-flight check before activation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "job_id": JOB_ID,
            "timeout": {"type": "integer", "minimum": 30, "maximum": 7200, "description": "Seconds before the run is killed (default 1800)."},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
}

ACTIVATE = {
    "name": "cronforge_activate",
    "description": (
        "Switch a compiled job to no_agent mode so the scheduler runs the script instead of the model "
        "(0 tokens per run). The original prompt and delivery settings are stored in a rollback receipt; "
        "two failing runs hand the job back to the agent automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {"job_id": JOB_ID},
        "required": ["job_id"],
        "additionalProperties": False,
    },
}

ROLLBACK = {
    "name": "cronforge_rollback",
    "description": (
        "Restore a job that cronforge activated: agent mode comes back with the original prompt, "
        "delivery and model. Safe to call at any time."
    ),
    "parameters": {
        "type": "object",
        "properties": {"job_id": JOB_ID},
        "required": ["job_id"],
        "additionalProperties": False,
    },
}
