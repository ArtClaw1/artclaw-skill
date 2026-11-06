# ARTCLAW AI Creative Suite

Use [ARTCLAW](https://artclaw.com/) through conversation to discover creative capabilities and execute workflows. A single task uses one node; multi-step requests connect nodes in the same workflow.

## Setup

Install this repository as a Skill in your agent. Set `ARTCLAW_API_KEY` to a key created in [ARTCLAW settings](https://artclaw.com/settings), or configure it locally:

```bash
python3 scripts/artclaw.py config-init --api-key "vk_..."
python3 scripts/artclaw.py verify-key
```

Requires Python 3.8+ and `requests`. Follow [SKILL.md](SKILL.md) and the platform adapter for OpenClaw, Claude Code, Hermes or a generic CLI environment.

## Usage

Describe the outcome. The agent discovers available nodes and current parameters online, validates a DSL workflow, and submits it.

```bash
python3 scripts/artclaw.py workflow guide
python3 scripts/artclaw.py nodes search --query "image"
python3 scripts/artclaw.py nodes describe <node_type>
python3 scripts/artclaw.py workflow validate --dsl-file task.yaml --inputs '{}'
python3 scripts/artclaw.py workflow run --dsl-file task.yaml --inputs '{}' --no-wait
python3 scripts/artclaw.py jobs status <job_id>
```

Node availability, models and defaults come from the server; model names match Creator. Typed inputs support local media as `@/absolute/path/file`; the server's upload plan is validated before uploading. Dry-run does not upload or generate.

Results preserve named outputs and typed artifacts, including multiple files and structured data. After a timeout, keep tracking the accepted job. For uncertain acceptance recover the original request ID instead of submitting another task.

Team selection uses `--team-id`, then `ARTCLAW_TEAM_ID`, then persisted `teamId`; an authentication-bound team takes precedence on the server.

## Maintenance

Ordinary updates to registered executable nodes, models and parameters are discovered online without synchronizing a business catalog into this Skill. Protocol and platform-adapter changes can still require a release.

Version 2 uses the generic workflow commands exclusively. Deploy the matching workflow API before distributing this client. Previously installed version 1 clients continue to use the retained server APIs; their frozen compatibility fixture is outside the published Skill.
