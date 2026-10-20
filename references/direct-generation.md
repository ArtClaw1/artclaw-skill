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

GT-Image-2.5 and GT-Image-2.5-Flash accept `low/medium/high/xhigh/max` quality. REST defaults to `high` when omitted; Creator's UI may choose a different default.

Legacy image switches `--n`, `--style` and `--negative-prompt` are currently ignored by the REST request schema. They do not provide batch generation or independent style/negative-prompt controls. Put stylistic instructions in the prompt; use separate submissions or an explicitly requested workflow for batching.

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

Changing `--provider` alone does not enable video-to-audio, sound effects, voice conversion or stem separation. Those require additional public API contracts.

## Asynchronous adapters

The new image/video options work with `generate-image --spawn` and `generate-video --spawn`. File references are normalized to absolute paths before spawning. Generated commands use POSIX shell quoting; follow the platform adapter when executing them. The standalone `spawn-task` helper retains its smaller argument set; use generation commands with `--spawn` for these advanced options.

Large typed-reference payloads are saved under `~/.artclaw/spawn-inputs/` and passed to the child with `--content-items @<absolute-file>`, avoiding command-line size limits. These files contain the reference data and remain available for delayed execution/retries; remove them once the associated work is finished.

`generate-seedance-video` has no `--spawn`; follow the adapter's asynchronous command mechanism or use `--no-wait` and job polling. Existing team selection and authentication rules are unchanged.
