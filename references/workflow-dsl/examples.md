# Complete Examples

Full, compile-verified workflows. Syntax in [`dsl-syntax.md`](dsl-syntax.md); nodes in [`node-catalog.md`](node-catalog.md).

## A. Two-prompt text-to-image (serial chain)

```yaml
apiVersion: artclaw/v1
kind: Workflow
metadata:
  name: two_prompt_t2i
  title: Two-prompt prompt-optimized image
  tags: [text2image, prompt-optimization]

inputs:
  - { name: subject_prompt, type: text, required: true, description: Subject description }
  - { name: style_prompt, type: text, required: true, description: Style description }
  - { name: reference_image, type: image, required: false, description: Optional style/composition reference }

outputs:
  - { name: result_image, type: image, from: $steps.gen_image.url }

pipeline:
  - id: optimize_prompt
    use: gen.text
    config: { model: gemini-3.1-pro-preview }
    in:
      systemPrompt: |
        You are an AI-image prompt engineer. The user gives a subject and a style.
        Fuse them into one detailed English image prompt covering subject, composition,
        lighting, style, reference artists, and camera language. Output the prompt only.
      prompt:
        - $inputs.subject_prompt
        - $inputs.style_prompt

  - id: gen_image
    use: gen.image
    config: { model: gemini-3.1-flash-image-preview, aspectRatio: "1:1", resolution: "2K" }
    in:
      prompt:
        - $steps.optimize_prompt.text
        - $inputs.reference_image   # http/base64 → multimodal reference; text → prompt
```

## B. Reference-style single-step generator

```yaml
apiVersion: artclaw/v1
kind: Workflow
metadata:
  name: irasutoya_character_generator
  title: Irasutoya character generator
  tags: [text2image]

inputs:
  - { name: input-prompt, type: text, required: true, description: Character description }

outputs:
  - { name: output-image, type: image, from: $steps.gen.url }

pipeline:
  - id: gen
    use: gen.image
    config: { model: gemini-3.1-flash-image-preview, aspectRatio: "1:1", resolution: "2K" }
    in:
      prompt:
        - 'https://assets.vicoo.ai/uploads/uploads/2026-03-21/d8bef84ad84b35d5b94b80f6f6ce0e9f.png'
        - |
          You are an Irasutoya-style illustration generator. Match the reference image's
          art style and linework, and render the user's request. Default to a solid
          background and no text unless the user asks otherwise.
        - $inputs.input-prompt
```

## C. Batch generation with `forEach`

```yaml
apiVersion: artclaw/v1
kind: Workflow
metadata:
  name: batch_character_render
  title: Batch character render
  tags: [text2image, batch]

inputs:
  - { name: prompts, type: array<text>, required: true, description: One prompt per image }
  - { name: style_ref, type: image, required: true, description: Shared style reference }

outputs:
  - { name: results, type: array, from: $steps.batch.results }

pipeline:
  - id: batch
    use: logic.forEach
    config: { executionMode: parallel, maxConcurrency: 5 }
    in:
      array: $inputs.prompts
    body:
      - id: render
        use: gen.image
        config: { model: gemini-3.1-flash-image-preview, aspectRatio: "1:1", resolution: "2K" }
        in:
          prompt:
            - $item
            - $inputs.style_ref
    yields: $body.render.url
```
