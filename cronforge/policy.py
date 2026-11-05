"""What cronforge is allowed to treat as deterministic, and what it must hide."""

from __future__ import annotations

import re

# --- secrets ---------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{8,}\b"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b"),  # telegram-style bot tokens
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd)\b\s*[=:]\s*\S+"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    re.compile(r"\bya29\.[0-9A-Za-z._\-]{20,}\b"),
]


def redact(text: str) -> str:
    """Strip credential-shaped substrings before anything is persisted."""
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: (m.group(1) + " [REDACTED]") if m.groups() else "[REDACTED]", out)
    return out


def contains_secret(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _SECRET_PATTERNS)


# --- replay classes --------------------------------------------------------

# Tools a generated script can execute on its own, without a model in the loop.
# Only shell execution qualifies: a script can re-run a command, it cannot make
# the judgement that reading a file or searching a repo implies.
REPLAYABLE = {"terminal"}

# Tools whose output is expected to change between runs; they may still be
# replayed, but their arguments are never baked in as literals.
VOLATILE_TOOLS = {"web_extract", "web_search"}


def tool_class(name: str) -> str:
    """'replayable' | 'volatile' | 'blocking'."""
    base = (name or "").split(":", 1)[-1].strip()
    if base in REPLAYABLE:
        return "replayable"
    if base in VOLATILE_TOOLS:
        return "volatile"
    return "blocking"


# --- command canonicalisation ---------------------------------------------

# Connectors an agent uses to glue several unrelated calls into one shell line.
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\n)\s*")

# Glue commands: they shape output, not behaviour, so they never affect the
# verdict and never become steps of their own.
GLUE_PREFIXES = ("echo ", "printf ", "sleep ", "true", "cd ", "pwd", "set -e", ":", "#")


def split_commands(command: str) -> list[str]:
    """Flatten a compound shell line into its atomic commands."""
    parts = []
    for chunk in _SPLIT.split(command or ""):
        chunk = re.sub(r"\s+", " ", chunk).strip()
        if not chunk:
            continue
        low = chunk.lower()
        redirects = any(tok in chunk for tok in (">", "<", "|"))
        if not redirects and any(low == g.strip() or low.startswith(g) for g in GLUE_PREFIXES):
            continue
        parts.append(chunk)
    return parts


# --- volatile spans --------------------------------------------------------

# (placeholder, compiled regex).  A span is only replaced when the same step
# really did differ between recorded runs, so fixed literals stay literal.
VOLATILE_SPANS: list[tuple[str, re.Pattern]] = [
    ("now_iso", re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")),
    ("today", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("clock", re.compile(r"\b\d{2}:\d{2}:\d{2}\b")),
    ("uuid4", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")),
    ("epoch", re.compile(r"\b1[6-9]\d{8}\b")),
    ("pid", re.compile(r"\bpid[ =:](\d{2,7})\b")),
]

PLACEHOLDER_SOURCE = {
    "today": "datetime.date.today().isoformat()",
    "now_iso": "datetime.datetime.now().astimezone().isoformat(timespec='seconds')",
    "clock": "datetime.datetime.now().strftime('%H:%M:%S')",
    "epoch": "int(time.time())",
    "uuid4": "str(uuid.uuid4())",
    "pid": "os.getpid()",
}
