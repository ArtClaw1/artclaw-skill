# Workflow DSL Syntax

How to write a workflow DSL that `run-workflow-dsl` can compile and execute. The DSL is a YAML document describing a **static** node graph; ARTCLAW compiles it server-side, then runs it.

Submit with:

```bash
python3 scripts/artclaw.py run-workflow-dsl --dsl-file ./my-workflow.yaml --inputs '{"...": "..."}' --no-wait
```

A compile failure returns HTTP 400 with an error code — see [`error-codes.md`](error-codes.md). Fix the DSL and resubmit; do **not** resubmit an unchanged file. For node types and their ports, see [`node-catalog.md`](node-catalog.md); for default models and config, see [`sensible-defaults.md`](sensible-defaults.md); for full workflows, see [`examples.md`](examples.md).

---

## Top-level schema

```yaml
apiVersion: artclaw/v1        # required — only v1 is supported
kind: Workflow                # required
metadata:                     # required
  name: my_workflow_slug      # required, snake_case
  title: Human-readable title # optional
  description: One-liner       # optional
  tags: [text2image, ...]      # optional

inputs:                       # may be empty; these are the runtime `--inputs`
  - name: <slot_name>
    type: text|image|video|audio|number|bool|array<T>
    required: true|false
    description: What the user provides
    default: <optional default>

outputs:                      # at least one required
  - name: <output_name>
    type: text|image|video|...
    from: $steps.<id>.<port>  # MUST reference a step's output port

pipeline:                     # at least one step required
  - id: <step_id>             # unique within the workflow
    use: <node_type>          # e.g. gen.image, gen.text (see node-catalog.md)
    config: { ... }           # node config fields
    in:                       # input-port bindings
      <port>: <value>
    port_types: { ... }       # optional — declare a dynamic port's type
    group: "<label>"          # optional — visual grouping only, no runtime effect
    body: [ ... ]             # only for container nodes (logic.forEach)
    yields: $body.<id>.<port> # only for logic.forEach — body output that flows back out
```

The runtime `--inputs '{...}'` map fills the declared `inputs` **by value only**; it never changes the graph structure.

---

## Reference syntax

| Form | Meaning |
|---|---|
| `$inputs.<name>` | A declared workflow input |
| `$steps.<id>.<port>` | A named output port of a step (e.g. `.text`, `.url`, `.array`) |
| `$steps.<id>.output` | Alias for a **single-output** node's port (forbidden on multi-output nodes) |
| `${expr}` | **String interpolation**, only valid inside a string-typed config/input |
| `$item` / `$index` | Current element / index — only inside a `logic.forEach` body |
| `$body.<id>.<port>` | Only inside a forEach `yields:`, referencing a body step's output |

`$path` (structural, strongly typed) and `${expr}` (string concatenation, weakly typed) **cannot be mixed in the same value**.

`outputs[].from` must be `$steps.<id>.<port>` — a literal or `$inputs.*` is rejected.

---

## Type system

```
text | image | video | audio | number | bool
array<T>      # homogeneous array; T is any scalar type
any           # only appears on port dataTypes; avoid writing it in DSL
```

### Compatibility matrix (source → sink)

| src ↓ / dst → | text | image | video | number | array | any |
|---|---|---|---|---|---|---|
| text | ✅ | ❌ | ❌ | ⚠️ | ❌ | ✅ |
| image | ⚠️* | ✅ | ❌ | ❌ | ❌ | ✅ |
| number | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ |
| array | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| any | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

- ⚠️* `image`/`video`/`audio` → `text` is allowed **only** when the sink port id contains `prompt` or `url` (URL soft-compat, used for multimodal reference injection).
- ⚠️ `number` → `text` soft-compat; `json` → `array`/`struct` soft-compat (e.g. a `data.json_parse` result fed into `logic.forEach.array`).

When a type mismatch (`E_TYPE_MISMATCH`) blocks you, declare the input with the matching type, or route the value through a step that produces the target type (e.g. feed text/URLs into a `gen.*` multiConnect `prompt`).

---

## Compiler sugar (write less)

### Literal materialization
A literal in `in:` auto-creates a `primitive.*` node:

```yaml
in:
  prompt: "hello world"            # → primitive.text
  reference: "https://x.com/a.jpg" # → primitive.image (recognized by URL extension)
  count: 5                         # → primitive.number
```

A non-URL string (e.g. `/local/path.png`) is treated as **text**, not image. For an image, use an `https://` URL or a declared `type: image` input.

### `outputs` auto `set_variable`
Every `outputs[].from` auto-injects a `data.set_variable` node + edge. You never write it by hand.

### Array aggregation (sink is `array<T>`)
```yaml
in:
  images:
    - $inputs.a
    - $inputs.b
    - $steps.gen.url
# auto-injects data.array_create; elementType inferred from the sink
```

### Multi-source fan-in (sink is a `multiConnect` port)
```yaml
- id: gen
  use: gen.image
  in:
    prompt:                          # gen.image.prompt is multiConnect=true
      - $inputs.text_prompt
      - $inputs.reference_image      # auto-treated as a multimodal reference image
      - $steps.style_optimizer.text
```

---

## `logic.forEach` in full

```yaml
inputs:
  - { name: prompts, type: array<text>, required: true }
  - { name: style_ref, type: image, required: true }
outputs:
  - { name: results, type: array, from: $steps.batch.results }
pipeline:
  - id: batch
    use: logic.forEach
    config:
      executionMode: parallel
      maxConcurrency: 5
    in:
      array: $inputs.prompts
    body:
      - id: render
        use: gen.image
        in:
          prompt:
            - $item
            - $inputs.style_ref
    yields: $body.render.url
```

---

## `logic.if` conditional branching

Pattern: `data.object_get → logic.if → (branch A / branch B) → logic.first_value`.

```yaml
pipeline:
  - id: batch
    use: logic.forEach
    config: { executionMode: parallel }
    in: { array: $inputs.actors }
    body:
      # 1. extract a boolean field from $item
      - id: get_flag
        use: data.object_get
        config: { path: "update_flag" }
        in: { object: $item }        # any → json soft-compat

      # 2. route (flat node, not a container)
      - id: branch
        use: logic.if
        in: { condition: $steps.get_flag.value }

      # 3a. true branch: generate an image
      - id: gen_portrait
        use: gen.image
        in:
          prompt:
            - $steps.branch.true     # controlflow signal (empty {}), establishes the dependency
            - $item                  # real data
            - $inputs.artstyle

      # 3b. false branch: passthrough text
      - id: passthrough
        use: gen.text
        in:
          prompt:
            - $steps.branch.false    # controlflow signal
            - $item

      # 4. merge: the skipped branch outputs empty; first_value picks the one with a value
      - id: merge
        use: logic.first_value
        in:
          input_0: $steps.gen_portrait.url
          input_1: $steps.passthrough.text

    yields: $body.merge.result
```

**Wiring the controlflow signal:** `logic.if.true` / `logic.if.false` are always empty `{}`; add them into a downstream `multiConnect` port's list (e.g. `gen.image.prompt`) so the compiler emits the correct edge dependency and the executor can track which branch fired.

---

## The static-structure rule (most important)

The DSL describes a graph that is **fully determined at author time**. Runtime `inputs` fill values only — they never add, remove, or rewire nodes.

- ❌ "tell me how many images at call time" → ✅ `array<text>` input + `forEach`
- ✅ "take a branch at runtime by condition" → `logic.if` + `logic.first_value`
- ❌ "add / delete nodes at runtime" → not supported at all

### Explicit `port_types` (rarely needed)
When a dynamic port's type can't be inferred (e.g. a `string_format` port fed by an `any`-typed upstream):

```yaml
- id: fmt
  use: data.string_format
  port_types: { ref: image }     # tell the compiler this dynamic port is image
  in:
    template: "see ${ref}"       # ${name} placeholder; name = the port key below
    ref: $inputs.any_typed_thing
```

---

## Out of scope (v1 does not support)

- `logic.switch` — multi-way branching (use multiple `logic.if` for >2 paths)
- `logic.sub_workflow` — nested sub-workflows
- `structured.decompose` — structured decomposition (L4 complexity)
- Runtime node creation / deletion

For these, either express the case with the supported nodes in [`node-catalog.md`](node-catalog.md), or fall back to drawing the workflow by hand in the web editor.
