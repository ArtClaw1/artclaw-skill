---
name: artclaw-creative-suite
description: |
  ARTCLAW AI Creative Suite - invoke ARTCLAW platform's AI content creation capabilities via CLI.
  Supports AI image/video/audio/text generation, reference music, video-to-audio, background removal, voice clone/conversion/separation, lip sync, image/video enhancement, subtitles, dubbing, workflows and job management.
  Job-backed generation commands must run asynchronously using the current platform adapter. Synchronous voice conversion and stem separation may take several minutes. Requires an API Key prefixed with vk_ for authenticated features.
  Trigger keywords: generate image, generate video, BGM, background music, audio generation, AI painting, text-to-image, text-to-video, image-to-video,
  logo, cover, workflow, remove background, text generation, model list, switch model, ARTCLAW, ArtClaw.
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

| Platform                       | Adapter document               |
| ------------------------------ | ------------------------------ |
| OpenClaw                       | `docs/adapters/openclaw.md`    |
| Claude Code                    | `docs/adapters/claude-code.md` |
| Hermes Agent                   | `docs/adapters/hermes.md`      |
| Unknown / unsupported platform | `docs/adapters/others.md`      |

## Universal Rules

1. Use the CLI, not raw curl: `python3 scripts/artclaw.py ...`.
2. Run `python3 scripts/artclaw.py verify-key` before authenticated operations.
3. Generation and workflow commands are long-running and must not block the main agent silently.
4. In Claude Code, prefer Bash `run_in_background: true` so `/tasks` can track the local background task. **DO NOT manually poll with `job-status` after the background task completes** — the background task already polls internally and returns the final result in its output.
5. In non-spawn platforms, use `--no-wait` by default unless the selected adapter explicitly defines a different async strategy or the user explicitly asks the agent to wait.
6. In OpenClaw-compatible spawn platforms, use `--spawn` instead of `--no-wait`.
7. Immediately tell the user after a generation/workflow job is submitted or a background task is started.
8. Guide users to https://artclaw.com/settings for API key creation and credit top-up.
9. Deliver generated media as native platform messages when the adapter supports it; otherwise return the result URL and job metadata.
10. Platform-specific async behavior, delivery semantics, and anti-blocking rules must be defined only in the selected adapter document under `docs/adapters/` and followed strictly.
11. Mention only the relevant default/switchable models before generation.
12. Answer model questions from `Capability and Model Defaults`; do not use CLI help or API calls for model lists.
13. For batch video generation, never resubmit an item after a `job_id` was returned. If polling times out or the agent is interrupted, continue with `job-status`, `last-job`, or `history` for the existing `job_id`.
14. Before `voice-clone`, read [Voice selection and cloning](references/voice-clone.md). Ask about voice choice only when it is missing; query `list-voices` for real catalog IDs and show a short shortlist. Seed Audio is the recommended default; preserve explicit IndexTTS choices. Seed Audio counts speaker plus reference audio together toward its three-reference limit.

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

## Team Billing

For team accounts, generation tasks can bill a team's credits instead of the individual account by sending a `team_id` (a team UUID) in the request body. This applies to image/video/audio generation, background removal, voice operations, lip sync, postprocessing, and billable workflow nodes. An API key bound to a team takes precedence over a request `team_id` on direct generation endpoints; workflow commands must send a team ID explicitly or through `ARTCLAW_TEAM_ID` / persisted config. The API checks team membership.

**The agent must guide the user to provide their `team_id` before generating with team billing.**

### How the user finds their `team_id`

In the VICOO web app:

1. Click the avatar / user menu in the top bar.
2. Open **团队设置** (Team Settings) — or **团队管理** (Team Management).
3. The team header shows a **团队 ID** (Team ID) line with a copy button. Click the copy icon to copy the team UUID, and paste it to the agent.

The copied value is the `team_id` UUID to use. If the user is not on a team, team billing is unavailable — bill the account instead (omit `team_id`).

### How to send it (highest priority first)

1. Per command: `--team-id <team-uuid>`
2. Environment variable: `ARTCLAW_TEAM_ID=<team-uuid>`
3. Persisted config: `python3 scripts/artclaw.py config-init --api-key "vk_xxx" --team-id "<team-uuid>"`

Example:

```bash
python3 scripts/artclaw.py generate-video \
  --prompt "A kitten" \
  --team-id 8f8bacf4-e130-4221-8bab-58cf14c755fb \
  --no-wait
```

- Omit `team_id` entirely to bill the account itself (default).
- If the user is not a member of the given team, the API returns `403`.

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
ARTCLAW is ready. Here is everything I can create, the models available, and what each supports — just tell me your need (and optionally a model; I use the default if you don't pick one):

| Type | Command | Models (★ = default) | Key options |
| --- | --- | --- | --- |
| 🖼️ Image | `generate-image` | ★`doubao-seedream-5-0-260128` · `doubao-seedream-5-0-pro-260628` · `youchuan-v-7` · `youchuan-niji-7` · `Navo Bana 2.5 Flash Image` · `Navo Bana 3.0 Pro Image` · `Navo Bana 3.1 Flash Image` · `Navo Bana 3.1 Flash Lite Image` · `Kling Image 3.0` · `Kling Image 3.0 Omni` · `GT-Image-2` · `GT-Image-2.5-Flash` · `GT-Image-2.5` | model-dependent aspect ratios and resolution · reference images · search enhancement · transparent background |
| 🎬 Video | `generate-video` | ★`doubao-seedance-2-0-260128` · `doubao-seedance-2-0-fast-260128` · `doubao-seedance-2-0-mini-260615` · `doubao-seedance-2-5-260628` · `doubao-seedance-1-5-pro-251215` · `kling-v3-omni` · `viduq3-pro` · `happyhorse-1.0` · `happyhorse-1.1` · `Vo 3.1` · `Vo 3.1 Fast` · `Grk Video` | aspect ratio · model-dependent duration (Seedance 2.5 up to 30s) · model-dependent 480p / 720p / 1080p / 4k · typed image/video/audio references · Seedance 2.5 MP4 / MOV output · BGM on by default (`--no-generate-audio` to mute) |
| 🎬 Seedance | `generate-seedance-video` | ★`doubao-seedance-2-0-260128` · `doubao-seedance-2-0-fast-260128` · `doubao-seedance-2-0-mini-260615` · `doubao-seedance-2-5-260628` | duration 4–15s (2.0) / 4–30s (2.5) · resolution 480p / 720p / 1080p (720p default) · Seedance 2.5 MP4 / MOV output · return last frame · priority · custom timeout · safety identifier |
| 🎵 Music / BGM | `generate-audio` | Suno: ★`Suvo V4.5 ALL` · `Suvo V5` · `Suvo V5.5`; Kling video-to-audio | instrumental/custom music · cover/mashup references · video sound effects/BGM |
| 📝 Text | `generate-text` | ★`deepseek-v4-pro` · `deepseek-v4-flash` (faster) · `Gemi 3.0 Flash` · `Gemi 3.1 Pro` · `Gemi 3.1 Flash Lite` · `Gemi 3.5 Flash` · `GT-5.5` | system instruction · `json_object` output · sync (default) or async · **image/video/audio analysis via `--reference-parts` (Gemi only)** |
| 🎙️ Speech→Text | `stt` | — | audio (base64 / file) → text · async |
| 🎙️ Text→Speech | `tts` | speakers: ★`en_male_tim_uranus_bigtts` · `zh_female_shuangkuaishu_moon_bigtts` · … | text → audio · pick voice via `--speaker` · async |
| ✂️ Remove BG | `remove-bg`, `remove-bg-image`, `remove-bg-video` | PhotoRoom · chroma key · Aliyun | image and video background removal |
| 🎙️ Voice | `voice-clone`, `list-voices`, `voice-convert`, `stem-separate` | ★Seed Audio · IndexTTS · DDSP/UVR | choose preset/reference audio for either clone model; Volcengine official speakers for Seed Audio only; convert timbre, separate stems |
| 🎞️ Editing | `video-edit`, `video-extend`, `video-timeline`, `video-retalk`, `image-enhance`, `smart-erase`, `smart-subtitles`, `ai-dubbing`, `video-enhance` | Creator segment retake, Seedance 2.5 extend, compositor | selected-interval AI retake, continuation, timeline cuts/composition and other postprocessing |
| 🧩 Workflow | `list-workflows` / `run-workflow` | preset workflows (run `list-workflows` to see them) | run a preset pipeline with JSON inputs |
| 🧩 Workflow (DSL) | `run-workflow-dsl` | workflow DSL YAML source | run a workflow defined by DSL, compiled server-side |

Tell me what you want to create!
```

Show this full table as the greeting. Localize the wording to the user's language, but keep model IDs and command names verbatim.

For concrete generation requests, mention the relevant default once and run with defaults unless the user chooses a model.

Use `generate-video` for a video with BGM. Use `generate-audio` for standalone BGM/audio.

### Capability and Model Defaults

Use this table for default and switchable models.

| Media type | Default                                     | Switchable models                                                                                                                                                                                                                                                                                                       | Notes                                                                                                                                                                                                                                                                                                            |
| ---------- | ------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Image      | `doubao-seedream-5-0-260128`                | `doubao-seedream-5-0-260128`, `doubao-seedream-5-0-pro-260628`, `youchuan-v-7`, `youchuan-niji-7`, `Navo Bana 2.5 Flash Image`, `Navo Bana 3.0 Pro Image`, `Navo Bana 3.1 Flash Image`, `Navo Bana 3.1 Flash Lite Image`, `Kling Image 3.0`, `Kling Image 3.0 Omni`, `GT-Image-2`, `GT-Image-2.5-Flash`, `GT-Image-2.5` | `youchuan-v-7`: realistic; `youchuan-niji-7`: anime; `Navo Bana`: Gemini image models. `GT-Image-2.5-Flash`: speed; `GT-Image-2.5`: editing precision. Both 2.5 variants support quality `low` / `medium` / `high` / `xhigh` / `max` (default `high`) and transparent background via `--background transparent`. |
| Video      | `doubao-seedance-2-0-260128`                | `doubao-seedance-2-0-260128`, `doubao-seedance-2-0-fast-260128`, `doubao-seedance-2-0-mini-260615`, `doubao-seedance-2-5-260628`, `doubao-seedance-1-5-pro-251215`, `kling-v3-omni`, `viduq3-pro`, `happyhorse-1.0`, `happyhorse-1.1`, `Vo 3.1`, `Vo 3.1 Fast`, `Grk Video`                                             | Audio/BGM is enabled by default when supported. Use `--no-generate-audio` only for silent/no-BGM output.                                                                                                                                                                                                         |
| Audio/BGM  | `suno` + `Suvo V4.5 ALL`                    | `Suvo V4.5 ALL`, `Suvo V5` · `Suvo V5.5`                                                                                                                                                                                                                                                                                | Use `generate-audio` for standalone music/BGM.                                                                                                                                                                                                                                                                   |
| Text       | `deepseek-v4-pro`                           | `deepseek-v4-pro`, `deepseek-v4-flash`, `Gemi 3.0 Flash`, `Gemi 3.1 Pro`, `Gemi 3.1 Flash Lite`, `Gemi 3.5 Flash`, `GT-5.5`                                                                                                                                                                                             | `deepseek-v4-flash` is the faster/cheaper option. **Gemi models support multimodal input** via `--reference-parts` (image/video/audio → text).                                                                                                                                                                   |
| Voice      | STT: auto; TTS: `en_male_tim_uranus_bigtts` | TTS speakers, e.g. `en_male_tim_uranus_bigtts`, `zh_female_shuangkuaishu_moon_bigtts`                                                                                                                                                                                                                                   | `stt` = speech→text, `tts` = text→speech; both async. TTS voice is chosen via `--speaker`.                                                                                                                                                                                                                       |

For `voice-clone`, the recommended default is **Seed Audio** (`seed-audio`); **IndexTTS** (`indextts`) is the alternative. Both accept preset/reference audio; Volcengine speaker IDs require Seed Audio. Query individual voices with `list-voices` as described in [Voice selection and cloning](references/voice-clone.md).

### Remove Background (`remove-bg`)

Remove the background from an image using AI. Returns a `job_id` — use `job-status` to check progress. When complete, the result includes the URL of the background-removed image.

```bash
python3 scripts/artclaw.py remove-bg --image "base64_data"
python3 scripts/artclaw.py remove-bg --image "@./portrait.png"
```

| Parameter   | Description                | Values                         |
| ----------- | -------------------------- | ------------------------------ |
| `--image`   | Image input, required      | Base64 string or `@local-file` |
| `--team-id` | Team UUID for team billing | Team UUID                      |

With team billing:

```bash
python3 scripts/artclaw.py remove-bg \
  --image "base64_data" \
  --team-id 8f8bacf4-e130-4221-8bab-58cf14c755fb
```

The CLI selects the request's `team_id` in this order: `--team-id`, `ARTCLAW_TEAM_ID`, then persisted `teamId` from `config-init`. If none is configured, it omits `team_id`. Server-side team validation and any team bound to the authentication context still apply.

For model-specific dimensions, references, editing, music, background removal, voice and postprocessing request examples, read [Direct generation controls](references/direct-generation.md). These controls supplement the command tables below.

### Generate Image

```bash
python3 scripts/artclaw.py generate-image \
  --prompt "Cyberpunk cityscape at night, neon lights reflected in rainwater" \
  --aspect-ratio 16:9 \
  --no-wait
```

With references (HTTPS URL, base64 data URI, or local file uploaded to Creator):

```bash
# HTTPS URL
python3 scripts/artclaw.py generate-image \
  --prompt "Landscape painting in the same style" \
  --reference-urls https://example.com/style_ref.jpg \
  --no-wait

# Local file (uploaded first; generated task receives its CDN URL)
python3 scripts/artclaw.py generate-image \
  --prompt "Landscape painting in the same style" \
  --reference-files ./style_ref.png \
  --no-wait
```

| Parameter           | Description                                        | Values                                                                                                                                                                                                                                                                                                                            |
| ------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--prompt`          | Image description, required                        | Text                                                                                                                                                                                                                                                                                                                              |
| `--aspect-ratio`    | Aspect ratio                                       | Positive `W:H`, `auto` or `adaptive` (model-dependent)                                                                                                                                                                                                                                                                            |
| `--resolution`      | Resolution                                         | `512`, `1K`, `2K`, `3K`, `4K` (model-dependent)                                                                                                                                                                                                                                                                                   |
| `--reference-urls`  | Reference image URLs or base64 data URIs           | One or more values                                                                                                                                                                                                                                                                                                                |
| `--reference-files` | Local reference images, uploaded before generation | One or more paths                                                                                                                                                                                                                                                                                                                 |
| `--model`           | Model override                                     | `doubao-seedream-5-0-260128` (default), `doubao-seedream-5-0-pro-260628`, `youchuan-v-7`, `youchuan-niji-7`, `Navo Bana 2.5 Flash Image`, `Navo Bana 3.0 Pro Image`, `Navo Bana 3.1 Flash Image`, `Navo Bana 3.1 Flash Lite Image`, `Kling Image 3.0`, `Kling Image 3.0 Omni`, `GT-Image-2`, `GT-Image-2.5-Flash`, `GT-Image-2.5` |
| `--quality`         | Image quality, model-dependent                     | `GT-Image-2.5` / `GT-Image-2.5-Flash`: `low`, `medium`, `high`, `xhigh`, `max` (default `high`); `GT-Image-2`: `auto`, `low`, `medium`, `high`                                                                                                                                                                                    |
| `--background`      | Background mode (GT-Image family)                  | `auto`, `opaque`, `transparent`; `transparent` needs an alpha-capable format (not `jpeg`)                                                                                                                                                                                                                                         |
| `--output-format`   | Output file format                                 | `png`, `jpeg`, `webp`                                                                                                                                                                                                                                                                                                             |
| `--team-id`         | Team UUID for team billing                         | Team UUID, e.g. `8f8bacf4-e130-4221-8bab-58cf14c755fb`                                                                                                                                                                                                                                                                            |

**Reference images.** `--reference-files` and `--reference-urls @./image.png` upload local images through Creator's authenticated media-upload entry point and use the returned CDN URLs. `--reference-urls` also accepts existing HTTPS URLs or image base64 data URIs. Local images must be JPG/JPEG/PNG/WebP/GIF/BMP and at most 30 MB.

**GT-Image-2.5 models.** Use the exact public names `GT-Image-2.5-Flash` for speed or `GT-Image-2.5` for editing precision in both CLI and REST requests. Both support text-to-image, reference images, and 1K / 2K / 4K resolution. Omitted or empty quality defaults to `high`; `auto` and unknown tiers are rejected for these two models before a generation job is submitted. Pass the chosen `low` / `medium` / `high` / `xhigh` / `max` value unchanged. Add `--background transparent` for a transparent background (PNG output keeps the alpha channel; `jpeg` is rejected).

Example for adapters that use `--no-wait`:

```bash
python3 scripts/artclaw.py generate-image \
  --prompt "A red cat in a sunlit garden" \
  --model GT-Image-2.5-Flash \
  --quality max \
  --resolution 2K \
  --no-wait
```

Replace `GT-Image-2.5-Flash` with `GT-Image-2.5` to choose the other variant. Apply the selected platform adapter's asynchronous strategy: OpenClaw uses `--spawn` with delivery options; Claude Code uses its background task mechanism. The model and quality options also work with image `spawn-task` commands.

### Generate Video

```bash
python3 scripts/artclaw.py generate-video \
  --prompt "Waves crashing on rocks, slow motion" \
  --aspect-ratio 16:9 \
  --duration 5 \
  --resolution 720p \
  --no-wait
```

Seedance 2.5 MOV output:

```bash
python3 scripts/artclaw.py generate-video \
  --prompt "Waves crashing on rocks, slow motion" \
  --model doubao-seedance-2-5-260628 \
  --resolution 1080p \
  --output-format mov \
  --no-wait
```

Image-to-video (HTTPS URL, base64 data URI, or local file uploaded to Creator):

```bash
# HTTPS URL
python3 scripts/artclaw.py generate-video \
  --prompt "Make the person in the frame turn their head and smile" \
  --reference-urls https://example.com/portrait.jpg \
  --no-wait

# Local file (uploaded first; generated task receives its CDN URL)
python3 scripts/artclaw.py generate-video \
  --prompt "Make the person in the frame turn their head and smile" \
  --reference-files ./portrait.png \
  --no-wait
```

| Parameter             | Description                                 | Values                                                                                                                                                                                                                                                                                |
| --------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--prompt`            | Video description, required                 | Text                                                                                                                                                                                                                                                                                  |
| `--aspect-ratio`      | Aspect ratio                                | Positive `W:H`, `auto` or `adaptive` (model-dependent)                                                                                                                                                                                                                                |
| `--duration`          | Duration in seconds                         | Model-dependent; Seedance 2.0: `4` - `15`, Seedance 2.5: `4` - `30`                                                                                                                                                                                                                   |
| `--resolution`        | Resolution                                  | `480p`, `720p`, `1080p`, `4k` (model-dependent)                                                                                                                                                                                                                                       |
| `--reference-urls`    | Reference image URLs or base64 data URIs    | One or more values                                                                                                                                                                                                                                                                    |
| `--reference-files`   | Local reference image files, uploaded first | One or more paths                                                                                                                                                                                                                                                                     |
| `--model`             | Model override                              | `doubao-seedance-2-0-260128` (default), `doubao-seedance-2-0-fast-260128`, `doubao-seedance-2-0-mini-260615`, `doubao-seedance-2-5-260628`, `doubao-seedance-1-5-pro-251215`, `kling-v3-omni`, `viduq3-pro`, `happyhorse-1.0`, `happyhorse-1.1`, `Vo 3.1`, `Vo 3.1 Fast`, `Grk Video` |
| `--output-format`     | Output container; Seedance 2.5 only         | `mp4` (default), `mov`                                                                                                                                                                                                                                                                |
| `--no-generate-audio` | Disable generated audio/BGM                 | Flag (default off; video audio/BGM is on by default)                                                                                                                                                                                                                                  |
| `--team-id`           | Team UUID for team billing                  | Team UUID, e.g. `8f8bacf4-e130-4221-8bab-58cf14c755fb`                                                                                                                                                                                                                                |

**Reference images (I2V).** `--reference-files` uploads local images and places their CDN URLs in `reference_image` items. `--reference-urls` accepts HTTPS URLs or image base64 data URIs. For local video/audio references, use typed `--content-items` with `reference_video` or `reference_audio` and `@path`.

`generate-seedance-video` accepts the same `--output-format mp4|mov` option. Explicit output format selection requires `--model doubao-seedance-2-5-260628`; MP4 remains the default when the option is omitted.

Typed first/last frames and video/audio references use `--content-items`; generic video also supports `--seed`, search and Seedance 2.5 task types. See [Video references and editing](references/direct-generation.md#video-references).

For a local reference video, use `--content-items '[{"role":"reference_video","url":"@/path/to/clip.mp4"}]'`. The CLI uploads it to Creator before submitting the video task, then passes the returned `https://assets.vicoo.ai/...` URL under the same role. The same `@path` syntax supports `reference_image` and `reference_audio`; local MP4/MOV, supported images and MP3/WAV/M4A/OGG/AAC/FLAC are accepted. Do not put a video file in `--reference-files`: that legacy flag is image-only. Invalid files or failed uploads stop before generation. A dry run never uploads; spawn mode uploads in the child process, which must be able to read the same local path.

For an existing video, route by intent: regenerate one selected interval → `video-edit` with `source_video_url`, `start`, `end`, and `prompt`; generate additional seconds after it → `video-extend`; cut, reorder or concatenate clips → `video-timeline`. Despite its name, `video-edit` is Creator's **segment retake**, not a general-purpose video editor. It also accepts `reference_image_urls`, `reference_video_urls`, and `reference_audio_urls` as optional guidance for the replacement segment. In structured `--input` JSON, `source_video_url`, reference image/video/audio URL arrays and timeline clip `url` accept `@local-file`; the CLI uploads each file before task submission. Retake and timeline media are platform-hosted at `https://assets.vicoo.ai` by the time they reach REST. External audio tracks are not supported by the timeline command. A timeline cut probes the source duration automatically, so do not ask the user to supply `source_duration`. These are separate billable operations. `video-edit` reuses Creator's deployed segment-retake chain and supports exactly one 4–30 second interval. If a user says only “edit 12–14 seconds”, ask whether to **cut** that interval or **regenerate its visual content** before submitting. For a 12–14 second retake request, explain the 4-second minimum and ask the user to choose a new range; never silently widen it or substitute timeline cutting. A 12–14 second _cut_ is valid. See [direct video editing examples](references/direct-generation.md#direct-video-editing-extension-and-timeline).

### Generate Audio

```bash
python3 scripts/artclaw.py generate-audio \
  --prompt "A cheerful pop song about summer" \
  --provider suno \
  --model "Suvo V4.5 ALL" \
  --style pop \
  --no-wait
```

| Parameter                | Description                           | Values                                                 |
| ------------------------ | ------------------------------------- | ------------------------------------------------------ |
| `--prompt`               | Music description or lyrics, required | Text                                                   |
| `--provider`             | Audio platform provider               | `suno` (default)                                       |
| `--model`                | Model ID                              | `Suvo V4.5 ALL` (default), `Suvo V5` · `Suvo V5.5`     |
| `--instrumental`         | Instrumental only, no vocals          | Flag (default off)                                     |
| `--custom-mode`          | Custom mode for precise control       | Flag (default off)                                     |
| `--style`                | Music style                           | `pop`, `rock`, `jazz`, etc.                            |
| `--title`                | Music title                           | Text                                                   |
| `--vocal-gender`         | Vocal gender                          | `m`, `f`                                               |
| `--negative-tags`        | Style tags to exclude                 | Text                                                   |
| `--style-weight`         | Style weight (0-1)                    | Float                                                  |
| `--weirdness-constraint` | Weirdness (0-1)                       | Float                                                  |
| `--audio-weight`         | Audio weight (0-1)                    | Float                                                  |
| `--persona-id`           | Persona ID for voice cloning          | Text                                                   |
| `--team-id`              | Team UUID for team billing            | Team UUID, e.g. `8f8bacf4-e130-4221-8bab-58cf14c755fb` |

Suvo V5.5 custom mode also supports `--duration 10..360` seconds. See the [music example](references/direct-generation.md#music).

### Generate Text

```bash
python3 scripts/artclaw.py generate-text \
  --prompt "Explain quantum computing in simple terms"
```

Sync mode (default) returns text directly. Add `--callback-url` for async job mode.

```bash
python3 scripts/artclaw.py generate-text \
  --prompt "Return a JSON list of 3 startup ideas" \
  --model deepseek-v4-flash \
  --response-format json_object
```

Multimodal analysis (image / video / audio → text) requires a **Gemi** model. Use `--reference-parts` with `type=url` (public HTTPS URL or matching `data:` URI) or `type=@file` (uploaded to Creator before text generation):

```bash
# Analyze a local video (uploaded first; task receives its CDN URL)
python3 scripts/artclaw.py generate-text \
  --prompt "Describe what happens in this video" \
  --model "Gemi 3.0 Flash" \
  --reference-parts "video=@/path/to/clip.mp4"

# Analyze an image by URL
python3 scripts/artclaw.py generate-text \
  --prompt "What is in this image?" \
  --model "Gemi 3.0 Flash" \
  --reference-parts "image=https://example.com/photo.png"
```

| Parameter              | Description                             | Values                                                                                                                                |
| ---------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `--prompt`             | Text prompt, required                   | Text                                                                                                                                  |
| `--model`              | Model ID                                | `deepseek-v4-pro` (default), `deepseek-v4-flash`, `Gemi 3.0 Flash`, `Gemi 3.1 Pro`, `Gemi 3.1 Flash Lite`, `Gemi 3.5 Flash`, `GT-5.5` |
| `--provider`           | LLM provider                            | `deepseek` (default)                                                                                                                  |
| `--system-instruction` | System prompt                           | Text                                                                                                                                  |
| `--response-format`    | Output format                           | `text` (default), `json_object`                                                                                                       |
| `--reference-parts`    | Multimodal reference (Gemi models only) | One or more `type=url` / `type=@file`, e.g. `video=@./clip.mp4`, `image=https://...`                                                  |

**Local references for `--reference-parts`.** `type=@file` uploads the local image, video or audio, then sends its CDN URL in the same typed slot. Existing HTTPS URLs and `data:<mime>;base64,...` inputs remain supported. Local upload limits are 30 MB for images and 50 MB for video/audio; a model may impose tighter limits. `--reference-parts` with a non-Gemi model errors out client-side.

### Voice STT / TTS (Async)

For voice cloning or choosing preset-library/Volcengine official voices, use `voice-clone` and read [Voice selection and cloning](references/voice-clone.md). Its default is Seed Audio, with IndexTTS available on request. The separate `tts` command below retains its existing speaker defaults.

Voice endpoints are **asynchronous**. Submit with audio/text → receive `job_id` → poll for result.

```bash
# Speech-to-Text
python3 scripts/artclaw.py stt --audio "data:audio/wav;base64,..."
python3 scripts/artclaw.py stt --audio-file ./recording.wav

# Text-to-Speech
python3 scripts/artclaw.py tts --text "Hello, welcome to VICOO."
python3 scripts/artclaw.py tts --text "你好" --speaker zh_female_shuangkuaishu_moon_bigtts
```

| Parameter      | Description                            | Values                                                                       |
| -------------- | -------------------------------------- | ---------------------------------------------------------------------------- |
| `--audio`      | Audio input (STT)                      | Public URL, `@local-file`, `data:audio/wav;base64,...` or raw base64         |
| `--audio-file` | Local audio file, uploaded first (STT) | Path                                                                         |
| `--text`       | Text to synthesize (TTS), required     | Text                                                                         |
| `--speaker`    | Speaker ID (TTS)                       | `en_male_tim_uranus_bigtts` (default), `zh_female_shuangkuaishu_moon_bigtts` |

Voice jobs use polling profile: interval 3s, timeout 120s.

### Execute Workflow

If `--inputs` contains an explicit `@path` to a supported local image, video or audio file (including inside a nested reference list), the CLI uploads it before workflow execution and substitutes the CDN URL. Other input values are unchanged. In spawn mode the child process must have access to the same local path; dry-run does not upload. The same rule applies to `run-workflow-dsl` inputs; DSL source files themselves are read as configuration, not uploaded.

```bash
python3 scripts/artclaw.py list-workflows
```

```bash
python3 scripts/artclaw.py run-workflow \
  --workflow-id "text-to-image-basic" \
  --inputs '{"prompt": "Anime-style forest"}' \
  --team-id 8f8bacf4-e130-4221-8bab-58cf14c755fb \
  --no-wait
```

Replace `--no-wait` with `--spawn`, `--deliver-to`, and `--deliver-channel` only when the current platform adapter says to do so.
For personal billing, omit `--team-id` and leave `ARTCLAW_TEAM_ID` and persisted `teamId` unset. The selected team ID is sent as a top-level request field, not as a workflow input. Both workflow commands and `spawn-task` use the same team selection priority as direct generation.

### Execute Workflow from DSL

For a workflow the caller defines as DSL YAML (not a preset), use `run-workflow-dsl`. The DSL is compiled server-side, so no local compiler is needed:

```bash
python3 scripts/artclaw.py run-workflow-dsl \
  --dsl-file ./my-workflow.yaml \
  --inputs '{"prompt": "a cat"}' \
  --team-id 8f8bacf4-e130-4221-8bab-58cf14c755fb \
  --no-wait
```

Or pass the DSL inline with `--dsl`. The DSL schema follows `apiVersion: artclaw/v1` with `inputs` / `outputs` / `pipeline` (generation nodes `gen.image`, `gen.text`, `gen.video`, `gen.music`, `gen.voiceClone`, plus `logic.forEach` and data/logic nodes). A compile error returns HTTP 400 with an error code (`E_UNKNOWN_NODE`, `E_TYPE_MISMATCH`, …) — fix the DSL and retry.

**Before authoring DSL, read the reference docs under [`references/workflow-dsl/`](references/workflow-dsl/):**

- [`dsl-syntax.md`](references/workflow-dsl/dsl-syntax.md) — top-level schema, reference syntax (`$inputs` / `$steps` / `$item`), type-compatibility matrix, compiler sugar, `logic.forEach` / `logic.if` control flow, static-structure rule
- [`node-catalog.md`](references/workflow-dsl/node-catalog.md) — every node with its config fields and ports (all `gen.*` generation nodes, `data.*`, `logic.*`, `primitive.*`) + a selection guide
- [`error-codes.md`](references/workflow-dsl/error-codes.md) — every compile error code with its fix, and common traps
- [`sensible-defaults.md`](references/workflow-dsl/sensible-defaults.md) — default models and config; fill these without asking the user
- [`examples.md`](references/workflow-dsl/examples.md) — complete, compile-verified workflows

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

| Error                           | Cause                                                              | Resolution                                                                           |
| ------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `401 Unauthorized`              | API key invalid, missing, or revoked                               | Guide user to regenerate the key                                                     |
| `402` / insufficient credits    | Account balance depleted                                           | Guide user to top up at https://artclaw.com/settings                                 |
| `403 not a member of this team` | `team_id` does not belong to the user, or the user is not a member | Ask the user to verify their team UUID; omit `--team-id` to bill the account instead |
| `404 Voice job not found`       | Job ID does not exist or expired                                   | Voice jobs have shorter TTL; re-submit                                               |
| `404 Job not found`             | Job ID does not exist or expired after 24h                         | Tell user the job expired and ask whether to regenerate                              |
| `404 Workflow not found`        | Workflow does not exist                                            | Run `list-workflows` first                                                           |
| `429 Too Many Requests`         | Rate limit exceeded                                                | Wait and retry                                                                       |

---

## Delivery Targets

Use delivery options only when the platform adapter supports spawn/delivery mode.

`--spawn` must be paired with both `--deliver-to` and `--deliver-channel`.

| Scenario              | `--deliver-channel` | `--deliver-to` value | Source                                                  |
| --------------------- | ------------------- | -------------------- | ------------------------------------------------------- |
| Feishu group chat     | `feishu`            | `oc_xxx` chat ID     | `conversation_label` or `chat_id`, strip `chat:` prefix |
| Feishu direct message | `feishu`            | `ou_xxx` open ID     | `sender_id`, strip `user:` prefix                       |
| Telegram              | `telegram`          | `chat_id`            | Inbound message context                                 |
| Discord               | `discord`           | `channel_id`         | Inbound message context                                 |

For Feishu, check `is_group_chat` in inbound metadata: `true` → use `oc_` chat ID; `false` → use `ou_` open ID.

| Channel    | Credential source                                             |
| ---------- | ------------------------------------------------------------- |
| `feishu`   | `~/.openclaw/openclaw.json` → `channels.feishu.accounts.main` |
| `telegram` | `TELEGRAM_BOT_TOKEN` environment variable                     |
| `discord`  | Framework built-in message tool                               |

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

After updating, reload this Skill in your agent or start a new session that reads the updated instructions. Version 1.8.0 adds read-only voice discovery, progressive voice selection and Seed Audio speaker inputs. Deploy the Proxy `/direct/voice-clone/validate` adapter first, then the matching `/voices` REST API and downstream speaker support before releasing this Skill; audio preview playback is not included.
