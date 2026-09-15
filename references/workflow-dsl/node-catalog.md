# Node Catalog

All node types available in the DSL, with their config fields and ports. For syntax and reference forms see [`dsl-syntax.md`](dsl-syntax.md); for default models see [`sensible-defaults.md`](sensible-defaults.md).

Model IDs below are **workflow-engine** model IDs used in a node's `config.model`. They are a separate namespace from the top-level `generate-image` / `generate-video` model catalog in `SKILL.md` — do not substitute those IDs here.

---

## Generation — `gen.*`

**`gen.image`** — image generation
```yaml
config:
  model: gemini-3.1-flash-image-preview  # main; gemini-3-pro-image-preview for higher quality
  aspectRatio: "1:1"                      # 1:1 | 16:9 | 9:16 | 4:3 | 3:4 | 2:3
  resolution: "2K"                        # 512 | 1K | 2K | 4K
in:
  prompt: <multiConnect, ordered>         # text prompt(s) + image URL(s) as multimodal refs
# out: url (image)
```

**`gen.text`** — LLM text generation
```yaml
config:
  model: gemini-3.1-pro-preview           # reasoning; gemini-3-flash-preview for speed
in:
  prompt: <multiConnect>                  # user prompt + multimodal refs
  systemPrompt: text                      # role / format constraints (must be non-empty)
# out: text (text); struct_out (struct) and struct_array (array<struct>) when structureId is set
```

**`gen.video`** — video generation
```yaml
config:
  refMode: universalRef | firstEndFrame
  model: doubao-seedance-2-0-260128
  duration: 5                             # 2–15 s (may also be a port)
  resolution: "720p"                      # 720p | 1080p
  aspectRatio: "16:9"                     # (may also be a port)
in:
  prompt: <multiConnect>
# out: url (video)
```

**`gen.music`** — music / BGM generation
```yaml
config:
  model: V4_5ALL                          # V4_5ALL (default) | V5 | V5_5
  lyricsMode: input                       # input | generate | instrumental
  instrumental: false                     # true → no vocals
  style: "Cinematic Classical"            # optional genre hint
  vocalGender: ""                         # "" | m | f
  title: ""                               # optional track name
  # advanced (customMode: true): styleWeight, weirdnessConstraint, audioWeight (0–1)
  # duration: 10–360 s — V5_5 pro mode only; omit to let the model decide
in:
  prompt: <multiConnect, ordered>         # style prompt; also the generation prompt in simple mode
  referenceAudio: <audio, multiConnect>   # optional, up to 2 ordered reference tracks
# out: url (audio), urls (array<audio> — all candidates)
```

**`gen.voiceClone`** — AI voice cloning (synthesize text in a reference voice)
```yaml
# no `model` config — the voice model is fixed
config:
  emotionMode: auto                       # auto | text | vector | audio
  emoAlpha: 1.0                           # emotion weight 0.1–2.0
in:
  text: <text, required, multiConnect>    # text to synthesize
  refAudio: <audio, required>             # timbre reference (defines the voice)
  emoText: text                           # optional — emotion text (emotionMode: text)
  emoAudio: audio                         # optional — emotion reference (emotionMode: audio)
  emoAlpha: number                        # optional — overrides the config slider
# out: url (audio)
```

---

## Primitives — `primitive.*`
Usually **not** hand-written (literal materialization does it). Write one only as a named input to a container node.

| Node | Purpose |
|---|---|
| `primitive.text` | Text constant (outputs an array when `batchMode: true`) |
| `primitive.image` | Image URL constant |
| `primitive.video` | Video URL constant |
| `primitive.number` | Number constant |

Config: `value` (text/number), `url` (image/video/audio), `batchMode`, `_boundVariable` (auto-managed).

---

## Data shaping — `data.*`

**`data.string_format`** — template concatenation
```yaml
in:
  template: "${subject}, ${lighting}, 4k"   # ${name} placeholders, matched by port name
  subject: $inputs.subject                  # each extra `in:` key becomes a dynamic port;
  lighting: $inputs.lighting                # its key is the ${name} the template references
# out: result (text)
```
Placeholders use `${name}` where `name` is the port key you add under `in:` (not positional `{0}`/`{1}`). Any `in:` key other than `template` becomes a dynamic input port named by that key.

**`data.array_create`** — array aggregation (usually auto-injected)
```yaml
config: { elementType: image | text | video | ... }
in: { item_0, item_1, item_2: any }   # static 3; more via dynamic ports
# out: array (array)
```

**`data.set_variable`** — write a blackboard output (auto-injected by `outputs`)
```yaml
config: { variableId: <output var id> }
in: { value: any }
```

**`data.json_parse`** — parse a JSON string
```yaml
in: { text: <json string> }   # input port is `text` (not `value`)
# out: result (json) — soft-compat to array/struct (feed a forEach); struct_out (struct) when a structure is bound
```

**`data.object_get`** — extract a field by dot path
```yaml
config: { path: "a.b.c" }
in: { object: json }      # accepts any (soft-compat); $item works directly
# out: value (any)
```

**`data.json_path_get`** — JSONPath query with array wildcards
```yaml
config: { path: "arr[].name" }   # supports arr[0] / arr[].field
in: { object: json }
# out: value (any)
```

---

## Control flow — `logic.*`

**`logic.forEach`** — array iteration (the primary control-flow node)
```yaml
- id: batch
  use: logic.forEach
  config:
    executionMode: parallel      # parallel | sequential
    maxConcurrency: 5            # parallel only
  in:
    array: $inputs.prompts       # array to iterate (json soft-compat)
  body:
    - id: render
      use: gen.image
      in:
        prompt:
          - $item                # current element
          - $inputs.style_ref    # outer scope is visible inside body
  yields: $body.render.url       # body step output that flows back to forEach.results
# out: results (array)
```
Full worked example in [`dsl-syntax.md`](dsl-syntax.md#logicforeach-in-full).

**`logic.if`** — conditional branch (a flat control-flow router, **not** a container)
```yaml
in:
  condition: boolean       # boolean; a non-empty string also counts as true
# out: true (any), false (any) — the active branch fires; both port values are empty {}
```
The activated branch's downstream runs; the other branch's downstream is marked skipped (its output is empty `{}`). The `true`/`false` ports carry only a routing signal — real data reaches the downstream node from its other inputs. Full branching pattern in [`dsl-syntax.md`](dsl-syntax.md#logicif-conditional-branching).

**`logic.first_value`** — pick the first non-empty value (merge two branches)
```yaml
in: { input_0, input_1, ...: any }   # dynamic ports
# out: result (any) — the first non-empty string / non-empty array
```

---

## Selection guide

| User says | Use |
|---|---|
| "generate one image" | `gen.image` |
| "have AI write some text" | `gen.text` |
| "generate music / BGM" | `gen.music` |
| "clone a voice / synthesize speech in a voice" | `gen.voiceClone` |
| "fuse two inputs into one prompt" | `gen.text` with a multiConnect prompt, or `data.string_format` (`${name}` template) |
| "batch-generate N images" | `array<T>` input + `logic.forEach` wrapping `gen.image` |
| "split fields from JSON" | `data.json_parse` + `forEach` over the array |
| "merge multiple images" | list into an `array<T>` sink (auto `array_create`) |
| "style transfer from a reference image" | `gen.image.prompt` receiving text + a reference image URL |
| "branch on a condition at runtime" | `logic.if` + `logic.first_value` |
