---
name: artclaw-creative-suite
description: |
  ARTCLAW AI Creative Suite - invoke ARTCLAW platform's AI content creation capabilities via CLI.
  Supports AI image generation, video generation with optional BGM/audio, audio/BGM generation, workflow execution, multimodal analysis, model selection guidance, and job management.
  All generation commands must run asynchronously using the current platform adapter. Requires an API Key prefixed with vk_ for authenticated features.
  Trigger keywords: generate image, generate video, BGM, background music, audio generation, AI painting, text-to-image, text-to-video, image-to-video, marketing image,
  logo, cover, workflow, video analysis, image analysis, model list, switch model, ARTCLAW, ArtClaw.
compatibility:
  dependencies:
    - ARTCLAW REST API (https://artclaw.com/api/v1)
    - Python 3.8+ with requests package
metadata:
  {
    'openclaw':
      {
        'emoji': '🎨',
        'requires': { 'env': ['ARTCLAW_API_KEY'] },
        'primaryEnv': 'ARTCLAW_API_KEY',
      },
  }
---

# ARTCLAW AI Creative Suite

ARTCLAW is an all-in-one AI content creation platform. This skill uses `scripts/artclaw.py` as the single CLI entry point for authentication, submission, polling, retry, history, and JSON output.

## Mandatory Startup Flow

1. Detect the current agent platform.
2. Read exactly one matching adapter document from `docs/adapters/` before running generation or workflow commands.
3. Run the pre-flight API key check before authenticated operations.
4. Never mix execution rules from multiple adapters.

If platform detection is ambiguous, ask the user which platform they are using. If the platform is unsupported, use `docs/adapters/others.md`.

## Platform Adapter Map

| Platform | Adapter document |
| --- | --- |
| OpenClaw | `docs/adapters/openclaw.md` |
| Claude Code | `docs/adapters/claude-code.md` |
| Hermes Agent | `docs/adapters/hermes.md` |
| Unknown / unsupported platform | `docs/adapters/others.md` |

## Universal Rules

1. Use the CLI, not raw curl: `python3 scripts/artclaw.py ...`.
2. Run `python3 scripts/artclaw.py verify-key` before authenticated operations.
3. Generation and workflow commands are long-running and must not block the main agent silently.
4. In Claude Code, prefer Bash `run_in_background: true` so `/tasks` can track the local background task. **DO NOT manually poll with `job-status` after the background task completes** — the background task already polls internally and returns the final result in its output.
5. In non-spawn platforms, use `--no-wait` by default unless the selected adapter explicitly defines a different async strategy or the user explicitly asks the agent to wait.
6. In OpenClaw-compatible spawn platforms, use `--spawn` instead of `--no-wait`.
7. Immediately tell the user after a generation/workflow job is submitted or a background task is started.
8. Analysis commands are synchronous and do not require spawn/background execution.
9. Guide users to https://artclaw.com/settings for API key creation and credit top-up.
10. Deliver generated media as native platform messages when the adapter supports it; otherwise return the result URL and job metadata.
11. Platform-specific async behavior, delivery semantics, and anti-blocking rules must be defined only in the selected adapter document under `docs/adapters/` and followed strictly.
12. Mention only the relevant default/switchable models before generation.
13. Answer model questions from `Capability and Model Defaults`; do not use CLI help or API calls for model lists.
14. For batch video generation, never resubmit an item after a `job_id` was returned. If polling times out or the agent is interrupted, continue with `job-status`, `last-job`, or `history` for the existing `job_id`.

---

## API Key & Account

Run this before authenticated operations:

```bash
python3 scripts/artclaw.py verify-key
```

- `{"status": "valid"}`: continue.
- Missing, invalid, or revoked key: stop and guide the user to configure a key.

Setup:

1. Open https://artclaw.com/settings.
2. Create an API key in the API Keys section.
3. Copy the generated key. It is prefixed with `vk_` and is shown only once.
4. Configure locally:

```bash
python3 scripts/artclaw.py config-init --api-key "vk_xxx"
```

Useful account commands:

```bash
python3 scripts/artclaw.py account-info
python3 scripts/artclaw.py config
```

All local ARTCLAW data is stored under `~/.artclaw/`, including `config.json`, `last_job.json`, and `history/`.

---

## Generation Commands

Generation and workflow commands are long-running. Always follow the current platform adapter before choosing `--spawn`, Claude Code `run_in_background`, `--no-wait`, or explicit waiting.

Safe default:

- OpenClaw-compatible adapters use `--spawn`.
- Claude Code uses Bash `run_in_background: true` when available.
- Other non-spawn adapters use `--no-wait` — unless the adapter defines its own background strategy (e.g. Hermes uses background terminal; see adapter doc).
- Only omit both when the user explicitly asks the agent to wait for completion.

### User Guidance

After a successful `verify-key` with no concrete generation request yet, show this startup greeting.

Startup greeting template:

```text
ARTCLAW is ready. I can generate:
- Images: default `doubao-seedream-5-0-260128`; switch to `youchuan-v-7` for realistic style or `youchuan-niji-7` for anime.
- Videos: default `doubao-seedance-2-0-260128`; switch to `doubao-seedance-2-0-fast-260128` for speed. Dreamina, Seedance 1.5, Kling, Vidu, and HappyHorse models are also available.
- BGM/audio: use `generate-audio` with Suno `Suvo V4.5 ALL`.

Tell me whether you want an image, video, or BGM/audio. If you do not specify a model, I will use the default.
```

For concrete generation requests, mention the relevant default once and run with defaults unless the user chooses a model.

Use `generate-video` for a video with BGM. Use `generate-audio` for standalone BGM/audio.

### Capability and Model Defaults

Use this table for default and switchable models.

| Media type | Default | Switchable models | Notes |
| --- | --- | --- | --- |
| Image | `doubao-seedream-5-0-260128` | `doubao-seedream-5-0-260128`, `youchuan-v-7`, `youchuan-niji-7` | `youchuan-v-7` is the realistic style option; `youchuan-niji-7` is the anime style option. |
| Video | `doubao-seedance-2-0-260128` | `doubao-seedance-2-0-260128`, `doubao-seedance-2-0-fast-260128`, `dreamina-seedance-2-0-260128`, `dreamina-seedance-2-0-fast-260128`, `doubao-seedance-1-5-pro-251215`, `kling-v3-omni`, `viduq3-pro`, `happyhorse-1.0` | Audio/BGM is enabled by default when supported. Use `--no-generate-audio` only for silent/no-BGM output. |

### Remove Background (`remove-bg`)

Remove the background from an image using AI.

```
artclaw remove-bg --image "base64_data"
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
|  | string | Yes | Base64-encoded image data |

Example:


Returns a  — use  to check progress. When complete, the result includes the URL of the background-removed image.

| Audio/BGM | `suno` + `Suvo V4.5 ALL` | `Suvo V4.5 ALL` | Use `generate-audio` for standalone music/BGM. |

### Generate Image

```bash
python3 scripts/artclaw.py generate-image \
  --prompt "Cyberpunk cityscape at night, neon lights reflected in rainwater" \
  --aspect-ratio 16:9 \
  --no-wait
```

With references:

```bash
python3 scripts/artclaw.py generate-image \
  --prompt "Landscape painting in the same style" \
  --reference-urls https://example.com/style_ref.jpg \
  --no-wait
```

| Parameter | Description | Values |
| --- | --- | --- |
| `--prompt` | Image description, required | Text |
| `--aspect-ratio` | Aspect ratio | `16:9`, `9:16`, `1:1`, `4:3`, `21:9` |
| `--resolution` | Resolution | `1K`, `2K`, `4K` |
| `--reference-urls` | Reference image URLs or base64 data URIs | One or more values |
| `--reference-files` | Local reference files, auto-converted to base64 | One or more paths |
| `--model` | Model override | `doubao-seedream-5-0-260128` (default), `youchuan-v-7`, `youchuan-niji-7` |

### Generate Video

```bash
python3 scripts/artclaw.py generate-video \
  --prompt "Waves crashing on rocks, slow motion" \
  --aspect-ratio 16:9 \
  --duration 5 \
  --resolution 720p \
  --no-wait
```

Image-to-video:

```bash
python3 scripts/artclaw.py generate-video \
  --prompt "Make the person in the frame turn their head and smile" \
  --reference-urls https://example.com/portrait.jpg \
  --no-wait
```

| Parameter | Description | Values |
| --- | --- | --- |
| `--prompt` | Video description, required | Text |
| `--aspect-ratio` | Aspect ratio | `16:9`, `9:16`, `1:1`, `4:3`, `21:9` |
| `--duration` | Duration in seconds | `2` - `12` |
| `--resolution` | Resolution | `480p`, `720p`, `1080p` |
| `--reference-urls` | Reference image URLs or base64 data URIs | One or more values |
| `--reference-files` | Local reference image files, auto-converted | One or more paths |
| `--model` | Model override | `doubao-seedance-2-0-260128` (default), `doubao-seedance-2-0-fast-260128`, `dreamina-seedance-2-0-260128`, `dreamina-seedance-2-0-fast-260128`, `doubao-seedance-1-5-pro-251215`, `kling-v3-omni`, `viduq3-pro`, `happyhorse-1.0` |
| `--no-generate-audio` | Disable generated audio/BGM | Flag (default off; video audio/BGM is on by default) |

### Generate Marketing Image

```bash
python3 scripts/artclaw.py generate-marketing-image \
  --prompt "Summer cool drinks promotional poster" \
  --size 1080x1920 \
  --no-wait
```

### Generate Audio

```bash
python3 scripts/artclaw.py generate-audio \
  --prompt "A cheerful pop song about summer" \
  --provider suno \
  --model Suvo V4.5 ALL \
  --style pop \
  --no-wait
```

| Parameter | Description | Values |
| --- | --- | --- |
| `--prompt` | Music description or lyrics, required | Text |
| `--provider` | Audio platform provider | `suno` (default) |
| `--model` | Model ID | `Suvo V4.5 ALL` (default) |
| `--instrumental` | Instrumental only, no vocals | Flag (default off) |
| `--custom-mode` | Custom mode for precise control | Flag (default off) |
| `--style` | Music style | `pop`, `rock`, `jazz`, etc. |
| `--title` | Music title | Text |
| `--vocal-gender` | Vocal gender | `m`, `f` |
| `--negative-tags` | Style tags to exclude | Text |
| `--style-weight` | Style weight (0-1) | Float |
| `--weirdness-constraint` | Weirdness (0-1) | Float |
| `--audio-weight` | Audio weight (0-1) | Float |
| `--persona-id` | Persona ID for voice cloning | Text |

### Generate Text

```bash
python3 scripts/artclaw.py generate-text \
  --prompt "Explain quantum computing in simple terms" \
  --model Gemi 3.0 Flash
```

Sync mode (default) returns text directly. Add `--callback-url` for async job mode.

```bash
python3 scripts/artclaw.py generate-text \
  --prompt "Describe this image" \
  --reference-urls https://example.com/photo.jpg \
  --provider gemini \
  --web-search
```

| Parameter | Description | Values |
| --- | --- | --- |
| `--prompt` | Text prompt, required | Text |
| `--model` | Model ID | `Gemi 3.0 Flash` (default), `Gemi 3.5 Flash`, `Gemi 3.1 Pro`, `deepseek-chat` |
| `--provider` | LLM provider | `gemini` (default), `openai`, `deepseek` |
| `--system-instruction` | System prompt | Text |
| `--response-format` | Output format | `text` (default), `json_object` |
| `--reasoning-effort` | OpenAI reasoning depth | `minimal`, `low`, `medium`, `high`, `xhigh` |
| `--thinking-level` | Gemini thinking depth | `low`, `medium`, `high` |
| `--web-search` | Enable web grounding | Flag (Gemini only) |
| `--reference-urls` | Multimodal reference (image/video/audio) | One or more URLs |

### Voice STT / TTS (Async)

Voice endpoints are **asynchronous**. Submit with audio/text → receive `job_id` → poll for result.

```bash
# Speech-to-Text
python3 scripts/artclaw.py stt --audio "data:audio/wav;base64,..."
python3 scripts/artclaw.py stt --audio-file ./recording.wav

# Text-to-Speech
python3 scripts/artclaw.py tts --text "Hello, welcome to VICOO."
python3 scripts/artclaw.py tts --text "你好" --speaker zh_female_shuangkuaishu_moon_bigtts
```

| Parameter | Description | Values |
| --- | --- | --- |
| `--audio` | Base64 audio data (STT) | `data:audio/wav;base64,...` or raw base64 |
| `--audio-file` | Local audio file, auto-converted (STT) | Path |
| `--text` | Text to synthesize (TTS), required | Text |
| `--speaker` | Speaker ID (TTS) | `en_male_tim_uranus_bigtts` (default), `zh_female_shuangkuaishu_moon_bigtts` |

Voice jobs use polling profile: interval 3s, timeout 120s.

### Execute Workflow

```bash
python3 scripts/artclaw.py list-workflows
```

```bash
python3 scripts/artclaw.py run-workflow \
  --workflow-id "text-to-image-basic" \
  --inputs '{"prompt": "Anime-style forest"}' \
  --no-wait
```

Replace `--no-wait` with `--spawn`, `--deliver-to`, and `--deliver-channel` only when the current platform adapter says to do so.

---

## Analysis Commands

Analysis commands are synchronous. They do not require `--spawn` or background execution.

### Image Analysis

```bash
python3 scripts/artclaw.py analyze-image \
  --reference-urls https://example.com/photo.jpg \
  --query "Describe the main content of this image"
```

### Video Analysis

```bash
python3 scripts/artclaw.py analyze-video \
  --reference-urls https://example.com/clip.mp4 \
  --query "Summarize the video content"
```

### Script Extraction

```bash
python3 scripts/artclaw.py analyze-script \
  --reference-paths https://example.com/drama.mp4
```

### Character Profiles

```bash
python3 scripts/artclaw.py analyze-characters \
  --text "Li Ming is an introverted but brilliant programmer..."
```

---

## Job Management & Errors

```bash
python3 scripts/artclaw.py job-status --job-id "job_xxxxxxxx"
python3 scripts/artclaw.py list-jobs --status success --limit 10
python3 scripts/artclaw.py cancel-job --job-id "job_xxxxxxxx"
python3 scripts/artclaw.py last-job
python3 scripts/artclaw.py history --limit 50
```

Use `job-status`, `last-job`, and `history` for follow-up instead of resubmitting generation requests. There is no `latest-job` command.

If a generation command returns `poll_timeout`, the job was already submitted. Do not rerun the same generation command automatically. Use the returned `job_id` with `job-status` and tell the user the existing job is still being tracked.

| Error | Cause | Resolution |
| --- | --- | --- |
| `401 Unauthorized` | API key invalid, missing, or revoked | Guide user to regenerate the key |
| `402` / insufficient credits | Account balance depleted | Guide user to top up at https://artclaw.com/settings |
| `404 Voice job not found` | Job ID does not exist or expired | Voice jobs have shorter TTL; re-submit |
| `404 Job not found` | Job ID does not exist or expired after 24h | Tell user the job expired and ask whether to regenerate |
| `404 Workflow not found` | Workflow does not exist | Run `list-workflows` first |
| `429 Too Many Requests` | Rate limit exceeded | Wait and retry |

---

## Delivery Targets

Use delivery options only when the platform adapter supports spawn/delivery mode.

`--spawn` must be paired with both `--deliver-to` and `--deliver-channel`.

| Scenario | `--deliver-channel` | `--deliver-to` value | Source |
| --- | --- | --- | --- |
| Feishu group chat | `feishu` | `oc_xxx` chat ID | `conversation_label` or `chat_id`, strip `chat:` prefix |
| Feishu direct message | `feishu` | `ou_xxx` open ID | `sender_id`, strip `user:` prefix |
| Telegram | `telegram` | `chat_id` | Inbound message context |
| Discord | `discord` | `channel_id` | Inbound message context |

For Feishu, check `is_group_chat` in inbound metadata: `true` → use `oc_` chat ID; `false` → use `ou_` open ID.

| Channel | Credential source |
| --- | --- |
| `feishu` | `~/.openclaw/openclaw.json` → `channels.feishu.accounts.main` |
| `telegram` | `TELEGRAM_BOT_TOKEN` environment variable |
| `discord` | Framework built-in message tool |

---

## Self Update

```bash
python3 scripts/artclaw.py self-update
```

Preview without writing files:

```bash
python3 scripts/artclaw.py self-update --dry-run
```

Downloads `https://github.com/ArtClaw1/artclaw-skill/archive/refs/heads/main.zip`, atomically applies added or modified files, and reports a JSON summary. Does not delete local-only files.
