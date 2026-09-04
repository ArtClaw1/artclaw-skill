# Compile Error Codes → Fixes

A compile failure returns HTTP 400 with one of these codes. Fix the DSL and resubmit. See [`dsl-syntax.md`](dsl-syntax.md) for syntax and [`node-catalog.md`](node-catalog.md) for node ports.

| Code | Trigger | Fix |
|---|---|---|
| `E_YAML_SYNTAX` | YAML syntax error (indent / quoting / unescaped quote) | Check the reported line; validate locally with `python3 -c "import yaml; yaml.safe_load(open('x.yaml'))"` |
| `E_SCHEMA` | Missing top-level field (`apiVersion` / `kind` / `metadata.name` …) | Fill from the top-level schema in `dsl-syntax.md` |
| `E_VERSION_UNSUPPORTED` | `apiVersion` is not `artclaw/v1` | Set `apiVersion: artclaw/v1` |
| `E_UNKNOWN_NODE` | `step.use` is an unregistered node type | Use a node from `node-catalog.md` |
| `E_DUP_STEP_ID` | Two steps share an `id` | Rename (e.g. `<function>_<n>`) |
| `E_REF_SYNTAX` | Malformed `$…` reference | Use `$inputs.<name>` / `$steps.<id>.<port>` / `${var}`, kept strictly separate |
| `E_REF_NOT_FOUND` | Reference to a missing input / step | Check the `step.id` or `input.name` spelling |
| `E_PORT_NOT_FOUND` | Node has no such port | Check the port list in `node-catalog.md`, or declare it via `port_types` |
| `E_PORT_AMBIGUOUS` | `.output` alias on a multi-output node | Write the explicit port (`.text` / `.url` / `.array`) |
| `E_PORT_NOT_MULTICONNECT` | A single-source port got a list | The port must be `multiConnect`; otherwise pass a single value |
| `E_TYPE_MISMATCH` | Source/sink types incompatible | See the compatibility matrix in `dsl-syntax.md`; declare the input with the matching type, or route through a step that produces the target type |
| `E_REQUIRED_MISSING` | A required input port or `outputs[].from` has no value | Fill the `in:` field or fix the reference |
| `E_CYCLE` | The DAG has a cycle | The compiler lists the cycle nodes; reverse a reference |

## Common traps

**Image literal parsed as text** — a non-`http` string can't be type-guessed:
```yaml
in: { reference: "/local/path/cat.png" }   # ❌ → primitive.text
in: { reference: "https://cdn.example.com/cat.png" }  # ✅ recognized by extension
# ✅ or declare an input of type: image and reference $inputs.ref
```

**`gen.text` has no `content` port** — its meta only has `prompt` + `systemPrompt`. A custom port needs an explicit declaration:
```yaml
port_types: { content: text }
in: { content: $inputs.x }
```

**`outputs.from` must be `$steps.x.y`:**
```yaml
outputs: [{ name: x, type: text, from: "hello" }]              # ❌ literal
outputs: [{ name: x, type: text, from: $inputs.something }]    # ❌ $inputs not allowed
outputs: [{ name: x, type: text, from: $steps.gen.text }]      # ✅
```
