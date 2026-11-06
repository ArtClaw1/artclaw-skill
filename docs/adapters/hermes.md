# Hermes Agent Adapter

Run workflow execution in the background, including analysis. Authentication, discovery and validation can run normally.

```text
terminal(
  command: "python3 /absolute/path/scripts/artclaw.py workflow run --dsl-file /absolute/path/task.yaml --inputs '{}' --request-id request_1234567890",
  background: true,
  notify_on_complete: true
)
```

Omit `--no-wait`: the CLI process waits and polls internally. Immediately tell the user the background task has started.

On completion, read that process's output for its full result and job ID. Records also live under `~/.artclaw/history/` and `~/.artclaw/last_job.json`. Only use a record whose job ID matches this task; concurrent tasks can replace `last_job.json`. Use matching history if necessary. Do not start API polling after a terminal result is available.

Deliver every named `result.outputs` and typed `result.artifacts`. Reply with media URLs as plain text (no `MEDIA:` prefix), plus text/structured outputs. Do not assume a single `result_url`.

For nonterminal output or `poll_timeout`, report the existing job as pending. Use `jobs status` or `jobs wait` for a subsequent status request; do not submit another job. For uncertain acceptance use `workflow recover` with the original request ID and team context.

## Delivery and formatting

The platform delivers replies automatically. Do not call Feishu, Telegram or other delivery tools unless the user explicitly requests that delivery.

Do not use Markdown tables in Feishu replies; use simple bullets. If the platform cannot render a returned local artifact, provide its path and explain the limitation instead of discarding it or inventing a remote URL.
