"""Filesystem locations cronforge uses (all inside the active Hermes home)."""

from __future__ import annotations

import os
from pathlib import Path


def hermes_home() -> Path:
    """The active profile's Hermes home, resolved the way core resolves it."""
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    try:
        from hermes_constants import get_hermes_home  # type: ignore

        return Path(get_hermes_home())
    except Exception:  # pragma: no cover - older/newer core layouts
        local = os.environ.get("LOCALAPPDATA")
        if local and (Path(local) / "hermes").exists():
            return Path(local) / "hermes"
        return Path.home() / ".hermes"


def state_dir() -> Path:
    """cronforge's own state: ledger, receipts, activation records."""
    path = hermes_home() / "cronforge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ledger_path() -> Path:
    return state_dir() / "ledger.jsonl"


def receipts_path() -> Path:
    return state_dir() / "receipts.json"


def events_path() -> Path:
    return state_dir() / "events.jsonl"


def scripts_dir() -> Path:
    path = hermes_home() / "scripts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_db_path() -> Path:
    return hermes_home() / "state.db"


def jobs_path() -> Path:
    return hermes_home() / "cron" / "jobs.json"
