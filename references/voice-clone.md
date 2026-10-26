# Voice selection and cloning

Use `voice-clone --input <JSON-or-@file>`. Recommend **Seed Audio** when no model was chosen; keep an explicit **IndexTTS** choice. This guide covers voice discovery and selection. Audio preview playback is deferred: do not auto-generate a paid sample or promise a CLI audio player.

## Choose without overwhelming the user

- If the user named a voice, query it to resolve its real ID and compatibility. If they supplied an audio reference and asked to clone it, use it directly. Do not repeat the voice-choice question.
- Otherwise, before submitting, ask briefly whether they want a specific voice, their own reference, or a recommendation. For example: “要指定音色吗？可以用预设音色库、火山官方音色（仅 Seed Audio），也可以提供参考音频，或让我按文案推荐。”
- Infer target language from the speech text or explicit request, not the conversation language. Use supplied style, gender and scene; only ask for a missing preference if it materially changes the choice.
- Query the catalog and normally present **3** suitable choices, at most **5** when browsing. Each line needs a name, source and one short distinguishing characteristic. Keep technical IDs and URLs in working context. Respond in the user's language; preserve original voice names.
- Keep the current displayed-number → stable-ID mapping, filters, `catalog_version` and `next_offset`. “选 2” refers only to the latest displayed list. “换一批” reuses filters and `next_offset`, passing `--catalog-version`; do not repeat the same first page. When there is no next page, say so and suggest relaxing a filter.
- “只看预设音色库” selects `--source official`; “只看火山官方音色” selects `--source volcengine`. If “官方” alone is ambiguous, clarify which library the user means. Changing the source changes `--source`; a style refinement changes `--q` or `--scene`. Restart at offset zero after changing filters and replace the old numbered list. Search terms match source metadata (often Chinese), so translate descriptive search terms where useful; this is not semantic search. Do not silently discard a user's hard constraint just to get results.
- If the CLI reports a changed catalog, discard that page and old numbers, query offset zero without the old version and show the refreshed choices. Before submitting a selected catalog voice, resolve its stable `--id` with the same model/source; if missing or changed, explain and offer a new choice.
- “你帮我选” authorizes choosing the best matching available voice. Briefly state the choice and proceed with the already requested task, without asking for another approval. It does not authorize changing an explicitly selected model.
- If IndexTTS was chosen and the user wants a Volcengine voice, explain that it requires Seed Audio and ask whether to switch model or use preset/reference audio. Never switch silently.
- No preference and no reference is not a valid submission. Offer the shortlist or delegated selection. A shortlist is a set of alternatives, not permission to mix all voices into one generation request.

## Read-only discovery

After the usual API key preflight:

```bash
python3 scripts/artclaw.py list-voices --engine seed-audio --language zh --gender female --limit 3
python3 scripts/artclaw.py list-voices --source official --engine indextts --q 温柔 --limit 3
python3 scripts/artclaw.py list-voices --source volcengine --language en --limit 5
```

`GET /voices` requires API Key authentication, consumes the query rate limit and creates no task or charge. Filters: `source` (`all`, `official`, `volcengine`), `engine` (`seed-audio`, `indextts`), `language`, `gender`, `scene`, `q`, exact `id`, `offset` and `limit` (1–5, default 3). CLI `--catalog-version` compares the returned version locally and is not sent as an API parameter. The route has no trailing slash.

The JSON response includes `items`, filtered `total`, `next_offset` (null at the end) and `catalog_version`. Items include stable source-prefixed `id`, `name`, `source`, `gender`, `languages`, `style`, `description`, `tags`, `scene`, `featured`, `engines` and `selection`.

The catalogs are maintained by Creator, not bundled into this Skill. Do not invent IDs or hard-code their counts. Preset library voices (`source=official`, user-facing “预设音色库”) are reference audio usable by either model; Volcengine official voices (`source=volcengine`, user-facing “火山官方音色”) are speaker IDs usable only by Seed Audio. Empty `languages` means **not labelled**, not verified support for every language. A language filter retains these unlabelled entries after known matches. Explain this uncertainty when recommending one for a specific language. `es`/`mx` identify Spanish; regional codes such as `en-US` normalize to their language prefix.

Exact selection and next-page templates (replace placeholders with returned values):

```bash
python3 scripts/artclaw.py list-voices --engine seed-audio --source '<RETURNED_SOURCE>' --id '<RETURNED_ID>'
python3 scripts/artclaw.py list-voices --engine seed-audio --language zh --gender female --offset <NEXT_OFFSET> --catalog-version '<PREVIOUS_VERSION>' --limit 3
```

Empty results: explain and offer another filter/source. 503: directory unavailable; retry discovery later or use an explicitly chosen available source/reference. 404: the catalog API may not be deployed; explain the deployment mismatch, do not invent fallback voices. Auth failures use normal key guidance. Never issue a generation request to test whether a voice exists.

## Build the existing generation payload

Merge the chosen item's `selection` into `{ "engine": "…", "text": "…" }`:

| Selection                 | REST/CLI fields                                         | Supported models                       |
| ------------------------- | ------------------------------------------------------- | -------------------------------------- |
| Preset voice library      | `reference_audio_urls: [<actual catalog audio URL>]`    | Seed Audio, IndexTTS                   |
| Volcengine official voice | `speaker: <actual catalog speaker>`                     | Seed Audio only                        |
| User reference            | `reference_audio_urls: [<HTTPS URL or @local-file>, …]` | Seed Audio up to 3; IndexTTS exactly 1 |

Seed Audio supports one scalar `speaker`. Count **speaker + audio references ≤ 3**, with at least one reference: speaker alone, speaker + 1–2 audios, or 1–3 audios. Only mix references when the user requests it. IndexTTS accepts exactly one audio and rejects `speaker`.

The downstream provider builds `references`; each object contains exactly one of `speaker`, `audio_url`, `audio_data`. Do not send raw provider `references`, `audio_url`, `audio_data`, catalog `id`, `source` or `selection` wrappers to the public voice-clone API. Do not treat a Volcengine preview recording as its cloning reference.

Seed Audio text is trimmed and must fit **3000 Unicode characters after assembly**. Plain text without `@音频` is wrapped downstream as `@音频1 说：“...”` (9 extra characters); count this wrapper but do not add it to the request. Numeric `@音频N` mentions must point to existing references: speaker is slot 1 when present, followed by audio URLs in their supplied order. After changing references, update any mentions with the user’s intended assignment; never silently move a line to another speaker.

Each Seed reference audio must be **≤30 seconds and ≤10 MiB**. The CLI rejects oversized local files before upload/spawn. REST reuses Creator media probes after upload and before generation for duration and remote size. Unreadable metadata remains subject to upstream validation. A failed/unavailable validation service returns 503 without submitting; fix deployment or retry validation, never bypass it. IndexTTS does not inherit these Seed-specific limits.

Seed Audio controls: `speech_rate`, `loudness_rate` (-50..100), `pitch_rate` (-12..12), `output_format` (wav/mp3/pcm/ogg_opus), `sample_rate` (8000/16000/24000/32000/48000; ogg_opus requires 48000). IndexTTS controls: `emo_alpha` (0.1..2), `use_random`, `use_emo_text`, `emo_text`, `emo_vector` (8 comma-separated values in 0..1), `emo_audio_url`. Do not mix model-specific controls.

Submit using the current adapter's async mode. For non-spawn adapters:

```bash
python3 scripts/artclaw.py voice-clone --input @voice-request.json --no-wait
```

The CLI checks model/reference constraints before upload or spawn. Keep the returned job ID and use existing status/history handling; never automatically resubmit after an ambiguous timeout or a known job ID. Use `--dry-run` to inspect a request without submitting or uploading.
