"""Stable workflow protocol client. Business schemas are owned by the server."""

import copy
import base64
import hashlib
import json
import mimetypes
import os
import shlex
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


def materialize_artifacts(result, output_dir):
    """Save only declared inline artifacts; keep ordinary text and structured data intact."""
    result = copy.deepcopy(result)
    payload = result.get("result", result)
    if not isinstance(payload, dict):
        return result
    saved = {}
    for artifact in payload.get("artifacts", []):
        value = artifact.get("url", "")
        if not isinstance(value, str) or not value.startswith("data:"):
            continue
        header, separator, encoded = value.partition(",")
        if not separator or not header.endswith(";base64"):
            raise ValueError("Unsupported inline artifact encoding")
        if value not in saved:
            content = base64.b64decode(encoded, validate=True)
            mime = header[5:].split(";", 1)[0]
            extension = mimetypes.guess_extension(mime) or ".bin"
            output_dir = Path(output_dir).resolve()
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / (hashlib.sha256(content).hexdigest() + extension)
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
            saved[value] = str(target)
        artifact["url"] = saved[value]
        artifact["local_path"] = saved[value]
        # Server paths are relative to the named output, including nested tracks/files.
        parent = payload.get("outputs", {})
        path = [artifact.get("name"), *artifact.get("path", [])]
        try:
            for part in path[:-1]:
                parent = parent[part]
            if parent[path[-1]] == value:
                parent[path[-1]] = saved[value]
        except (KeyError, IndexError, TypeError):
            raise ValueError("Artifact path does not match its named workflow output")
    return result


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextmanager
def request_lock(path):
    """OS-owned lock: released on process exit, including a crash during submission."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("This request_id is already being processed; use workflow recover") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class WorkflowClient:
    def __init__(self, config, runtime):
        self.config, self.runtime = config, runtime
        scope = json.dumps([config["baseUrl"].rstrip("/"), config["apiKey"]])
        self.state = runtime.STATE_DIR / "workflow" / hashlib.sha256(scope.encode()).hexdigest()

    def request(self, method, path, body=None, params=None, **options):
        return self.runtime._request_with_retry(
            method, self.config["baseUrl"].rstrip("/") + "/workflows/" + path,
            api_key=self.config["apiKey"], json_body=body, params=params, **options,
        )

    def discover(self, path, params=None):
        # Cached contracts are usable only after an authenticated online revalidation.
        key = hashlib.sha256(json.dumps([path, params], sort_keys=True).encode()).hexdigest()
        cache_path = self.state / "catalog" / (key + ".json")
        cached = None
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        options = {}
        if isinstance(cached, dict) and cached.get("catalog_revision"):
            options = {"extra_headers": {"If-None-Match": '"' + cached.get("resource_revision", cached["catalog_revision"]) + '"'}, "cached_response": cached}
        result = self.request("GET", path, params=params, **options)
        if result.get("protocol_version") != "1":
            raise ValueError("Unsupported workflow protocol; upgrade the client")
        if not result.get("catalog_revision"):
            raise ValueError("Workflow catalog response is missing its revision")
        atomic_json(cache_path, result)
        return result

    def validate(self, body):
        result = self.request("POST", "validate", body)
        if not result.get("valid"):
            raise ValueError(json.dumps(result.get("errors", result), ensure_ascii=False))
        return result

    def prepare(self, body, plan, *, upload=False, uploads=None, on_upload=None):
        prepared = copy.deepcopy(body)
        cache = uploads if uploads is not None else {}
        checked = []
        # Check every local file before the first upload, preserving path, role and order.
        for item in plan:
            parts = item.get("path", [])
            if not parts or parts[0] != "inputs":
                raise ValueError("Unsupported media plan path")
            parent = prepared
            for part in parts[:-1]:
                parent = parent[part]
            value = parent[parts[-1]]
            if not isinstance(value, str) or not value.startswith("@"):
                continue
            media_type = item["media_type"]
            if media_type not in self.runtime.LOCAL_MEDIA_TYPES:
                raise ValueError(f"Local upload protocol does not support {media_type}; use a hosted URL")
            path = self.runtime._local_media_path(value[1:], media_type)
            if item.get("max_bytes") and path.stat().st_size > item["max_bytes"]:
                raise ValueError("Media exceeds this operation's byte limit")
            checked.append((parent, parts[-1], path, media_type))
        for parent, key, path, media_type in checked:
            cache_key = json.dumps([str(path), media_type, path.stat().st_size, path.stat().st_mtime_ns])
            if upload:
                if cache_key not in cache:
                    cache[cache_key] = self.runtime._upload_local_media(self.config, path, media_type, {})
                    if on_upload:
                        on_upload()
                parent[key] = cache[cache_key]
            else:
                parent[key] = "@" + str(path)
        return prepared

    def run(self, body, *, request_id=None, no_wait=False):
        request_id = request_id or uuid.uuid4().hex
        if not request_id.isascii() or not all(c.isalnum() or c in "_-" for c in request_id) or not 16 <= len(request_id) <= 128:
            raise ValueError("Invalid request_id")
        receipt_path = self.state / "requests" / (request_id + ".json")
        with request_lock(receipt_path.with_suffix(".lock")):
            return self._run_locked(body, request_id, receipt_path, no_wait)

    def _run_locked(self, body, request_id, receipt_path, no_wait):
        if receipt_path.exists():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("original") != body:
                raise ValueError("request_id is already bound to different workflow inputs")
            if receipt.get("job_id"):
                return self.finish(receipt["job_id"], no_wait)
            if receipt.get("state") == "submitting":
                return self.recover(request_id, body.get("team_id"), no_wait=no_wait)
        else:
            receipt = {"request_id": request_id, "original": body, "uploads": {}, "state": "preparing"}
            atomic_json(receipt_path, receipt)
        validation = self.validate(body)
        body = {**body, "inputs": validation.get("inputs", body.get("inputs", {}))}
        prepared = self.prepare(body, validation["media_plan"], upload=True, uploads=receipt["uploads"], on_upload=lambda: atomic_json(receipt_path, receipt))
        receipt["payload"] = {**prepared, "request_id": request_id}
        atomic_json(receipt_path, receipt)
        self.validate(prepared)
        receipt["state"] = "submitting"
        atomic_json(receipt_path, receipt)
        try:
            result = self.request("POST", "run", receipt["payload"])
        except self.runtime.ArtClawApiError as exc:
            if getattr(exc, "execution_outcome_unknown", False) or exc.status_code >= 500:
                return self.recover(request_id, body.get("team_id"), no_wait=no_wait)
            receipt["state"] = "rejected"
            atomic_json(receipt_path, receipt)
            raise
        receipt.update(job_id=result["job_id"], state="accepted")
        atomic_json(receipt_path, receipt)
        self.runtime.save_job_record({**result, "request_id": request_id, "job_type": "workflow"})
        return self.finish(result["job_id"], no_wait)

    def recover(self, request_id, team_id=None, *, no_wait=True):
        if not request_id.isascii() or not all(c.isalnum() or c in "_-" for c in request_id) or not 16 <= len(request_id) <= 128:
            raise ValueError("Invalid request_id")
        try:
            result = self.request("GET", "requests/" + quote(request_id, safe=""), params={"team_id": team_id} if team_id else None)
        except self.runtime.ArtClawApiError:
            return {"error": "outcome_unknown", "request_id": request_id, "do_not_resubmit": True,
                    "message": "Acceptance could not be recovered. Keep this request_id; do not create another request."}
        path = self.state / "requests" / (request_id + ".json")
        if path.exists():
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipt.update(job_id=result["job_id"], state="accepted")
            atomic_json(path, receipt)
        self.runtime.save_job_record({**result, "request_id": request_id, "job_type": "workflow"})
        return self.finish(result["job_id"], no_wait)

    def finish(self, job_id, no_wait):
        if no_wait:
            return {"job_id": job_id, "type": "workflow", "message": "Query this job; do not resubmit."}
        result = materialize_artifacts(self.runtime._poll_until(self.config, job_id, "workflow"), self.runtime.OUTPUT_DIR)
        self.runtime.save_job_record(result)
        return result


def add_parsers(sub):
    jobs = sub.add_parser("jobs", help="Query, wait for or cancel accepted jobs").add_subparsers(dest="action", required=True)
    for action in ("status", "wait", "cancel"):
        jobs.add_parser(action).add_argument("job_id")
    listing = jobs.add_parser("list")
    listing.add_argument("--status")
    listing.add_argument("--job-type")
    listing.add_argument("--limit", type=int, default=20)
    nodes = sub.add_parser("nodes", help="Discover current executable capabilities").add_subparsers(dest="action", required=True)
    search = nodes.add_parser("search")
    for flag in ("query", "category", "cursor"):
        search.add_argument("--" + flag, default="")
    search.add_argument("--limit", type=int, default=20)
    nodes.add_parser("describe").add_argument("node_type")
    workflow = sub.add_parser("workflow", help="Validate and run any discovered DSL workflow").add_subparsers(dest="action", required=True)
    workflow.add_parser("guide")
    workflow.add_parser("templates", help="List server-owned workflow presets")
    template = workflow.add_parser("template", help="Fetch a preset as DSL for the normal validate/run flow")
    template.add_argument("name")
    template.add_argument("--output", help="Save the returned DSL to a new local file")
    for action in ("validate", "run"):
        parser = workflow.add_parser(action)
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--dsl")
        source.add_argument("--dsl-file")
        values = parser.add_mutually_exclusive_group()
        values.add_argument("--inputs", default="{}")
        values.add_argument("--inputs-file", help="Read workflow inputs from a JSON file")
        parser.add_argument("--team-id")
        parser.add_argument("--catalog-revision")
        parser.add_argument("--dry-run", action="store_true", help="Validate remotely without uploads or generation")
        if action == "run":
            parser.add_argument("--request-id")
            parser.add_argument("--callback-url", help="Receive the existing signed job completion callback")
            parser.add_argument("--no-wait", action="store_true")
            parser.add_argument("--spawn", action="store_true")
            parser.add_argument("--deliver-to")
            parser.add_argument("--deliver-channel")
    recover = workflow.add_parser("recover")
    recover.add_argument("request_id")
    recover.add_argument("--team-id")


def command(args, config, runtime):
    runtime._check_api_key(config)
    client = WorkflowClient(config, runtime)
    if args.command == "jobs":
        if args.action == "wait":
            return client.finish(args.job_id, False)
        if args.action == "status":
            return materialize_artifacts(runtime.api_job_status(config, args.job_id), runtime.OUTPUT_DIR)
        if args.action == "cancel":
            return runtime.api_cancel_job(config, args.job_id)
        return runtime.api_list_jobs(config, status=args.status, job_type=args.job_type, limit=args.limit)
    if args.command == "nodes":
        if args.action == "describe":
            return client.discover("nodes/" + quote(args.node_type, safe=""))
        return client.discover("nodes", {k: getattr(args, k) for k in ("query", "category", "cursor", "limit")})
    if args.action == "guide":
        return client.discover("dsl-guide")
    if args.action == "templates":
        return client.discover("templates")
    if args.action == "template":
        result = client.discover("templates/" + quote(args.name, safe=""))
        if getattr(args, "output", None):
            destination = Path(args.output).resolve()
            with destination.open("x", encoding="utf-8") as stream:
                stream.write(result["dsl"])
            return {**result, "dsl_file": str(destination)}
        return result
    team = runtime._resolve_team_id(args, config)
    if args.action == "recover":
        return client.recover(args.request_id, team)
    dsl = Path(args.dsl_file).read_text(encoding="utf-8") if args.dsl_file else args.dsl
    inputs = json.loads(Path(args.inputs_file).read_text(encoding="utf-8-sig") if args.inputs_file else args.inputs)
    if not isinstance(inputs, dict):
        raise ValueError("--inputs must be a JSON object")
    revision = args.catalog_revision or client.discover("dsl-guide")["catalog_revision"]
    body = {"dsl": dsl, "inputs": inputs, "catalog_revision": revision}
    if getattr(args, "callback_url", None):
        body["callback_url"] = args.callback_url
    if team:
        body["team_id"] = team
    validated = client.validate(body)
    body["inputs"] = validated.get("inputs", inputs)
    body = client.prepare(body, validated["media_plan"])
    if args.action == "validate" or args.dry_run:
        return {**validated, "prepared_request": body, "generation_submitted": False}
    if runtime._should_spawn(args):
        request_id = args.request_id or uuid.uuid4().hex
        # Templates can exceed OS command-line limits. Snapshot both payloads so
        # delayed workers use the validated content regardless of later file edits.
        directory = client.state / "spawn"
        directory.mkdir(parents=True, exist_ok=True)
        sources = []
        for content, suffix in ((dsl, ".yaml"), (json.dumps(body["inputs"], ensure_ascii=False), ".json")):
            destination = directory / (hashlib.sha256(content.encode()).hexdigest() + suffix)
            try:
                with destination.open("x", encoding="utf-8") as stream:
                    stream.write(content)
            except FileExistsError:
                if destination.read_text(encoding="utf-8") != content:
                    raise ValueError("Saved workflow snapshot does not match its content")
            sources.append(str(destination.resolve()))
        argv = [sys.executable, str(Path(runtime.__file__).resolve()), "workflow", "run", "--dsl-file", sources[0],
                "--inputs-file", sources[1], "--catalog-revision", revision, "--request-id", request_id]
        if team:
            argv += ["--team-id", team]
        if body.get("callback_url"):
            argv += ["--callback-url", body["callback_url"]]
        cmd = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        task = f"Run this workflow command and wait for completion:\n{cmd}\nDeliver every named output and typed artifact using the current platform adapter, preserving subtitles, audio tracks and structured data. On unknown outcome use workflow recover {request_id}; never create a new request_id."
        if args.deliver_to:
            task += f"\nDeliver to {args.deliver_to} on {args.deliver_channel}."
        return {"command": cmd, "argv": argv, "request_id": request_id, "sessions_spawn_args": {"task": task, "label": "artclaw-workflow", "runTimeoutSeconds": 28800}}
    return client.run(body, request_id=args.request_id, no_wait=args.no_wait)
