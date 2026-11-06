#!/usr/bin/env python3
"""ARTCLAW workflow CLI: online discovery, validation, execution and job tracking.

Requires Python 3.8+ and requests. JSON results on stdout; diagnostics on stderr.
"""


import argparse


import json


import os


import sys


import time


from datetime import datetime, timezone


from pathlib import Path


from typing import Any, Dict, List, Optional, Tuple


from urllib.parse import urlparse


try:
    import requests
except ImportError:
    print(json.dumps({
        "error": "requests package not installed",
        "install_command": "pip install requests",
    }), file=sys.stderr)
    sys.exit(1)


STATE_DIR = Path(os.path.expanduser("~/.artclaw"))


CONFIG_FILE = STATE_DIR / "config.json"


LAST_JOB_FILE = STATE_DIR / "last_job.json"


HISTORY_DIR = STATE_DIR / "history"


OUTPUT_DIR = STATE_DIR / "outputs"


DEFAULT_BASE_URL = "https://artclaw.com/api/v1"


TOPUP_URL = "https://artclaw.com/settings"


GET_KEY_URL = "https://artclaw.com/settings"


GITHUB_REPO_URL = "https://github.com/ArtClaw1/artclaw-skill"


GITHUB_RAW_SCRIPT_URL = (
    "https://raw.githubusercontent.com/ArtClaw1/artclaw-skill/main/scripts/artclaw.py"
)


GITHUB_ARCHIVE_URL = (
    "https://github.com/ArtClaw1/artclaw-skill/archive/refs/heads/main.zip"
)


CONNECT_TIMEOUT = 15


READ_TIMEOUT = 60


MAX_RETRIES = 2


POLL_PROFILES = {"workflow": {"interval": 30, "timeout": 28800, "backoff": 1.2, "max_interval": 60}}


TERMINAL_STATUSES = frozenset({"success", "failed", "canceled", "expired"})


LOCAL_MEDIA_TYPES = {
    "file": {},
    "image": {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
    },
    "video": {".mp4": "video/mp4", ".mov": "video/quicktime"},
    "audio": {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".ogg": "audio/ogg", ".aac": "audio/aac", ".flac": "audio/flac",
    },
}


LOCAL_MEDIA_LIMITS = {"image": 30 * 1024 * 1024, "video": 250 * 1024 * 1024,
                      "audio": 50 * 1024 * 1024, "file": 100 * 1024 * 1024}


class ArtClawApiError(Exception):
    """Raised when an ArtClaw API call fails."""

    def __init__(self, message: str, *, status_code: int = 0,
                 code: str = "", detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.detail = detail

    def is_retryable(self) -> bool:
        if getattr(self, "execution_outcome_unknown", False):
            return False
        if self.status_code >= 500:
            return True
        if self.status_code == 429:
            return True
        if self.code == "network_error":
            return True
        return False

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {"error": str(self)}
        if self.code:
            d["code"] = self.code
        if self.status_code:
            d["http_status"] = self.status_code
        if self.detail:
            d["detail"] = self.detail
        if self.status_code == 401:
            d["hint"] = f"API key invalid or missing. Get one at {GET_KEY_URL}"
        elif self.status_code == 402:
            d["hint"] = f"Insufficient credits. Top up at {TOPUP_URL}"
        elif self.status_code == 429:
            d["hint"] = "Rate limited (120 req/min). Wait a moment and retry."
        return d


class ArtClawUploadError(ArtClawApiError):
    """An upload failed before any generation request was submitted."""

    def __init__(self, message: str, *, status_code: int = 0,
                 detail: Any = None, retryable: bool = False):
        super().__init__(message, status_code=status_code,
                         code="upload_failed", detail=detail)
        self.retryable = retryable


class ArtClawPollTimeout(Exception):
    """Raised when polling exceeds the timeout for a job type."""

    def __init__(self, job_id: str, job_type: str, attempts: int,
                 elapsed: float, last_status: str = ""):
        msg = (f"Polling {job_type} job {job_id} timed out after "
               f"{attempts} attempts ({elapsed:.0f}s), last status: {last_status}")
        super().__init__(msg)
        self.job_id = job_id
        self.job_type = job_type
        self.attempts = attempts
        self.elapsed = elapsed
        self.last_status = last_status


class ArtClawPollFailed(Exception):
    """Raised when a polled job enters a terminal failure state."""

    def __init__(self, job_id: str, status: str, error_detail: Any = None):
        msg = f"Job {job_id} entered terminal failure: {status}"
        super().__init__(msg)
        self.job_id = job_id
        self.status = status
        self.error_detail = error_detail


def _local_media_path(value: str, media_type: str) -> Path:
    """Validate a local reference before any upload or paid task submission."""
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Local {media_type} file not found: {value}")
    if media_type != "file" and path.suffix.lower() not in LOCAL_MEDIA_TYPES[media_type]:
        raise ValueError(f"Local file extension does not match {media_type} reference: {value}")
    size = path.stat().st_size
    if size == 0 or size > LOCAL_MEDIA_LIMITS[media_type]:
        raise ValueError(f"Local {media_type} file must be 1 to {LOCAL_MEDIA_LIMITS[media_type]} bytes")
    return path


def _upload_local_media(config: dict, path: Path, media_type: str,
                        cache: Dict[Tuple[str, str], str]) -> str:
    key = (str(path), media_type)
    if key in cache:
        return cache[key]
    content_type = "application/octet-stream" if media_type == "file" else LOCAL_MEDIA_TYPES[media_type][path.suffix.lower()]
    _log(f"Uploading local {media_type} reference: {path.name}")
    try:
        with path.open("rb") as file:
            response = requests.post(
                f"{config['baseUrl'].rstrip('/')}/assets/upload",
                params={"filename": path.name, "type": media_type},
                headers={"X-API-KEY": config["apiKey"], "Content-Type": content_type},
                data=file, timeout=(CONNECT_TIMEOUT, 300),
            )
    except requests.RequestException as exc:
        raise ArtClawUploadError(f"Local media upload failed: {exc}", retryable=True) from exc
    except OSError as exc:
        raise ArtClawUploadError(f"Cannot read local media: {exc}") from exc
    if response.status_code != 200:
        try:
            error = _parse_error_response(response)
        except (AttributeError, TypeError):
            error = ArtClawApiError(f"HTTP {response.status_code}",
                                    status_code=response.status_code)
        raise ArtClawUploadError(
            str(error), status_code=error.status_code, detail=error.detail,
            retryable=error.status_code == 429 or error.status_code >= 500,
        )
    try:
        url = response.json()["url"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ArtClawUploadError("Local media upload returned no URL") from exc
    if not _uploaded_media_url(url):
        raise ArtClawUploadError("Local media upload returned an untrusted URL")
    cache[key] = url
    return url


def _ensure_dir():
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_history_dir():
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_raw_config() -> dict:
    """Load config from config.json. Warns on parse errors (does NOT swallow)."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            _log(f"WARNING: {CONFIG_FILE} has invalid JSON: {e}")
        except IOError as e:
            _log(f"WARNING: cannot read {CONFIG_FILE}: {e}")
    return {}


def _get_config() -> dict:
    """
    Resolve full config.

    Priority: env var > config.json > defaults.
    """
    api_key = os.environ.get("ARTCLAW_API_KEY", "")
    base_url = os.environ.get("ARTCLAW_BASE_URL", "")
    team_id = os.environ.get("ARTCLAW_TEAM_ID", "")

    file_cfg = {}
    if not api_key or not team_id:
        file_cfg = _load_raw_config()
    if not api_key:
        api_key = file_cfg.get("apiKey", "")
    if not base_url:
        base_url = file_cfg.get("baseUrl", "") if file_cfg else ""
    if not team_id:
        team_id = file_cfg.get("teamId", "") if file_cfg else ""

    return {
        "apiKey": api_key,
        "baseUrl": base_url or DEFAULT_BASE_URL,
        "teamId": team_id,
    }


def _check_api_key(config: dict, *, allow_dry_run: bool = False):
    """Exit with setup instructions if API key is missing.

    If allow_dry_run=True and --dry-run is active, skip the check.
    """
    if allow_dry_run:
        return
    if not config.get("apiKey"):
        print(json.dumps({
            "error": "ArtClaw API key not configured",
            "setup_required": True,
            "step1": f"Visit {GET_KEY_URL} to get your API key (starts with vk_)",
            "step2": 'Run: artclaw.py config-init --api-key "vk_your_key"',
            "step3": "Or set env var: export ARTCLAW_API_KEY=vk_your_key",
        }, indent=2), file=sys.stderr)
        sys.exit(1)


def _log(msg: str):
    """Print progress info to stderr (never to stdout)."""
    print(f"[artclaw] {msg}", file=sys.stderr, flush=True)


def _parse_error_response(resp: requests.Response) -> ArtClawApiError:
    """Parse an error HTTP response into ArtClawApiError."""
    try:
        body = resp.json()
        return ArtClawApiError(
            message=body.get("detail", body.get("message", f"HTTP {resp.status_code}")),
            status_code=resp.status_code,
            code=str(body.get("code", "")),
            detail=body.get("details", body.get("data")),
        )
    except (ValueError, KeyError):
        return ArtClawApiError(
            message=f"HTTP {resp.status_code}: {resp.text[:500]}",
            status_code=resp.status_code,
        )


def _request_with_retry(
    method: str,
    url: str,
    *,
    api_key: str = "",
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    max_retries: int = MAX_RETRIES,
    connect_timeout: int = CONNECT_TIMEOUT,
    read_timeout: int = READ_TIMEOUT,
    idempotency_key: Optional[str] = None,
    dry_run: bool = False,
    extra_headers: Optional[dict] = None,
    cached_response: Optional[dict] = None,
) -> dict:
    """
    Unified HTTP request with exponential backoff retry.

    - Retries GET/HEAD on 5xx, 429, and network errors
    - Never automatically replays execution POSTs (creation is not idempotent)
    - Does NOT retry other 4xx (client errors)
    - Returns parsed JSON response on 200
    """
    if dry_run:
        return {
            "__dry_run": True,
            "method": method,
            "url": url,
            "body": json_body,
            "params": params,
        }

    headers: Dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["X-API-KEY"] = api_key
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    if extra_headers:
        headers.update(extra_headers)

    # A supplied key does not establish a server-side idempotency guarantee.
    if method.upper() not in {"GET", "HEAD"}:
        max_retries = 0

    last_err: Optional[ArtClawApiError] = None

    for attempt in range(max_retries + 1):
        try:
            resp = requests.request(
                method, url,
                headers=headers,
                json=json_body if json_body is not None else None,
                params=params,
                timeout=(connect_timeout, read_timeout),
            )
            if resp.status_code == 304 and cached_response is not None:
                return cached_response
            if resp.status_code == 200:
                return resp.json()

            err = _parse_error_response(resp)
            # Don't retry client errors (except 429)
            if 400 <= resp.status_code < 500 and resp.status_code != 429:
                raise err
            last_err = err

        except ArtClawApiError:
            raise
        except requests.RequestException as e:
            last_err = ArtClawApiError(
                f"Network error: {e}", status_code=0, code="network_error",
            )

        if attempt < max_retries:
            wait = 2 ** attempt
            _log(f"Retry {attempt + 1}/{max_retries} in {wait}s...")
            time.sleep(wait)

    if last_err and method.upper() not in {"GET", "HEAD"}:
        last_err.execution_outcome_unknown = True
    raise last_err or ArtClawApiError("Unknown error after retries")


def _poll_until(
    config: dict,
    job_id: str,
    job_type: str,
    *,
    dry_run: bool = False,
) -> dict:
    """
    Generic job poller with type-aware intervals and timeouts.

    Uses POLL_PROFILES[job_type] for interval, timeout, backoff, max_interval.
    Returns the full job status dict when status == "success".
    Raises ArtClawPollTimeout or ArtClawPollFailed on failure/timeout.
    """
    if dry_run:
        return {"__dry_run": True, "job_id": job_id, "job_type": job_type,
                "action": "would_poll"}

    profile = POLL_PROFILES.get(job_type, POLL_PROFILES["workflow"])
    interval = profile["interval"]
    timeout = profile["timeout"]
    backoff = profile["backoff"]
    max_interval = profile["max_interval"]

    base_url = config["baseUrl"]
    api_key = config["apiKey"]
    start = time.time()
    attempt = 0
    last_status = ""

    while True:
        time.sleep(interval)
        attempt += 1
        elapsed = time.time() - start

        if elapsed > timeout:
            raise ArtClawPollTimeout(
                job_id, job_type, attempt, elapsed, last_status,
            )

        try:
            result = _request_with_retry(
                "GET", f"{base_url}/jobs/{job_id}",
                api_key=api_key,
            )
        except ArtClawApiError as e:
            if e.is_retryable():
                _log(f"Poll {job_type}: transient error attempt {attempt}: {e}")
                interval = min(interval * backoff, max_interval)
                continue
            # 404 early on can mean job not yet visible
            if e.status_code == 404 and attempt <= 5:
                _log(f"Poll {job_type}: job not found yet (404), retrying...")
                interval = min(interval * backoff, max_interval)
                continue
            raise

        status = result.get("status", "")
        last_status = status

        if attempt % 3 == 0 or status in TERMINAL_STATUSES:
            _log(f"Poll {job_type} [{job_id[:16]}]: attempt={attempt} "
                 f"status={status} elapsed={elapsed:.0f}s")

        if status == "success":
            return result

        if status in TERMINAL_STATUSES:
            error_detail = result.get("metadata", {}).get("error_detail")
            raise ArtClawPollFailed(job_id, status, error_detail)

        interval = min(interval * backoff, max_interval)


def api_list_voices(config: dict, params: dict, *, dry_run: bool = False) -> dict:
    return _request_with_retry(
        "GET", f"{config['baseUrl']}/voices", api_key=config["apiKey"],
        params=params, dry_run=dry_run,
    )


def api_job_status(config: dict, job_id: str, *,
                   dry_run: bool = False) -> dict:
    return _request_with_retry(
        "GET", f"{config['baseUrl']}/jobs/{job_id}",
        api_key=config["apiKey"], dry_run=dry_run,
    )


def api_list_jobs(config: dict, *, status: str = None, job_type: str = None,
                  limit: int = None, dry_run: bool = False) -> dict:
    params: Dict[str, Any] = {}
    if status:
        params["status"] = status
    if job_type:
        params["type"] = job_type
    if limit is not None:
        params["limit"] = limit
    # Trailing slash required!
    return _request_with_retry(
        "GET", f"{config['baseUrl']}/jobs/",
        api_key=config["apiKey"], params=params or None, dry_run=dry_run,
    )


def api_cancel_job(config: dict, job_id: str, *,
                   dry_run: bool = False) -> dict:
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/jobs/{job_id}/cancel",
        api_key=config["apiKey"], dry_run=dry_run,
    )


def api_account_info(config: dict, *, dry_run: bool = False) -> dict:
    return _request_with_retry(
        "GET", f"{config['baseUrl']}/account/info",
        api_key=config["apiKey"], dry_run=dry_run,
    )


def api_verify_key(config: dict, api_key_to_verify: str, *,
                   dry_run: bool = False) -> dict:
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/auth/verify",
        json_body={"api_key": api_key_to_verify}, dry_run=dry_run,
    )


def _collect_optional_args(args, keys: List[str]) -> dict:
    """Extract optional args as dict, skipping None values.

    Replaces medeo's repetitive `if hasattr(args, "x") and args.x:` pattern.
    """
    return {k: getattr(args, k) for k in keys
            if getattr(args, k, None) is not None}


def _resolve_team_id(args, config: dict) -> Optional[str]:
    """Resolve team_id: --team-id flag > env var > config.json.

    Returns None when no team is configured (billed to the account itself).
    """
    return getattr(args, "team_id", None) or config.get("teamId", "") or None


def save_job_record(record: dict):
    """Save job record to last_job.json and history (microsecond-precision)."""
    _ensure_dir()
    with open(LAST_JOB_FILE, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    _ensure_history_dir()
    # Microsecond precision avoids collisions (medeo used only seconds)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    hist_file = HISTORY_DIR / f"job_{ts}.json"
    with open(hist_file, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    # Prune old history (keep last 50)
    history_files = sorted(HISTORY_DIR.glob("job_*.json"))
    if len(history_files) > 50:
        for old_file in history_files[:-50]:
            old_file.unlink(missing_ok=True)


def _should_spawn(args) -> bool:
    """Infer spawn mode from args.

    Spawn is enabled if:
    - --spawn is explicitly provided, OR
    - both --deliver-to and --deliver-channel are provided.
    """
    has_deliver_to = bool(getattr(args, "deliver_to", None))
    has_deliver_channel = bool(getattr(args, "deliver_channel", None))

    if has_deliver_to ^ has_deliver_channel:
        print(json.dumps({
            "error": "Both --deliver-to and --deliver-channel are required together",
            "hint": "Provide both for async spawn delivery, or omit both for local execution",
        }, indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    return bool(getattr(args, "spawn", False) or
                (has_deliver_to and has_deliver_channel))


def cmd_job_status(args, config: dict):
    """Check a job's current status."""
    _check_api_key(config, allow_dry_run=getattr(args, "dry_run", False))
    result = api_job_status(config, args.job_id,
                            dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_list_jobs(args, config: dict):
    """List historical jobs."""
    _check_api_key(config, allow_dry_run=getattr(args, "dry_run", False))
    kwargs = _collect_optional_args(args, ["status", "job_type", "limit"])
    result = api_list_jobs(config, **kwargs,
                           dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_list_voices(args, config: dict):
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)
    if not 1 <= args.limit <= 5 or args.offset < 0:
        raise ValueError("list-voices requires limit 1–5 and offset >= 0")
    params = _collect_optional_args(args, ["source", "engine", "language", "gender", "scene", "q", "id", "offset", "limit"])
    result = api_list_voices(config, params, dry_run=dry_run)
    if not dry_run and args.catalog_version and result.get("catalog_version") != args.catalog_version:
        raise ValueError("voice catalog changed; discard this page and old numbered choices, query again with offset 0")
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_cancel_job(args, config: dict):
    """Cancel a pending or running job."""
    _check_api_key(config, allow_dry_run=getattr(args, "dry_run", False))
    result = api_cancel_job(config, args.job_id,
                            dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_account_info(args, config: dict):
    """Show account balance and usage."""
    _check_api_key(config, allow_dry_run=getattr(args, "dry_run", False))
    result = api_account_info(config,
                              dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _uploaded_media_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    try:
        return (
            parsed.scheme == "https"
            and parsed.hostname == "assets.vicoo.ai"
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path.startswith("/")
            and not parsed.fragment
        )
    except ValueError:
        return False


def cmd_verify_key(args, config: dict):
    """Verify an API key is valid (no auth required)."""
    key = args.api_key or config.get("apiKey", "")
    if not key:
        print(json.dumps({"error": "No API key provided or configured"}),
              file=sys.stderr)
        sys.exit(1)
    result = api_verify_key(config, key,
                            dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_config(args, config: dict):
    """Show current configuration."""
    safe = dict(config)
    key = safe.get("apiKey", "")
    if key and len(key) > 10:
        safe["apiKey"] = key[:6] + "..." + key[-4:]
    elif key:
        safe["apiKey"] = "***"
    safe["source_env_var"] = "ARTCLAW_API_KEY"
    safe["team_env_var"] = "ARTCLAW_TEAM_ID"
    safe["config_file"] = str(CONFIG_FILE)
    safe["state_dir"] = str(STATE_DIR)
    print(json.dumps(safe, indent=2, ensure_ascii=False))


def cmd_config_init(args, config: dict):
    """Initialize or update config.json with API key (and optional team ID)."""
    _ensure_dir()

    if not args.api_key.startswith("vk_"):
        _log(f"WARNING: API key should start with 'vk_'. Got: {args.api_key[:8]}...")

    raw = _load_raw_config()
    raw["apiKey"] = args.api_key
    raw.setdefault("baseUrl", DEFAULT_BASE_URL)
    if getattr(args, "team_id", None):
        raw["teamId"] = args.team_id

    with open(CONFIG_FILE, "w") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)

    _log(f"Config saved to {CONFIG_FILE}")
    print(json.dumps({
        "status": "ok",
        "config_file": str(CONFIG_FILE),
    }, indent=2))


def _repo_root() -> Path:
    """Return the root directory of the artclaw-skill repo.

    Assumes this script lives at <repo_root>/scripts/artclaw.py.
    """
    return Path(__file__).resolve().parent.parent


def _apply_archive(archive_zip_bytes: bytes, repo_root: Path, *,
                   dry_run: bool = False) -> dict:
    """Extract a GitHub main.zip archive over repo_root.

    The zip contains a single top-level directory (e.g. artclaw-skill-main/).
    We strip that prefix and merge the contents into repo_root.

    Returns a summary dict with lists of added/modified/deleted files.
    """
    import io
    import zipfile
    import shutil
    import tempfile

    with zipfile.ZipFile(io.BytesIO(archive_zip_bytes)) as zf:
        names = zf.namelist()

    # Determine the top-level prefix to strip (e.g. "artclaw-skill-main/")
    prefix = names[0] if names[0].endswith("/") else names[0].split("/")[0] + "/"

    # Collect incoming file paths (relative to repo_root) and their content
    incoming: Dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(archive_zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = info.filename
            if not rel.startswith(prefix):
                continue
            rel_path = rel[len(prefix):]  # strip top-level dir prefix
            if not rel_path:
                continue
            incoming[rel_path] = zf.read(info.filename)

    # Collect existing files under repo_root
    existing: set = set()
    for p in repo_root.rglob("*"):
        if p.is_file():
            existing.add(str(p.relative_to(repo_root)))

    added, modified, unchanged = [], [], []
    for rel_path, new_bytes in sorted(incoming.items()):
        dest = repo_root / rel_path
        if not dest.exists():
            added.append(rel_path)
        else:
            try:
                old_bytes = dest.read_bytes()
            except IOError:
                old_bytes = b""
            if old_bytes != new_bytes:
                modified.append(rel_path)
            else:
                unchanged.append(rel_path)

    # Files in existing but not in incoming are untouched (we never delete)
    # unless --delete is someday added.

    if dry_run:
        return {
            "status": "dry_run",
            "added": added,
            "modified": modified,
            "unchanged_count": len(unchanged),
        }

    # Write new/modified files atomically via temp file + rename
    for rel_path in added + modified:
        dest = repo_root / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=dest.parent,
                                            prefix=".artclaw_upd_")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(incoming[rel_path])
            if dest.exists():
                shutil.copymode(str(dest), tmp_path)
            os.replace(tmp_path, str(dest))
        except OSError as e:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise OSError(f"Failed to write {dest}: {e}") from e

    return {
        "status": "updated",
        "added": added,
        "modified": modified,
        "unchanged_count": len(unchanged),
    }


def cmd_self_update(args, config: dict):
    """Update the entire artclaw-skill repo from GitHub (downloads archive ZIP)."""
    dry_run = getattr(args, "dry_run", False)
    repo_root = _repo_root()

    _log(f"Downloading archive from {GITHUB_ARCHIVE_URL} ...")
    _log(f"Repo root: {repo_root}")

    try:
        resp = requests.get(
            GITHUB_ARCHIVE_URL,
            timeout=(CONNECT_TIMEOUT, 120),
            stream=False,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(json.dumps({
            "error": f"Failed to download archive: {e}",
            "url": GITHUB_ARCHIVE_URL,
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    _log(f"Downloaded {len(resp.content):,} bytes. Applying update...")

    try:
        summary = _apply_archive(resp.content, repo_root, dry_run=dry_run)
    except OSError as e:
        print(json.dumps({"error": str(e)}, indent=2), file=sys.stderr)
        sys.exit(1)

    if not summary["added"] and not summary["modified"]:
        summary["message"] = "Already up to date. No files changed."
    else:
        n_add = len(summary["added"])
        n_mod = len(summary["modified"])
        parts = []
        if n_add:
            parts.append(f"{n_add} added")
        if n_mod:
            parts.append(f"{n_mod} modified")
        action = "Would update" if dry_run else "Updated"
        summary["message"] = f"{action}: {', '.join(parts)}."

    summary["repo_root"] = str(repo_root)
    summary["source"] = GITHUB_ARCHIVE_URL
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def cmd_last_job(args, config: dict):
    """Show the most recent job record."""
    if not LAST_JOB_FILE.exists():
        print(json.dumps({"message": "No job records found."}))
        return
    with open(LAST_JOB_FILE, "r") as f:
        record = json.load(f)
    print(json.dumps(record, indent=2, ensure_ascii=False))


def cmd_history(args, config: dict):
    """Show job history."""
    if not HISTORY_DIR.exists():
        print(json.dumps({"message": "No history found.", "jobs": []}))
        return

    files = sorted(HISTORY_DIR.glob("job_*.json"), reverse=True)
    limit = args.limit
    jobs = []
    for fpath in files[:limit]:
        try:
            with open(fpath, "r") as fh:
                record = json.load(fh)
            jobs.append({
                "file": fpath.name,
                "job_id": record.get("job_id", ""),
                "status": record.get("status", "unknown"),
                "submitted_at": record.get("submitted_at", ""),
                "subcommand": record.get("subcommand", ""),
            })
        except (json.JSONDecodeError, IOError):
            pass

    print(json.dumps({
        "count": len(jobs),
        "total_records": len(files),
        "jobs": jobs,
    }, indent=2, ensure_ascii=False))


def _add_dry_run(parser):
    """Add --dry-run flag to sync command parsers."""
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Print what would be sent without making API calls")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="artclaw",
        description="ArtClaw AI Creative Suite CLI — agent-friendly API client",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    import workflow_client
    workflow_client.add_parsers(sub)

    # --- job-status ---
    p = sub.add_parser("job-status", help="Check job status")
    p.add_argument("--job-id", required=True, help="Job ID to check")
    _add_dry_run(p)

    # --- list-jobs ---
    p = sub.add_parser("list-voices", help="Browse official and Volcengine voices (read-only, no audio playback)")
    p.add_argument("--source", choices=["all", "official", "volcengine"], default="all")
    p.add_argument("--engine", choices=["seed-audio", "indextts"], default="seed-audio")
    p.add_argument("--language", help="Language code, e.g. zh, en, es; unlabelled voices remain last")
    p.add_argument("--gender", choices=["male", "female", "neutral"])
    p.add_argument("--scene", help="Scene/tag substring in the source metadata")
    p.add_argument("--q", help="Search name, ID, style, description and tags")
    p.add_argument("--id", help="Exact source-prefixed ID from a previous catalog response")
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--limit", type=int, default=3, help="Page size 1–5 (default 3)")
    p.add_argument("--catalog-version", help="Reject a changed catalog when continuing the same filtered search")
    _add_dry_run(p)

    # --- list-jobs ---
    p = sub.add_parser("list-jobs", help="List historical jobs")
    p.add_argument("--status", default=None,
                   help="Filter by status (pending, running, success, failed, ...)")
    p.add_argument("--job-type", default=None,
                   help="Filter by type (image_generation, video_generation, ...)")
    p.add_argument("--limit", type=int, default=None,
                   help="Number of jobs to return (1-100)")
    _add_dry_run(p)

    # --- cancel-job ---
    p = sub.add_parser("cancel-job", help="Cancel a pending/running job")
    p.add_argument("--job-id", required=True, help="Job ID to cancel")
    _add_dry_run(p)

    # --- account-info ---
    p = sub.add_parser("account-info", help="Show account balance and usage")
    _add_dry_run(p)

    # --- verify-key ---
    p = sub.add_parser("verify-key",
                       help="Verify an API key (no auth required)")
    p.add_argument("--api-key", default=None,
                   help="API key to verify (uses configured key if omitted)")
    _add_dry_run(p)

    # --- config ---
    sub.add_parser("config", help="Show current configuration")

    # --- config-init ---
    p = sub.add_parser("config-init", help="Initialize or update config")
    p.add_argument("--api-key", required=True,
                   help="ArtClaw API key (starts with vk_)")
    p.add_argument("--team-id", default=None,
                   help="Team UUID — stored so generation bills the team's credits")

    # --- last-job ---
    sub.add_parser("last-job", help="Show last job record")

    # --- history ---
    p = sub.add_parser("history", help="Show local job history")
    p.add_argument("--limit", type=int, default=20,
                   help="Number of records to show")

    # --- self-update ---
    p = sub.add_parser("self-update",
                       help="Update the entire artclaw-skill repo from GitHub")
    _add_dry_run(p)

    return parser


COMMAND_MAP = {
    "job-status": cmd_job_status,
    "list-jobs": cmd_list_jobs,
    "list-voices": cmd_list_voices,
    "cancel-job": cmd_cancel_job,
    "account-info": cmd_account_info,
    "verify-key": cmd_verify_key,
    "config": cmd_config,
    "config-init": cmd_config_init,
    "last-job": cmd_last_job,
    "history": cmd_history,
    "self-update": cmd_self_update,
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = _get_config()

    if args.command in {"nodes", "workflow", "jobs"}:
        import workflow_client
        from types import SimpleNamespace
        def handler(args, config):
            print(json.dumps(workflow_client.command(args, config, SimpleNamespace(**globals())), indent=2, ensure_ascii=False))
    else:
        handler = COMMAND_MAP.get(args.command)
    if not handler:
        parser.print_help()
        sys.exit(1)

    try:
        handler(args, config)
    except ArtClawUploadError as e:
        error = {
            "error": "upload_failed", "message": str(e),
            "generation_submitted": False, "retryable": e.retryable,
        }
        if e.status_code:
            error["http_status"] = e.status_code
        if e.detail:
            error["detail"] = e.detail
        print(json.dumps(error, indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except ArtClawApiError as e:
        if e.is_retryable():
            _log(f"[retryable] {e.to_dict()}")
            print(json.dumps({
                "error": "service_unavailable",
                "message": "ArtClaw service is temporarily unavailable. Please try again.",
                "retryable": True,
            }, indent=2), file=sys.stderr)
        else:
            print(json.dumps(e.to_dict(), indent=2, ensure_ascii=False),
                  file=sys.stderr)
        sys.exit(1)
    except ArtClawPollTimeout as e:
        print(json.dumps({
            "error": "poll_timeout",
            "job_id": e.job_id,
            "job_type": e.job_type,
            "elapsed_seconds": round(e.elapsed),
            "last_status": e.last_status,
            "message": (f"Job timed out after {round(e.elapsed)}s. "
                        f"Check with: artclaw.py job-status --job-id {e.job_id}. "
                        "Do not resubmit the same generation automatically."),
            "retryable": False,
            "do_not_resubmit": True,
        }, indent=2), file=sys.stderr)
        sys.exit(1)
    except ArtClawPollFailed as e:
        print(json.dumps({
            "error": "job_failed",
            "job_id": e.job_id,
            "status": e.status,
            "detail": e.error_detail,
        }, indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except (ValueError, OSError) as e:
        print(json.dumps({
            "error": "invalid_input", "message": str(e),
        }, indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        _log("Interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()
