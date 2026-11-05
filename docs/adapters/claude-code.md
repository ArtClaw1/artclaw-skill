# Claude Code Adapter

All workflow executions, including analysis workflows, use the same async strategy. Authentication, discovery and validation can run in the foreground.

## Preferred: background task

When Bash supports `run_in_background`, run `workflow run` without `--no-wait`:

```json
{
  "command": "python3 /absolute/path/scripts/artclaw.py workflow run --dsl-file /absolute/path/task.yaml --inputs '{}' --request-id request_1234567890",
  "description": "Run ARTCLAW workflow",
  "run_in_background": true
}
```

Tell the user the background task has started; it is visible in `/tasks`. This does not yet confirm server acceptance.

The process polls internally. Wait for the completion notification, then read its output file for the full JSON result. Do not use `TaskOutput` or start a duplicate polling loop. Deliver all named outputs and artifacts, including text and structured results.

On `poll_timeout`, continue the existing job with `jobs status` or `jobs wait` as needed. For uncertain acceptance use `workflow recover` with the original request ID and team context. Never rerun with a fresh ID.

## Fallback: server async

If Bash lacks background support, use `workflow run ... --no-wait`. Return the accepted `job_id`; use `jobs status <job_id>` for follow-up. This creates a server job, not a Claude Code background task.

Use the OpenClaw adapter only if the environment actually provides its compatible `sessions_spawn` interface and a current delivery target.
