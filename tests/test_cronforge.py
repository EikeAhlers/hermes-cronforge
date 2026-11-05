"""cronforge test suite — runs against a throwaway HERMES_HOME."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """A fake profile home with one cron job and two recorded runs."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "cron").mkdir()
    (tmp_path / "scripts").mkdir()
    jobs = {
        "jobs": [
            {
                "id": "job1",
                "name": "disk-report",
                "schedule": {"kind": "cron", "expr": "0 5 * * *"},
                "prompt": "report disk",
                "no_agent": False,
            }
        ]
    }
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    return tmp_path


def _ledger(home: Path, exec_id: str, commands: list[str], ts: float) -> None:
    path = home / "cronforge" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for i, command in enumerate(commands):
            fh.write(
                json.dumps(
                    {
                        "kind": "tool",
                        "ts": ts + i,
                        "job_id": "job1",
                        "job_name": "disk-report",
                        "execution_id": exec_id,
                        "session_id": "s",
                        "tool": "terminal",
                        "args": json.dumps({"command": command}),
                        "result": "ok",
                    }
                )
                + "\n"
            )


def test_split_commands_drops_glue():
    from cronforge.policy import split_commands

    assert split_commands('df -h /c | tail -1; echo "---"; curl -s x') == [
        "df -h /c | tail -1",
        "curl -s x",
    ]
    assert split_commands("cd /tmp && ls") == ["ls"]


def test_redact_hides_secrets():
    from cronforge.policy import redact

    out = redact("Authorization: Bearer abcdef1234567890 api_key=deadbeef")
    assert "abcdef1234567890" not in out
    assert "deadbeef" not in out


def test_grouping_differences_do_not_block(home):
    from cronforge import analyze, history

    _ledger(home, "e1", ['df -h /c | tail -1; echo "x"; curl -s http://127.0.0.1:9119/'], 1.0)
    _ledger(home, "e2", ["df -h /c | tail -1", "curl -s http://127.0.0.1:9119/"], 10.0)
    report = analyze.analyze(history.get_job("job1"))
    assert report["compilable"] is True
    assert report["steps"] == 2
    assert report["reasons"] == []


def test_volatile_dates_are_slots(home):
    from cronforge import analyze, history

    _ledger(home, "e1", ["python report.py --day 2026-11-04"], 1.0)
    _ledger(home, "e2", ["python report.py --day 2026-11-05"], 10.0)
    report = analyze.analyze(history.get_job("job1"))
    assert report["compilable"] is True
    assert report["volatile_slots"] == ["today"]


def test_judgement_tools_block_compilation(home):
    from cronforge import analyze, history

    path = home / "cronforge" / "ledger.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    for exec_id, ts in (("e1", 1.0), ("e2", 10.0)):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "kind": "tool",
                        "ts": ts,
                        "job_id": "job1",
                        "execution_id": exec_id,
                        "tool": "delegate_task",
                        "args": json.dumps({"goal": "summarise"}),
                    }
                )
                + "\n"
            )
    report = analyze.analyze(history.get_job("job1"))
    assert report["compilable"] is False
    assert "delegate_task" in report["blocking_tools"]


def test_unstable_arguments_block_compilation(home):
    from cronforge import analyze, history

    _ledger(home, "e1", ["gh issue close 12"], 1.0)
    _ledger(home, "e2", ["gh issue close 47"], 10.0)
    report = analyze.analyze(history.get_job("job1"))
    assert report["compilable"] is False
    assert any("non-volatile" in reason for reason in report["reasons"])


def test_generated_script_runs_and_exits_zero(home, tmp_path):
    from cronforge import analyze, codegen, history, ops

    _ledger(home, "e1", ["echo one > out.txt"], 1.0)
    _ledger(home, "e2", ["echo one > out.txt"], 10.0)
    job = history.get_job("job1")
    report = analyze.analyze(job)
    source = codegen.generate(report, job, "2026-11-05T00:00:00+01:00")
    script = home / "scripts" / "replay.py"
    script.write_text(source, encoding="utf-8")

    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is True and payload["steps"] == 1
    assert (tmp_path / "out.txt").read_text().strip() == "one"


def test_generated_script_fails_loudly(home, tmp_path):
    from cronforge import analyze, codegen, history

    _ledger(home, "e1", ["exit 7"], 1.0)
    _ledger(home, "e2", ["exit 7"], 10.0)
    job = history.get_job("job1")
    source = codegen.generate(analyze.analyze(job), job, "now")
    script = home / "scripts" / "replay.py"
    script.write_text(source, encoding="utf-8")

    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, cwd=str(tmp_path))
    assert proc.returncode == 1
    events = (home / "cronforge" / "events.jsonl").read_text(encoding="utf-8")
    assert "script_failure" in events


def test_scan_reports_every_job(home):
    from cronforge import ops

    data = ops.scan()
    assert [j["job_name"] for j in data["jobs"]] == ["disk-report"]
    assert data["jobs"][0]["compilable"] is False  # no runs yet


def test_compile_refuses_without_evidence(home):
    from cronforge import ops

    result = ops.compile_job("job1")
    assert result["ok"] is False
    assert "not compilable" in result["error"]
