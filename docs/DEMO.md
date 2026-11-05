# 90-second demo — shot list

Goal: show a job that burns tokens become a job that burns none, and show the failure path
that makes it safe to do so. Record the whole thing in one take, no cuts across the proof.

## Before recording

```bash
hermes plugins list | grep cronforge          # enabled
hermes cron list                              # any job you have run at least twice
```

Pick a job that always runs the same commands. If you don't have one, create the demo job:

```bash
hermes cron create "0 5 * * *" \
  'Run exactly these two shell commands with the terminal tool, in this order, then report both outputs verbatim. Use no other tool: 1) df -h /c | tail -1  2) curl -s -I http://127.0.0.1:9119/ | head -1' \
  --name "cronforge-demo" --deliver local
hermes cron run <job id>   # twice, a minute apart, so two runs are on record
```

## Shot list (85 s)

| Time | On screen | Say |
|---|---|---|
| 0–8 s | `hermes cron list`, highlight one job | "Every run of this job starts a full agent session — four thousand tokens to type the same two commands." |
| 8–20 s | ask: *"scan my cron jobs with cronforge"* → table | "cronforge reads the real run history: two runs, same commands, arguments match, no judgement steps. It says compilable — and says why." |
| 20–32 s | `cronforge_compile` → script path + 127 lines | "It compiles those runs into a plain Python script — one step per command, the original exit codes asserted." |
| 32–45 s | open the script, scroll the STEPS block and the assertion | "No prompt, no model. If a command deviates, the script fails loudly instead of guessing." |
| 45–58 s | `cronforge_verify` → `{"ok": true ...}` with real output | "Verify runs it once for real: disk line, HTTP status, exit 0." |
| 58–70 s | `cronforge_activate` → mode: script; then a scheduler run, `script_success` | "Activated: the scheduler now runs the script. Zero tokens. The agent is out of the loop." |
| 70–85 s | break a step (`exit 3`), run twice, show `rolled_back` + `no_agent: false` | "And if the world drifts, two failing runs hand the job straight back to the agent — automatically, no watcher." |

## Voice-over (short version)

> "Cron jobs re-burn tokens to repeat work the agent already solved. cronforge proves which jobs are
> deterministic from their own run history, compiles them into a script with the original exit codes
> asserted, and switches the job to zero-token mode. If the script ever drifts, it hands the job back
> after two failures. Same job, same output, no model."

## Post caption

> cronforge for @NousResearch Hermes: cron jobs that re-run the same commands become 0-token
> scripts — compiled from proven run history, with exit-code assertions and automatic rollback.
> Repo: https://github.com/EikeAhlers/hermes-cronforge
