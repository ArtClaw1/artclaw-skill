# Direct generation controls

These options submit REST generation jobs directly. Apply the selected platform adapter's asynchronous strategy; the examples use `--no-wait`, then `job-status --job-id …`. Do not resubmit a job merely because polling timed out.

## Image

| Option                                         | Meaning                                                                                                              |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `--aspect-ratio`                               | Positive `W:H`, or model-supported `auto` / `adaptive`. The service validates each model's supported ratios.         |
| `--resolution`                                 | `512`, `1K`, `2K`, `3K`, `4K`; model-dependent.                                                                      |
| `--online-search` / `--no-online-search`       | Explicit search enhancement setting; supported Navo/Gemini and Seedream models only. Omit to retain the API default. |
| `--moderation auto\|low`                       | GT-Image moderation mode.                                                                                            |
| `--sequential-image-generation auto\|disabled` | Seedream sequential generation strategy; not a requested image count.                                                |
| `--seed`                                       | Random seed; supported models only.                                                                                  |
| `--guidance-scale`                             | Seedream guidance weight.                                                                                            |
| `--watermark`                                  | Enable the provider's visible watermark where supported.                                                             |
| `--background auto\|opaque\|transparent`       | GT-Image background. Transparent output needs PNG or WebP.                                                           |

Resolution examples: Navo Bana 2.5 Flash Image uses `1K`; Navo Bana 3.0 Pro Image supports `1K/2K/4K`; Navo Bana 3.1 Flash Image adds `512`. Seedream 5.0 uses `2K/3K`, while Seedream 5.0 Pro uses `1K/2K`. Do not assume every model supports every CLI choice.

New direct models: `Navo Bana 3.1 Flash Lite Image` uses `1K` and its 14 supported ratios; `Kling Image 3.0` uses `1K/2K`, `16:9|9:16|1:1`, at most one reference image; `Kling Image 3.0 Omni` uses `1K/2K/4K`, the same three ratios and multiple references. Use their exact names with `generate-image --model`.

GT-Image-2.5 and GT-Image-2.5-Flash accept `low/medium/high/xhigh/max` quality. REST defaults to `high` when omitted; Creator's UI may choose a different default.

`--n` accepts 1, 2 or 4 outputs per image job, matching the Creator workflow count options. Legacy `--style` and `--negative-prompt` are still ignored by the REST request schema; put stylistic instructions in the prompt.

```bash
python3 scripts/artclaw.py generate-image \
  --prompt "An illustrated guide to this season's flowers" \
  --model doubao-seedream-5-0-260128 --resolution 3K \
  --aspect-ratio 3:4 --online-search \
  --sequential-image-generation disabled --no-wait
```

## Video references

`generate-video` and `generate-seedance-video` accept `--content-items` as a JSON array or `@JSON-file` (UTF-8, with or without BOM). Each item must contain exactly `role` and `url`. Array order is retained.

| Role              | Media |
| ----------------- | ----- |
| `first_frame`     | Image |
| `last_frame`      | Image |
| `reference_image` | Image |
| `reference_video` | Video |
| `reference_audio` | Audio |

For `generate-video`, images can use HTTP(S), matching image base64 data URIs or `@local-file`; video/audio references must use HTTP(S) URLs because this REST route cannot resolve inline video/audio. The dedicated `generate-seedance-video` contract also supports audio data URIs and local audio. Video references require hosted HTTP(S) URLs in both commands; inline video is not verified by the public contract. Local media are inlined, with a 10 MB limit per file. All relative media paths resolve against the command's working directory, including paths inside a JSON file. Supported local extensions: JPG/JPEG/PNG/GIF/WebP/BMP; additionally MP3/WAV/M4A/OGG for the dedicated Seedance command. Larger files need an accessible hosted URL.

The CLI validates the shape and media role; the service determines whether a model accepts the role, format, count and media duration. Prefer hosted URLs for video/audio references. Accepting a CLI option does not guarantee provider support.

Legacy `--reference-urls` and `--reference-files` continue to mean reference images. They cannot be combined with `--content-items`. Use typed items for first/last frames. Multiple reference images do not by themselves reproduce a workflow's multi-segment video generation and composition.

Example `refs.json`:

```json
[
  { "role": "first_frame", "url": "@./start.png" },
  { "role": "last_frame", "url": "https://example.com/end.png" }
]
```

```bash
python3 scripts/artclaw.py generate-video \
  --prompt "Move smoothly between the two scenes" \
  --model doubao-seedance-2-0-260128 \
  --content-items @refs.json --seed 42 --no-wait
```

The dedicated Seedance command maps these items to Ark `content` after the text prompt. It also accepts `--online-search` / `--no-online-search` and `--watermark` / `--no-watermark`.

## Video controls and editing

Generic `generate-video` adds `--seed`, `--online-search` / `--no-online-search`, and `--omni-reference-task-type auto|reference|edit|extend`. Search and task types remain model-dependent. Use the generic command for Seedance 2.5 editing/extension.

To edit a source video, save the following as `edit-refs.json`:

```json
[{ "role": "reference_video", "url": "https://example.com/source.mp4" }]
```

```bash
python3 scripts/artclaw.py generate-video \
  --model doubao-seedance-2-5-260628 \
  --prompt "Change the jacket to blue, preserving the action" \
  --content-items @edit-refs.json --omni-reference-task-type edit \
  --duration -1 --aspect-ratio adaptive --no-wait
```

This exposes the existing video editing API; it does not implement workflow segment selection, retake and stitching. `extend` likewise requests the provider task type, without the workflow node's surrounding composition.

Video resolutions include `480p/720p/1080p/4k`. `4k` is available for Seedance 2.0, not Seedance 2.5. Seedance 2.0 Fast/Mini use `480p/720p`; 2.5 supports up to `1080p`, with MP4/MOV output. Other model restrictions remain service-authoritative.

## Music

`generate-audio` supports `Suvo V4.5 ALL`, `Suvo V5` and `Suvo V5.5`. `--duration` is 10–360 seconds and only supported with Suvo V5.5 custom mode. Existing style, title, vocal gender, negative tags, weights and persona parameters remain available. A Suvo persona ID is not a general reference-audio voice-cloning endpoint.

```bash
python3 scripts/artclaw.py generate-audio \
  --provider suno --model "Suvo V5.5" --custom-mode \
  --prompt "Warm piano and light strings" --instrumental \
  --style "cinematic ambient" --title "Quiet morning" \
  --duration 60 --no-wait
```

For a Suno reference cover use `--operation upload_cover --reference-audio-urls https://example.com/ref.mp3`; for a mashup use `--operation mashup --reference-audio-urls https://example.com/first.mp3 https://example.com/second.mp3`. Order is preserved. `--operation generate` takes no references. For Kling video-to-audio use `--provider kling --video-url https://example.com/clip.mp4 --sound-effect-prompt 'footsteps' --bgm-prompt 'subtle ambient' --asmr-mode`; `--video-id` may replace `--video-url`. These commands return a job; the final job result can contain multiple tracks.

## Direct background removal, voice and postprocessing

Use the operation-specific CLI commands below with `--input @request.json` or an inline JSON object. The JSON keys are the typed REST fields; the server rejects unknown fields and invalid provider combinations. `--team-id` works on every command and overrides `ARTCLAW_TEAM_ID`, then persisted `teamId`. All these commands except `voice-convert` and `stem-separate` return `job_id`; use the selected platform adapter's background strategy or `--no-wait` and then `job-status`. Do not resubmit an existing job after a poll timeout.

- remove-bg-image: supply image_url for PhotoRoom or Aliyun, or image_urls for chroma key. Set matte_model to photoroom, chroma_key or aliyun_matting. PhotoRoom accepts output_size and fill_color. Chroma accepts key_mode, manual_key_hex, threshold and softness (both 0–255), despill_strength (0–1) and halo_pixels (0–20). Aliyun accepts background_type, bg_color, and paired target_width/target_height. The job returns an image.
- remove-bg-video: supply video_url plus the matching algorithm controls above. The job returns a video.
- voice-clone: supply engine (seed-audio or indextts), text, and reference_audio_urls. Seed Audio accepts 1–3 references plus speech_rate, loudness_rate, pitch_rate, output_format, and sample_rate (8000, 16000, 24000, 32000 or 48000 Hz; ogg_opus requires 48000). IndexTTS accepts one reference plus emo_alpha (0.1–2.0), use_random, use_emo_text, emo_text, emo_vector (8 comma-separated values), and emo_audio_url. The job returns audio.
- video-retalk: supply video_url and audio_url; optionally ref_image_url and video_extension. The job returns lip-synced video.
- image-enhance: supply image_url; optionally tool_version (standard, professional, or max), multiple, target_width and/or target_height, and generative_enhance_mode. Each dimension is 64–6144 for standard or 64–10240 otherwise. enable_correct_color is max-only. The job returns an image.
- smart-erase: supply video_url; optionally target_language and template_id. The job returns a cleaned video and available subtitle URLs.
- smart-subtitles: supply video_url; optionally template_id. The job returns a subtitle asset URL.
- ai-dubbing: supply video_url, original_subtitle_url, and translated_subtitle_url; optionally source_language, target_language, and template_id. The job returns a dubbed video and speaker metadata URL.
- video-enhance: supply video_url and enhancement_mode (sr, lm, or fi). For Tencent, choose matching sr_type, lm_type, or fps_num. For Huoshan, use sr or lm with resolution and fps; enhance_style applies only to sr. The job returns a video.
- voice-convert: supply model, spk_id, and audio_base64; optionally audio_extension, key, formant_shift_key, and pitch_extractor. Synchronous response: audio_data.
- stem-separate: supply model and audio_base64; optionally audio_extension, output_format, and use_tta. Synchronous response: vocals and other.

All hosted media URLs must be public HTTPS URLs. For the two synchronous commands, `audio_base64` may be `@./audio.wav`; the CLI reads and encodes a local file up to 10 MB. Synchronous GPU requests may take several minutes. If a network timeout or 5xx follows any direct submission, the outcome may be unknown: check credits/results before manually retrying. The CLI never automatically retries direct POSTs. Do not print or retain large Base64 audio results unnecessarily.

```bash
python3 scripts/artclaw.py remove-bg-image \
  --input '{"matte_model":"photoroom","image_url":"https://example.com/cat.png"}' \
  --team-id YOUR_TEAM_ID --no-wait

python3 scripts/artclaw.py voice-clone \
  --input '{"engine":"seed-audio","text":"Hello","reference_audio_urls":["https://example.com/voice.mp3"]}' \
  --no-wait

python3 scripts/artclaw.py voice-convert \
  --input '{"model":"YOUR_ACTIVE_DDSP_MODEL","spk_id":"YOUR_SPEAKER_ID","audio_base64":"@./source.wav"}'
```

## Asynchronous adapters

The new image/video options work with `generate-image --spawn` and `generate-video --spawn`. File references are normalized to absolute paths before spawning. Generated commands use POSIX shell quoting; follow the platform adapter when executing them. The standalone `spawn-task` helper retains its smaller argument set; use generation commands with `--spawn` for these advanced options.

Large typed-reference payloads are saved under `~/.artclaw/spawn-inputs/` and passed to the child with `--content-items @<absolute-file>`, avoiding command-line size limits. These files contain the reference data and remain available for delayed execution/retries; remove them once the associated work is finished.

`generate-seedance-video` has no `--spawn`; follow the adapter's asynchronous command mechanism or use `--no-wait` and job polling. Existing team selection and authentication rules are unchanged.
