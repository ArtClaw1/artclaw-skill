# OpenClaw Adapter

Use this adapter only when the environment exposes OpenClaw-compatible `sessions_spawn`.

For every workflow execution, including analysis:

```bash
python3 /absolute/path/scripts/artclaw.py workflow run \
  --dsl-file /absolute/path/task.yaml --inputs '{}' \
  --request-id request_1234567890 --spawn \
  --deliver-to <current_conversation_id> --deliver-channel <channel>
```

1. Take the destination from the current conversation context. For Feishu groups use the `oc_` chat ID; for direct messages use the `ou_` sender ID. Remove metadata prefixes such as `chat:` or `user:`. For Telegram use the current chat ID; for Discord use the current channel ID.
2. Run the CLI and pass returned `sessions_spawn_args` to `sessions_spawn`.
3. Tell the user the background worker has started. The CLI spawn response is a plan, not an accepted server job.
4. The worker waits and delivers every named output and artifact using available platform tools. Preserve text and structured data as well as media.
5. After acceptance, track the existing job ID. For uncertain acceptance, use `workflow recover <request_id>` with the same team context; never create another submission.

Both delivery flags are required in spawn mode. Do not invent destinations or send to another conversation without the user's request. If the current destination is unavailable, use the generic adapter's server async mode.

Authentication, discovery and validation do not need a worker.
