# Sensible Defaults

Fill these automatically; do not ask the user for every field. Node ports and config live in [`node-catalog.md`](node-catalog.md).

| Purpose | Default model |
|---|---|
| Text (reasoning / prompt optimization) | `gemini-3.1-pro-preview` |
| Text (fast / simple classification) | `gemini-3-flash-preview` |
| Image (main) | `gemini-3.1-flash-image-preview` |
| Image (high quality) | `gemini-3-pro-image-preview` |
| Video | `doubao-seedance-2-0-260128` |
| Music | `V4_5ALL` (`V5` / `V5_5` for higher quality) |
| Voice clone | fixed model — no `model` field |

```yaml
# gen.image
config: { model: gemini-3.1-flash-image-preview, aspectRatio: "1:1", resolution: "2K" }
# gen.text  (systemPrompt must be non-empty)
config: { model: gemini-3.1-pro-preview }
# gen.video
config: { refMode: universalRef, model: doubao-seedance-2-0-260128, duration: 5, resolution: "720p", aspectRatio: "9:16" }
# gen.music
config: { model: V4_5ALL, lyricsMode: input }   # instrumental: true for no vocals
# gen.voiceClone
config: { emotionMode: auto, emoAlpha: 1.0 }
# logic.forEach
config: { executionMode: parallel, maxConcurrency: 5 }
```

`systemPrompt` starter templates:
- **Prompt optimization**: "You are an AI-image prompt engineer. Expand the user input into a 100–200 word English prompt covering subject, lighting, composition, style. Output the prompt only, no explanation."
- **Structured output**: "Output a single JSON object with fields { … }. Output only the JSON code block."

**aspectRatio by scenario:** default / character bust `1:1` · YouTube / landscape cover `16:9` · TikTok / vertical `9:16` · three-view / scene `4:3` · half-body portrait `2:3`.

If the user gives no `title`, use `metadata.name`. If no `description`, leave it empty — do not invent an LLM-style description that contradicts the actual behavior.
