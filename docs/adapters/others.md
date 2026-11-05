# Generic Adapter

Use this adapter when no dedicated adapter applies. An arbitrary delegation tool does not imply compatibility with OpenClaw's `sessions_spawn_args`.

For workflow execution, including analysis:

```bash
python3 /absolute/path/scripts/artclaw.py workflow run \
  --dsl-file /absolute/path/task.yaml --inputs '{}' \
  --request-id request_1234567890 --no-wait
```

Return the accepted `job_id` and track it with `jobs status <job_id>`.

If the user asks to wait, use `jobs wait <job_id>` for an accepted job, or omit `--no-wait` on initial submission with an available background execution tool. Tell the user when background work starts. Do not block the main turn silently.

A polling timeout is not a reason to regenerate. Use `workflow recover <request_id>` for uncertain acceptance, preserving team context. Deliver all named outputs and artifacts through available native rendering or links/paths.

Authentication, discovery and validation run synchronously. Use OpenClaw's adapter only when its exact spawn interface and a current delivery target are available.
