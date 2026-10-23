# Runtime logging

`miniagent`, `miniagent run`, `miniagent resume`, `trigger`, and `eval.py` enable logging automatically. Normal result output remains on stdout; progress goes to stderr. No additional package is required.

## Configuration

Set environment variables or entries in `.env`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MINIAGENT_LOG_LEVEL` | `INFO` | stderr threshold: DEBUG, INFO, WARNING, ERROR |
| `MINIAGENT_LOG_FILE_LEVEL` | `INFO` | JSONL threshold, independent of stderr |
| `MINIAGENT_LOG_DIR` | `~/.miniagent/logs` | Log root |
| `MINIAGENT_LOG_FILE_ENABLED` | `true` | Enable JSONL files |
| `MINIAGENT_LOG_RETENTION_DAYS` | `14` | Retention for dated invocation logs |
| `MINIAGENT_PROGRESS_INTERVAL_SECONDS` | `15` | Heartbeat interval; `0` disables heartbeats |

For example:

```sh
MINIAGENT_PROGRESS_INTERVAL_SECONDS=5 uv run miniagent 'inspect this repository'
MINIAGENT_LOG_FILE_LEVEL=DEBUG uv run python trigger --instance-id django__django-11477
MINIAGENT_LOG_DIR=/tmp/miniagent-logs uv run python eval.py all
```

The startup log reports the JSONL location: `<root>/<UTC-date>/<invocation-id>.jsonl`. Each file rotates at 20 MiB with three backups. Old generated files are removed on a later logging startup. Each eval worker has its own invocation; the parent displays worker starts/completions and log paths. Detailed worker stderr remains in the existing `eval/<batch>/<index>.log` files.

`trigger` defaults to `DEFAULT_AGENT.model.model`, as miniagent and eval do. `--model` and `MINIAGENT_TRIGGER_MODEL` still override it.

## What the logs mean

Committed Run, Step, model, tool, compaction, policy/approval, evaluation and resume events emit safe summaries only after persistence succeeds. Preparation, retry waits, evaluation harness/cleanup/report collection, artifact collection, trajectory export and Workspace cleanup also expose their operation stages. `run_finished` is the Runtime outcome; `invocation.completed` occurs after application finalization and CLI result delivery.

A long operation emits `operation.waiting` with its phase and elapsed time. Nested operations suppress their parent's heartbeat, so evaluation harness waits do not produce duplicate generic evaluation heartbeats. Request attempts and tool waits carry their available Run/Step/attempt identifiers. Each request attempt restarts its elapsed timer. Heartbeats stop on success, failure, timeout or cancellation and are not journal events.

A heartbeat means the event loop is still waiting. It does not prove remote model progress or process health. Blocking synchronous work can prevent heartbeat scheduling. Task completion percentages and raw stdout/stderr streaming are not inferred.

Logs whitelist metadata fields and omit task text, prompts, model responses, tool arguments, command text and output bodies, including at DEBUG. Use the existing journal/artifacts/dashboard for detailed evidence. Log summaries contain error codes/types; ordinary CLI error messages and existing eval worker tracebacks retain their previous behavior.

## Failure behavior

Logging uses a bounded background queue. When congested, heartbeats are dropped before other records; a full queue can drop other records as well. The delivery thread reports dropped-record counts when it drains. Shutdown waits at most 0.5 seconds for log delivery; a blocked output device or hard process exit can lose pending diagnostics. JSONL logs are not a recovery source.

An unavailable file destination produces a stderr warning and leaves console delivery active. A file that fails during delivery is disabled after one warning. Log write failures do not replace the Run result; journal persistence failures retain their existing fail-fast semantics.

Library callers do not install handlers automatically. They can configure Python logging themselves; CLI logging owns handler setup/teardown and preserves existing host handlers. Heartbeats are enabled by the invocation's logging session.
