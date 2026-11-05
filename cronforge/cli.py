"""The one place cronforge talks to Hermes core.

Mutations go through the documented CLI (``hermes cron edit``) instead of
importing ``cron.jobs``, so a core refactor cannot silently break the plugin and
catalog rule 9 stays satisfied.  The child process is spawned with every
approval-bypassing environment variable stripped: the edit must behave exactly
like a human typing the command.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Sequence

_STRIP_ENV = (
    "HERMES_YOLO",
    "HERMES_UNATTENDED",
    "HERMES_AUTO_APPROVE",
    "HERMES_APPROVALS_OFF",
    "HERMES_SKIP_APPROVALS",
)


def hermes_bin() -> str:
    for name in ("hermes", "hermes.exe", "hermes.cmd", "hermes.bat"):
        found = shutil.which(name)
        if found:
            return found
    return "hermes"


def _clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() not in _STRIP_ENV}
    env.pop("HERMES_SESSION", None)
    return env


def hermes(args: Sequence[str], timeout: int = 180) -> dict:
    try:
        proc = subprocess.run(
            [hermes_bin(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_clean_env(),
        )
    except FileNotFoundError:
        return {"ok": False, "error": "hermes CLI not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "hermes %s timed out" % " ".join(args[:2])}
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return {"ok": proc.returncode == 0, "exit": proc.returncode, "output": out[-2000:]}
