#!/usr/bin/env python3
"""
ArtClaw AI Creative Suite CLI — Agent-friendly REST API client.

Flow:  Submit Job → Poll Status → Return Result (JSON stdout)
API:   https://artclaw.com/api/v1
Auth:  X-API-KEY header (starts with vk_)

Requires:
  - pip install requests
  - ARTCLAW_API_KEY env var or config-init

All results output to stdout as JSON. Progress/diagnostics go to stderr.
"""

import argparse
import base64
import binascii
import ipaddress
import json
import math
import os
import re
import shlex
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print(json.dumps({
        "error": "requests package not installed",
        "install_command": "pip install requests",
    }), file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Section 1: Module-Level Constants
# ---------------------------------------------------------------------------

# Paths
STATE_DIR = Path(os.path.expanduser("~/.artclaw"))
CONFIG_FILE = STATE_DIR / "config.json"
LAST_JOB_FILE = STATE_DIR / "last_job.json"
HISTORY_DIR = STATE_DIR / "history"
OUTPUT_DIR = STATE_DIR / "outputs"

# API
DEFAULT_BASE_URL = "https://artclaw.com/api/v1"
TOPUP_URL = "https://artclaw.com/settings"
GET_KEY_URL = "https://artclaw.com/settings"

# Self-update
GITHUB_REPO_URL = "https://github.com/ArtClaw1/artclaw-skill"
GITHUB_RAW_SCRIPT_URL = (
    "https://raw.githubusercontent.com/ArtClaw1/artclaw-skill/main/scripts/artclaw.py"
)
GITHUB_ARCHIVE_URL = (
    "https://github.com/ArtClaw1/artclaw-skill/archive/refs/heads/main.zip"
)

# Network
CONNECT_TIMEOUT = 15   # seconds — TCP handshake
READ_TIMEOUT = 60      # seconds — wait for response body
MAX_RETRIES = 2

# Polling profiles per job type
POLL_PROFILES: Dict[str, dict] = {
    "image":    {"interval": 5,  "timeout": 300,  "backoff": 1.5, "max_interval": 15},
    "video":    {"interval": 10, "timeout": 600,  "backoff": 1.5, "max_interval": 30},
    "audio":    {"interval": 10, "timeout": 600,  "backoff": 1.5, "max_interval": 30},
    "workflow": {"interval": 30, "timeout": 1800, "backoff": 1.2, "max_interval": 60},
    "voice":    {"interval": 3,  "timeout": 120,  "backoff": 1.5, "max_interval": 10},
}

# Job type inference from subcommand
JOB_TYPE_MAP = {
    "generate-image": "image",
    "generate-video": "video",
    "generate-seedance-video": "video",
    "generate-audio": "audio",
    "stt": "voice",
    "tts": "voice",
    "run-workflow": "workflow",
    "remove-bg-image": "image",
    "remove-bg-video": "video",
    "voice-clone": "audio",
    "video-retalk": "video",
    "image-enhance": "image",
    "smart-erase": "video",
    "smart-subtitles": "video",
    "ai-dubbing": "video",
    "video-enhance": "video",
    "video-edit": "video",
    "video-extend": "video",
    "video-timeline": "video",
}

DIRECT_OPERATIONS = {
    "remove-bg-image": "remove-bg/image",
    "remove-bg-video": "remove-bg/video",
    "voice-clone": "voice/clone",
    "voice-convert": "voice/convert",
    "stem-separate": "voice/separate",
    "video-retalk": "video-retalk",
    "image-enhance": "image-enhance",
    "smart-erase": "smart-erase",
    "smart-subtitles": "smart-subtitles",
    "ai-dubbing": "ai-dubbing",
    "video-enhance": "video-enhance",
    "video-edit": "video-edit",
    "video-extend": "video-extend",
    "video-timeline": "video-timeline",
}
SYNC_DIRECT_OPERATIONS = frozenset({"voice-convert", "stem-separate"})
VIDEO_DIRECT_OPERATIONS = frozenset({"video-edit", "video-extend", "video-timeline"})

# Validation sets (module-level, not created per call)
VALID_ASPECT_RATIOS = frozenset({"16:9", "9:16", "1:1", "4:3", "21:9"})
VALID_RESOLUTIONS_IMAGE = frozenset({"512", "1K", "2K", "3K", "4K"})
VALID_RESOLUTIONS_VIDEO = frozenset({"480p", "720p", "1080p", "4k"})
TERMINAL_STATUSES = frozenset({"success", "failed", "canceled", "expired"})
ALLOWED_URL_SCHEMES = ("http://", "https://")
ALLOWED_IMAGE_DATA_URI_PREFIX = "data:image/"


def _aspect_ratio(value: str) -> str:
    if value in ("auto", "adaptive") or re.fullmatch(r"[1-9]\d*:[1-9]\d*", value):
        return value
    raise argparse.ArgumentTypeError("Expected a positive W:H ratio, auto or adaptive")


def _video_content_items(args, *, for_spawn: bool = False):
    """Read typed references; relative media paths use the caller's cwd."""
    raw = getattr(args, "content_items", None)
    if raw is None:
        return None
    try:
        if getattr(args, "reference_urls", None) or getattr(args, "reference_files", None):
            raise ValueError("--content-items cannot be combined with legacy reference flags")
        if raw.startswith("@"):
            raw = Path(raw[1:]).read_text(encoding="utf-8-sig")
        items = json.loads(raw)
        if not isinstance(items, list) or not items:
            raise ValueError("--content-items must be a non-empty JSON array")
        result = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"role", "url"}:
                raise ValueError("Each content item must contain exactly role and url")
            role, url = item["role"], item["url"]
            media = {"first_frame": "image", "last_frame": "image",
                     "reference_image": "image", "reference_video": "video",
                     "reference_audio": "audio"}.get(role) if isinstance(role, str) else None
            if not media or not isinstance(url, str) or not url:
                raise ValueError("Each content item needs a valid media role and non-empty url")
            if url.startswith("@"):
                url = "@" + str(_local_media_path(url[1:], media))
            elif args.command == "generate-video" and media != "image" and not url.startswith(ALLOWED_URL_SCHEMES):
                raise ValueError("generate-video requires HTTP(S) or @local-file video/audio references")
            elif media == "video" and not url.startswith(ALLOWED_URL_SCHEMES):
                raise ValueError("Video references require HTTP(S) or @local-file URLs")
            if url.startswith("data:"):
                if not url.startswith(f"data:{media}/") or ";base64," not in url:
                    raise ValueError(f"{role} requires a {media} base64 data URI")
            elif not url.startswith(ALLOWED_URL_SCHEMES) and not url.startswith("@"):
                raise ValueError("Reference URL must use http(s), a data URI or @local-file")
            result.append({"role": role, "url": url})
        return result
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


def _is_seedance25_model(model: Optional[str]) -> bool:
    normalized = (model or "").lower().replace(".", "-").replace("_", "-")
    return bool(re.search(r"(?:^|-)seedance-2-5(?:-|$)", normalized))


def _spawn_content_argument(items: List[dict]) -> str:
    """Keep large reference payloads out of the child process command line."""
    serialized = json.dumps(items, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= 8192:
        return serialized
    try:
        directory = STATE_DIR / "spawn-inputs"
        directory.mkdir(parents=True, exist_ok=True)
        # Keep the file for delayed execution and retries, like local job history.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", prefix="video-",
            dir=directory, delete=False,
        ) as handle:
            handle.write(serialized)
            return f"@{Path(handle.name).resolve()}"
    except OSError as exc:
        print(json.dumps({"error": f"Cannot save spawn references: {exc}"}), file=sys.stderr)
        sys.exit(1)


def _validate_video_output_format_model(model: Optional[str], output_format: Optional[str]):
    if output_format is None or _is_seedance25_model(model):
        return
    print(json.dumps({
        "error": "--output-format is only supported by Seedance 2.5",
        "model": model,
    }), file=sys.stderr)
    sys.exit(1)

# Multimodal reference (generate-text --reference-parts). The Gemi models map to
# Gemini under the hood and are the only ones that accept image/video/audio input.
# Backend enforces the same set (vicoo-mcp-server model_policy.REST_TEXT_MODELS).
VALID_TEXT_MODELS = frozenset({
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "Gemi 3.0 Flash",
    "Gemi 3.1 Pro",
    "Gemi 3.1 Flash Lite",
    "Gemi 3.5 Flash",
    "GT-5.5",
})

REFERENCE_PART_TYPES = ("image", "video", "audio")
LOCAL_MEDIA_TYPES = {
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
LOCAL_MEDIA_LIMITS = {"image": 30 * 1024 * 1024, "video": 50 * 1024 * 1024,
                      "audio": 50 * 1024 * 1024}

# Display name → real model ID (mirrors vicoo-mcp-server model_policy._NAME_TO_MODEL).
# Used only to tell whether a text model is Gemini-backed (for reference_parts gating).
MODEL_ALIASES = {
    "Gemi 3.0 Flash": "gemini-3-flash-preview",
    "Gemi 3.1 Pro": "gemini-3.1-pro-preview",
    "Gemi 3.1 Flash Lite": "gemini-3.1-flash-lite",
    "Gemi 3.5 Flash": "gemini-3.5-flash",
    "GT-5.5": "gpt-5.5",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
}

# Spawn
SPAWN_TIMEOUT_SECONDS = 2400  # 40 minutes


# ---------------------------------------------------------------------------
# Section 2: Exception Hierarchy
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Section 3: Config / Credential Loading
# ---------------------------------------------------------------------------

def _local_media_path(value: str, media_type: str) -> Path:
    """Validate a local reference before any upload or paid task submission."""
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Local {media_type} file not found: {value}")
    if path.suffix.lower() not in LOCAL_MEDIA_TYPES[media_type]:
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
    content_type = LOCAL_MEDIA_TYPES[media_type][path.suffix.lower()]
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
    if not _timeline_media_url(url):
        raise ArtClawUploadError("Local media upload returned an untrusted URL")
    cache[key] = url
    return url


def _prepare_media_value(value: Any, media_type: str, config: dict, *,
                         dry_run: bool, cache: Dict[Tuple[str, str], str]) -> Any:
    if not isinstance(value, str) or not value.startswith("@"):
        return value
    path = _local_media_path(value[1:], media_type)
    return f"@{path}" if dry_run else _upload_local_media(config, path, media_type, cache)


def _prepare_content_items(items: List[dict], config: dict, *, dry_run: bool) -> List[dict]:
    cache: Dict[Tuple[str, str], str] = {}
    roles = {"first_frame": "image", "last_frame": "image",
             "reference_image": "image", "reference_video": "video",
             "reference_audio": "audio"}
    return [{"role": item["role"], "url": _prepare_media_value(
        item["url"], roles[item["role"]], config, dry_run=dry_run, cache=cache,
    )} for item in items]


def _prepare_reference_urls(urls: List[str], files: List[str], media_type: str,
                            config: dict, *, dry_run: bool) -> List[str]:
    """Resolve URL and file flags through one upload cache, in queue order."""
    references = list(urls) + [f"@{file}" for file in files]
    for value in references:
        if value.startswith("@"):
            _local_media_path(value[1:], media_type)
        else:
            _validate_url(value)
    cache: Dict[Tuple[str, str], str] = {}
    return [_prepare_media_value(value, media_type, config, dry_run=dry_run, cache=cache)
            for value in references]


def _prepare_workflow_inputs(value: Any, config: dict, *, dry_run: bool,
                             cache: Dict[Tuple[str, str], str]) -> Any:
    """Resolve explicit @media paths in arbitrary workflow input structures."""
    if isinstance(value, dict):
        return {key: _prepare_workflow_inputs(item, config, dry_run=dry_run, cache=cache)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare_workflow_inputs(item, config, dry_run=dry_run, cache=cache)
                for item in value]
    if isinstance(value, str) and value.startswith("@"):
        candidate = value[1:]
        extension = Path(candidate).suffix.lower()
        media_type = next((kind for kind, types in LOCAL_MEDIA_TYPES.items()
                           if extension in types), None)
        if not media_type:
            # Workflow inputs can also contain text such as @username or
            # @alice 请点评. Only path-shaped values are explicit local files.
            looks_like_path = (candidate.startswith((".", "~")) or
                               "/" in candidate or "\\" in candidate or
                               Path(candidate).is_file())
            if looks_like_path:
                raise ValueError(f"unsupported local media extension in workflow input: {value}")
            return value
        return _prepare_media_value(value, media_type, config,
                                    dry_run=dry_run, cache=cache)
    return value


DIRECT_MEDIA_FIELDS = {
    "generate-audio": {"reference_audio_urls": "audio", "video_url": "video"},
    "voice-convert": {"audio_url": "audio"},
    "stem-separate": {"audio_url": "audio"},
    "remove-bg-image": {"image_url": "image", "image_urls": "image"},
    "remove-bg-video": {"video_url": "video"},
    "voice-clone": {"reference_audio_urls": "audio", "emo_audio_url": "audio"},
    "video-retalk": {"video_url": "video", "audio_url": "audio", "ref_image_url": "image"},
    "image-enhance": {"image_url": "image"},
    "smart-erase": {"video_url": "video"},
    "smart-subtitles": {"video_url": "video"},
    "ai-dubbing": {"video_url": "video"},
    "video-enhance": {"video_url": "video"},
    "video-edit": {
        "source_video_url": "video", "reference_image_urls": "image",
        "reference_video_urls": "video", "reference_audio_urls": "audio",
    },
    "video-extend": {
        "source_video_url": "video", "reference_image_urls": "image",
        "reference_video_urls": "video", "reference_audio_urls": "audio",
    },
}


def _prepare_direct_media(operation: str, payload: dict, config: dict, *,
                          dry_run: bool) -> dict:
    """Resolve only known media fields; preserve all unrelated user input."""
    result = dict(payload)
    cache: Dict[Tuple[str, str], str] = {}
    for field, media_type in DIRECT_MEDIA_FIELDS.get(operation, {}).items():
        if field not in result:
            continue
        value = result[field]
        if field.endswith("_urls"):
            if not isinstance(value, list):
                raise ValueError(f"{field} must be a list")
            if any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"{field} entries must be non-empty URLs or @local-files")
            result[field] = [_prepare_media_value(item, media_type, config,
                             dry_run=dry_run, cache=cache) for item in value]
        else:
            if not isinstance(value, str):
                raise ValueError(f"{field} must be a URL or @local-file")
            result[field] = _prepare_media_value(value, media_type, config,
                                                 dry_run=dry_run, cache=cache)
    if operation == "video-timeline":
        if isinstance(result.get("cut"), dict):
            cut = dict(result["cut"])
            cut["source_video_url"] = _prepare_media_value(
                cut.get("source_video_url"), "video", config, dry_run=dry_run, cache=cache,
            )
            result["cut"] = cut
        if isinstance(result.get("video_clips"), list):
            result["video_clips"] = [
                {**clip, "url": _prepare_media_value(
                    clip.get("url"), "video", config, dry_run=dry_run, cache=cache,
                )} if isinstance(clip, dict) else clip
                for clip in result["video_clips"]
            ]
    return result


def _build_reference_parts(specs: List[str], config: dict, *,
                           dry_run: bool) -> List[Dict[str, str]]:
    """Turn `--reference-parts` args into the API's [{type, url}] list.

    Each spec is one of:
      - `type=url`        → pass the URL through (https:// or data: URI)
      - `type=@filepath`  → upload a local file and use its CDN URL
    `type` must be image / video / audio.
    """
    parts: List[Dict[str, str]] = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(
                f"Invalid --reference-parts spec (expected type=url or type=@file): {spec}"
            )
        ref_type, ref_value = spec.split("=", 1)
        ref_type = ref_type.strip().lower()
        ref_value = ref_value.strip()
        if ref_type not in REFERENCE_PART_TYPES:
            raise ValueError(
                f"Unsupported reference type {ref_type!r}; "
                f"expected one of {', '.join(REFERENCE_PART_TYPES)}"
            )
        if not ref_value:
            raise ValueError(f"Empty reference value for type {ref_type!r}")

        if ref_value.startswith("@"):
            ref_value = "@" + str(_local_media_path(ref_value[1:], ref_type))
        elif not (ref_value.startswith("https://") or
                  ref_value.startswith(f"data:{ref_type}/")):
            raise ValueError(f"{ref_type} reference must use HTTPS, a matching data URI or @local-file")
        parts.append({"type": ref_type, "url": ref_value})
    cache: Dict[Tuple[str, str], str] = {}
    return [{"type": part["type"], "url": _prepare_media_value(
        part["url"], part["type"], config, dry_run=dry_run, cache=cache,
    )} for part in parts]


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


# ---------------------------------------------------------------------------
# Section 4: Low-Level HTTP — Unified _request_with_retry
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Section 5: Generic _poll_until
# ---------------------------------------------------------------------------

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

    profile = POLL_PROFILES.get(job_type, POLL_PROFILES["image"])
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


# ---------------------------------------------------------------------------
# Section 6: API Endpoint Functions
# ---------------------------------------------------------------------------

# --- Generation (async, returns job_id) ---

def api_generate_image(config: dict, prompt: str, *,
                       aspect_ratio: str = None, resolution: str = None,
                       reference_urls: List[str] = None, model: str = None,
                       callback_url: str = None,
                       n: int = None, seed: int = None,
                       quality: str = None,
                       background: str = None,
                       output_format: str = None,
                       negative_prompt: str = None,
                       style: str = None, guidance_scale: float = None,
                       watermark: bool = None,
                       online_search: bool = None,
                       moderation: str = None,
                       sequential_image_generation: str = None,
                       team_id: str = None,
                       idempotency_key: str = None,
                       dry_run: bool = False) -> dict:
    body: Dict[str, Any] = {"prompt": prompt}
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if not resolution and (not model or model == "doubao-seedream-5-0-260128"):
        resolution = "2K"
    if resolution:
        body["resolution"] = resolution
    if reference_urls:
        body["reference_urls"] = reference_urls
    if model:
        body["model"] = model
    if callback_url:
        body["callback_url"] = callback_url
    if n is not None and n > 1:
        body["n"] = n
    if quality is not None and quality.strip():
        body["quality"] = quality
    if background is not None and background.strip():
        body["background"] = background
    if seed is not None:
        body["seed"] = seed
    if output_format:
        body["output_format"] = output_format
    if negative_prompt:
        body["negative_prompt"] = negative_prompt
    if style:
        body["style"] = style
    if guidance_scale is not None:
        body["guidance_scale"] = guidance_scale
    if watermark is not None:
        body["watermark"] = watermark
    for key, value in (("online_search", online_search), ("moderation", moderation),
                       ("sequential_image_generation", sequential_image_generation)):
        if value is not None:
            body[key] = value
    if team_id:
        body["team_id"] = team_id
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/image",
        api_key=config["apiKey"], json_body=body,
        idempotency_key=idempotency_key, dry_run=dry_run,
    )


def api_generate_video(config: dict, prompt: str, *,
                       aspect_ratio: str = None, duration: int = None,
                       resolution: str = None,
                       reference_urls: List[str] = None, model: str = None,
                       callback_url: str = None,
                       generate_audio: bool = None,
                       output_format: str = None,
                       content_items: List[dict] = None,
                       seed: int = None, online_search: bool = None,
                       omni_reference_task_type: str = None,
                       team_id: str = None,
                       idempotency_key: str = None,
                       dry_run: bool = False) -> dict:
    body: Dict[str, Any] = {"prompt": prompt}
    if aspect_ratio:
        body["aspect_ratio"] = aspect_ratio
    if duration is not None:
        body["duration"] = duration
    if resolution:
        body["resolution"] = resolution
    if reference_urls:
        body["content_items"] = [{"role": "reference_image", "url": u} for u in reference_urls]
    for key, value in (("content_items", content_items), ("seed", seed),
                       ("online_search", online_search),
                       ("omni_reference_task_type", omni_reference_task_type)):
        if value is not None:
            body[key] = value
    if model:
        body["model"] = model
    if callback_url:
        body["callback_url"] = callback_url
    if generate_audio is not None:
        body["generate_audio"] = generate_audio
    if output_format:
        body["output_format"] = output_format
    if team_id:
        body["team_id"] = team_id
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/video",
        api_key=config["apiKey"], json_body=body,
        idempotency_key=idempotency_key, dry_run=dry_run,
    )


def api_generate_audio(config: dict, *,
                       provider: str = "suno", model: str = "Suvo V4.5 ALL",
                       prompt: str = "",
                       instrumental: bool = None, custom_mode: bool = None,
                       style: str = None, title: str = None,
                       vocal_gender: str = None, negative_tags: str = None,
                       style_weight: float = None,
                       weirdness_constraint: float = None,
                       audio_weight: float = None, persona_id: str = None,
                       duration: int = None,
                       operation: str = None,
                       reference_audio_urls: List[str] = None,
                       video_url: str = None, video_id: str = None,
                       sound_effect_prompt: str = None, bgm_prompt: str = None,
                       asmr_mode: bool = None,
                       callback_url: str = None,
                       team_id: str = None,
                       idempotency_key: str = None,
                       dry_run: bool = False) -> dict:
    body: Dict[str, Any] = {"provider": provider, "model": model}
    if prompt:
        body["prompt"] = prompt
    if instrumental is not None:
        body["instrumental"] = instrumental
    if custom_mode is not None:
        body["custom_mode"] = custom_mode
    if style:
        body["style"] = style
    if title:
        body["title"] = title
    if vocal_gender:
        body["vocal_gender"] = vocal_gender
    if negative_tags:
        body["negative_tags"] = negative_tags
    if style_weight is not None:
        body["style_weight"] = style_weight
    if weirdness_constraint is not None:
        body["weirdness_constraint"] = weirdness_constraint
    if audio_weight is not None:
        body["audio_weight"] = audio_weight
    if persona_id:
        body["persona_id"] = persona_id
    if duration is not None:
        body["duration"] = duration
    for key, value in (("operation", operation),
                       ("reference_audio_urls", reference_audio_urls),
                       ("video_url", video_url), ("video_id", video_id),
                       ("sound_effect_prompt", sound_effect_prompt),
                       ("bgm_prompt", bgm_prompt), ("asmr_mode", asmr_mode)):
        if value is not None:
            body[key] = value
    if callback_url:
        body["callback_url"] = callback_url
    if team_id:
        body["team_id"] = team_id
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/audio",
        api_key=config["apiKey"], json_body=body,
        idempotency_key=idempotency_key, dry_run=dry_run,
    )


def api_generate_text(config: dict, prompt: str, *,
                      model: str = "deepseek-v4-pro",
                      provider: str = "deepseek",
                      system_instruction: str = None,
                      response_format: str = None,
                      reference_parts: List[Dict[str, str]] = None,
                      team_id: str = None,
                      callback_url: str = None,
                      dry_run: bool = False) -> dict:
    body: Dict[str, Any] = {"prompt": prompt, "model": model, "provider": provider}
    if system_instruction:
        body["system_instruction"] = system_instruction
    if response_format:
        body["response_format"] = response_format
    if reference_parts:
        body["reference_parts"] = reference_parts
    if team_id:
        body["team_id"] = team_id
    if callback_url:
        body["callback_url"] = callback_url
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/text",
        api_key=config["apiKey"], json_body=body, dry_run=dry_run,
    )


def api_remove_bg(config: dict, image_base64: str, *,
                  team_id: str = None,
                  dry_run: bool = False) -> dict:
    """Remove background from an image. Returns job_id immediately."""
    body: Dict[str, Any] = {"image_base64": image_base64}
    if team_id:
        body["team_id"] = team_id
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/remove-bg",
        api_key=config["apiKey"], json_body=body,
        dry_run=dry_run,
    )


def api_direct_operation(config: dict, operation: str, payload: dict, *,
                         dry_run: bool = False) -> dict:
    """Submit one typed direct REST operation without repeating a billable POST."""
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/{DIRECT_OPERATIONS[operation]}",
        api_key=config["apiKey"], json_body=payload,
        max_retries=0,
        read_timeout=300 if operation in SYNC_DIRECT_OPERATIONS else READ_TIMEOUT,
        dry_run=dry_run,
    )


def api_generate_seedance_video(config: dict, prompt: str, *,
                                model: str = "doubao-seedance-2-0-260128",
                                aspect_ratio: str = None, duration: int = None,
                                resolution: str = None, seed: int = None,
                                generate_audio: bool = None,
                                output_format: str = None,
                                content_items: List[dict] = None,
                                online_search: bool = None,
                                watermark: bool = None,
                                return_last_frame: bool = None,
                                execution_expires_after: int = None,
                                safety_identifier: str = None,
                                priority: int = None,
                                callback_url: str = None,
                                dry_run: bool = False) -> dict:
    # 后端 /volcengine/video 对齐 Ark 官方 API：prompt 必须放在 content[] 数组里，
    # 参考图为 {"type":"image_url","image_url":{"url":...},"role":...}。顶层 prompt 会被忽略。
    body: Dict[str, Any] = {
        "content": [{"type": "text", "text": prompt}],
        "model": model,
    }
    for item in content_items or []:
        media = {"reference_video": "video", "reference_audio": "audio"}.get(item["role"], "image")
        key = f"{media}_url"
        body["content"].append({"type": key, key: {"url": item["url"]}, "role": item["role"]})
    if online_search is not None:
        body["tools"] = [{"type": "web_search"}] if online_search else []
    if watermark is not None:
        body["watermark"] = watermark
    if aspect_ratio:
        body["ratio"] = aspect_ratio
    if duration is not None:
        body["duration"] = duration
    if resolution:
        body["resolution"] = resolution
    if seed is not None:
        body["seed"] = seed
    if generate_audio is not None:
        body["generate_audio"] = generate_audio
    if output_format:
        body["output_format"] = output_format
    if return_last_frame is not None:
        body["return_last_frame"] = return_last_frame
    if execution_expires_after is not None:
        body["execution_expires_after"] = execution_expires_after
    if safety_identifier:
        body["safety_identifier"] = safety_identifier
    if priority is not None and priority > 0:
        body["priority"] = priority
    if callback_url:
        body["callback_url"] = callback_url
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/volcengine/video",
        api_key=config["apiKey"], json_body=body, dry_run=dry_run,
    )


def api_voice_stt(config: dict, audio: str, *,
                  callback_url: str = None,
                  dry_run: bool = False) -> dict:
    """Submit STT async job. Returns job_id immediately. Poll for result."""
    body: Dict[str, Any] = {"audio": audio}
    if callback_url:
        body["callback_url"] = callback_url
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/voice/stt",
        api_key=config["apiKey"], json_body=body, dry_run=dry_run,
    )


def api_voice_tts(config: dict, text: str, *,
                  speaker: str = None,
                  callback_url: str = None,
                  dry_run: bool = False) -> dict:
    """Submit TTS async job. Returns job_id immediately. Poll for result."""
    body: Dict[str, Any] = {"text": text}
    if speaker:
        body["speaker"] = speaker
    if callback_url:
        body["callback_url"] = callback_url
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/generate/voice/tts",
        api_key=config["apiKey"], json_body=body, dry_run=dry_run,
    )


# --- Workflows ---

def api_list_voices(config: dict, params: dict, *, dry_run: bool = False) -> dict:
    return _request_with_retry(
        "GET", f"{config['baseUrl']}/voices", api_key=config["apiKey"],
        params=params, dry_run=dry_run,
    )


def api_list_workflows(config: dict, *, dry_run: bool = False) -> dict:
    # Trailing slash required! Without it: 307 redirect -> 502
    return _request_with_retry(
        "GET", f"{config['baseUrl']}/workflows/",
        api_key=config["apiKey"], dry_run=dry_run,
    )


def api_run_workflow(config: dict, workflow_id: str, inputs: dict, *,
                     timeout: int = None, callback_url: str = None,
                     team_id: str = None,
                     dry_run: bool = False) -> dict:
    body: Dict[str, Any] = {"inputs": inputs}
    if timeout is not None:
        body["timeout"] = timeout
    if callback_url:
        body["callback_url"] = callback_url
    if team_id:
        body["team_id"] = team_id
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/workflows/{workflow_id}/run",
        api_key=config["apiKey"], json_body=body, dry_run=dry_run,
    )


def api_run_workflow_from_dsl(config: dict, dsl: str, inputs: dict, *,
                             timeout: int = None, callback_url: str = None,
                             team_id: str = None,
                             dry_run: bool = False) -> dict:
    """执行由 workflow DSL 源码定义的工作流（服务端编译后执行）。"""
    body: Dict[str, Any] = {"dsl": dsl, "inputs": inputs}
    if timeout is not None:
        body["timeout"] = timeout
    if callback_url:
        body["callback_url"] = callback_url
    if team_id:
        body["team_id"] = team_id
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/workflows/run",
        api_key=config["apiKey"], json_body=body, dry_run=dry_run,
    )


# --- Job management ---

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


# --- Account ---

def api_account_info(config: dict, *, dry_run: bool = False) -> dict:
    return _request_with_retry(
        "GET", f"{config['baseUrl']}/account/info",
        api_key=config["apiKey"], dry_run=dry_run,
    )


# --- Auth (public, no key required for verify) ---

def api_verify_key(config: dict, api_key_to_verify: str, *,
                   dry_run: bool = False) -> dict:
    return _request_with_retry(
        "POST", f"{config['baseUrl']}/auth/verify",
        json_body={"api_key": api_key_to_verify}, dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Section 7: High-Level Operations
# ---------------------------------------------------------------------------

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


def _validate_url(url: str):
    is_http_url = any(url.startswith(s) for s in ALLOWED_URL_SCHEMES)
    is_image_data_uri = (
        url.startswith(ALLOWED_IMAGE_DATA_URI_PREFIX)
        and ";base64," in url[:128]
    )
    if not (is_http_url or is_image_data_uri):
        print(json.dumps({
            "error": (
                "Invalid URL scheme. Only http://, https://, and "
                "data:image/*;base64,... are allowed."
            ),
            "url": url[:100] + "..." if len(url) > 100 else url,
        }), file=sys.stderr)
        sys.exit(1)


def submit_and_poll(
    config: dict,
    subcommand: str,
    api_fn: Callable,
    api_kwargs: dict,
    *,
    no_wait: bool = False,
    dry_run: bool = False,
) -> dict:
    """Submit an async generation job. If wait=True, poll to completion.

    Returns the full result dict (either submission response or final result).
    """
    started_at = datetime.now(timezone.utc)
    job_type = JOB_TYPE_MAP.get(subcommand, "image")

    _log(f"Submitting {subcommand} job...")
    submit_result = api_fn(config, **api_kwargs, dry_run=dry_run)

    if dry_run:
        return submit_result

    job_id = submit_result.get("job_id", "")
    _log(f"Job submitted: {job_id} (type={job_type})")

    # Save submission record
    record = {
        "job_id": job_id,
        "job_type": job_type,
        "subcommand": subcommand,
        "status": "pending",
        "submitted_at": started_at.isoformat(),
    }
    save_job_record(record)

    if no_wait:
        submit_result["message"] = (
            f"Job submitted. Poll with: artclaw.py job-status --job-id {job_id}"
        )
        return submit_result

    # Poll until completion
    final = _poll_until(config, job_id, job_type)

    elapsed = time.time() - started_at.timestamp()
    final["_cli"] = {
        "subcommand": subcommand,
        "total_duration_seconds": round(elapsed, 1),
    }
    save_job_record(final)
    _log(f"Job complete: {job_id} ({elapsed:.0f}s)")
    return final


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


# ---------------------------------------------------------------------------
# Section 8: Spawn Task Builder
# ---------------------------------------------------------------------------

def _build_delivery_instructions(deliver_to: str, channel: str,
                                 base_dir: str, subcommand: str) -> str:
    """Build channel-specific delivery instructions for spawn task."""
    # Determine if this is image or video generation
    is_image = subcommand in {"generate-image", "remove-bg-image", "image-enhance"}
    is_audio = subcommand in {"generate-audio", "voice-clone"}

    if subcommand == "smart-subtitles":
        return f"  1. Share the subtitle file URL with \"{deliver_to}\" as a link."

    if is_audio:
        return (
            f"  1. Share every generated audio/BGM URL with \"{deliver_to}\" "
            "as a separate link, in result order. "
            "Native audio upload is not configured in this skill."
        )

    if channel == "feishu":
        if is_image:
            return (
                "  1. For each generated image URL, send via Feishu:\n"
                f"     python3 {base_dir}/scripts/feishu_send_video.py "
                f"--image \"<image_url>\" --to \"{deliver_to}\"\n"
            )
        else:  # video
            return (
                "  1. For each generated video URL, send via Feishu:\n"
                f"     python3 {base_dir}/scripts/feishu_send_video.py "
                f"--video-url \"<video_url>\" --to \"{deliver_to}\" "
                "--cover-url \"<thumbnail_url>\" --duration <duration_ms>\n"
                "     Note: duration is in milliseconds for Feishu"
            )
    if channel == "telegram":
        if is_image:
            return (
                "  1. For each generated image URL, download it to a unique local file "
                "(curl -o /tmp/artclaw_result_N.jpg \"<image_url>\")\n"
                "  2. Send each file via Telegram:\n"
                f"     TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN "
                f"python3 {base_dir}/scripts/telegram_send_video.py "
                f"--image /tmp/artclaw_result_N.jpg --to \"{deliver_to}\" "
                "--caption \"Result ready!\"\n"
            )
        else:  # video
            return (
                "  1. For each generated video URL, download it to a unique local file "
                "(curl -o /tmp/artclaw_result_N.mp4 \"<video_url>\")\n"
                "  2. Send each file via Telegram:\n"
                f"     TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN "
                f"python3 {base_dir}/scripts/telegram_send_video.py "
                f"--video /tmp/artclaw_result_N.mp4 --to \"{deliver_to}\" "
                "--cover-url \"<thumbnail_url>\" --duration <duration_seconds> "
                "--caption \"Result ready!\"\n"
                "     Note: duration is in seconds for Telegram"
            )
    if channel == "discord":
        media_file = "/tmp/artclaw_result_N.jpg" if is_image else "/tmp/artclaw_result_N.mp4"
        return (
            f"  1. For each generated media URL, download it to a unique local file:\n"
            f"     curl -sL -o {media_file} \"<media_url>\"\n"
            "  2. Send each file with the message tool:\n"
            f"     message(action=\"send\", channel=\"discord\", "
            f"target=\"{deliver_to}\", message=\"Result ready!\", "
            f"filePath=\"{media_file}\")\n"
            "     (25 MB limit; for larger files share URL as link)"
        )
    return (
        f"  1. Deliver every generated media result to \"{deliver_to}\" using the message tool,\n"
        "     or share every result URL as a separate link."
    )


def build_spawn_task(
    subcommand: str,
    args_dict: dict,
    *,
    deliver_to: Optional[str] = None,
    deliver_channel: Optional[str] = None,
    script_path: Optional[str] = None,
) -> dict:
    """Build a sessions_spawn payload for async generation via another agent.

    Returns:
        {
            "sessions_spawn_args": {"task": ..., "label": ..., "runTimeoutSeconds": ...},
            "command": "python3 .../artclaw.py generate-image --prompt ...",
        }
    """
    if script_path is None:
        script_path = str(Path(__file__).resolve())

    cmd_parts = ["python3", script_path, subcommand]
    for key, value in args_dict.items():
        if key == "online_search" and value is False:
            cmd_parts.append("--no-online-search")
            continue
        if value is None or value is False:
            continue
        # Empty image quality uses the API default; do not emit a flag without a value.
        if key in ("quality", "background") and isinstance(value, str) and not value.strip():
            continue
        flag = f"--{key.replace('_', '-')}"
        if isinstance(value, bool) and value:
            cmd_parts.append(flag)
        elif isinstance(value, list):
            cmd_parts.append(flag)
            cmd_parts.extend(str(v) for v in value)
        elif isinstance(value, dict):
            cmd_parts.extend([flag, json.dumps(value, ensure_ascii=False)])
        else:
            cmd_parts.extend([flag, str(value)])

    cmd_str = shlex.join(cmd_parts)
    base_dir = str(Path(__file__).resolve().parent.parent)
    deliver_to_str = deliver_to or "<user_id>"
    deliver_channel_str = deliver_channel or "feishu"

    delivery_instructions = _build_delivery_instructions(
        deliver_to_str, deliver_channel_str, base_dir, subcommand,
    )

    label_text = args_dict.get("prompt", subcommand)
    if isinstance(label_text, str) and len(label_text) > 60:
        label_text = label_text[:60] + "..."

    task_text = (
        "You are an ArtClaw creative worker. Execute the following command "
        "and deliver the result to the user.\n\n"
        f"Command:\n```\n{cmd_str}\n```\n\n"
        "This command calls the ArtClaw API and waits for completion. "
        "Typical times: images ~30s, videos ~2-5min, workflows ~5-15min. "
        "Wait for the command to complete — do NOT background it.\n\n"
        "When the command finishes:\n"
        "- If the JSON output contains \"status\": \"success\":\n"
        "  Inspect the full result object, including nested arrays. Deliver every generated "
        "image, audio track, or video in result order; do not select only the first URL.\n"
        f"{delivery_instructions}\n"
        "  Share companion asset URLs too, including original/translated subtitles "
        "and speaker metadata when present.\n"
        "  Always include a short summary: what was generated, settings used.\n"
        "- If error indicates insufficient credits (402), inform the user and "
        f"guide them to top up at {TOPUP_URL}\n"
        "- If polling times out after a job_id was returned, do not resubmit. "
        "Tell the user the job is still submitted and use job-status for that job_id.\n"
        "- If submission reports outcome_unknown, do not run the command again; "
        "check billing and results first.\n"
        "- For other failures, report the error."
    )

    return {
        "sessions_spawn_args": {
            "task": task_text,
            "label": f"artclaw: {label_text}",
            "runTimeoutSeconds": SPAWN_TIMEOUT_SECONDS,
        },
        "command": cmd_str,
    }


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


# ---------------------------------------------------------------------------
# Section 9: CLI Command Handlers
# ---------------------------------------------------------------------------

# --- Generation commands ---

def cmd_generate_image(args, config: dict):
    """Generate an image from a text prompt."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    api_kwargs: Dict[str, Any] = {"prompt": args.prompt}
    api_kwargs.update(_collect_optional_args(args, [
        "aspect_ratio", "resolution", "model", "callback_url",
        "n", "seed", "output_format", "negative_prompt",
        "style", "guidance_scale", "quality", "background",
        "online_search", "moderation", "sequential_image_generation",
    ]))
    api_kwargs["team_id"] = _resolve_team_id(args, config)
    if getattr(args, "watermark", None):
        api_kwargs["watermark"] = True

    # Handle spawn mode differently: don't convert files to base64 yet
    if _should_spawn(args):
        # Pass reference URLs/files as-is to spawn task
        if args.reference_urls:
            api_kwargs["reference_urls"] = _prepare_reference_urls(
                args.reference_urls, [], "image", config, dry_run=True,
            )
        if getattr(args, "reference_files", None):
            api_kwargs["reference_files"] = [str(_local_media_path(p, "image"))
                                              for p in args.reference_files]

        result = build_spawn_task(
            "generate-image", api_kwargs,
            deliver_to=getattr(args, "deliver_to", None),
            deliver_channel=getattr(args, "deliver_channel", None),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Upload local image references before submitting the generation job.
    references = list(args.reference_urls or [])
    files = list(getattr(args, "reference_files", None) or [])
    _prepare_reference_urls(references, files, "image", config, dry_run=True)
    reference_urls = _prepare_reference_urls(
        references, files, "image", config, dry_run=dry_run,
    )

    if reference_urls:
        api_kwargs["reference_urls"] = reference_urls

    result = submit_and_poll(
        config, "generate-image", api_generate_image, api_kwargs,
        no_wait=args.no_wait, dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_generate_video(args, config: dict):
    """Generate a video from a text prompt."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    api_kwargs: Dict[str, Any] = {"prompt": args.prompt}
    api_kwargs.update(_collect_optional_args(args, [
        "aspect_ratio", "duration", "resolution", "model", "callback_url",
        "output_format", "seed", "online_search", "omni_reference_task_type",
    ]))
    _validate_video_output_format_model(
        api_kwargs.get("model"), api_kwargs.get("output_format"),
    )
    api_kwargs["team_id"] = _resolve_team_id(args, config)
    spawn = _should_spawn(args)
    content_items = _video_content_items(args, for_spawn=spawn)
    if content_items is not None:
        api_kwargs["content_items"] = _spawn_content_argument(content_items) if spawn else content_items

    # Handle spawn mode differently: don't convert files to base64 yet
    if spawn:
        # Pass reference URLs/files as-is to spawn task
        if args.reference_urls:
            api_kwargs["reference_urls"] = _prepare_reference_urls(
                args.reference_urls, [], "image", config, dry_run=True,
            )
        if getattr(args, "reference_files", None):
            api_kwargs["reference_files"] = [str(_local_media_path(p, "image"))
                                              for p in args.reference_files]
        if args.no_generate_audio:
            api_kwargs["no_generate_audio"] = True

        result = build_spawn_task(
            "generate-video", api_kwargs,
            deliver_to=getattr(args, "deliver_to", None),
            deliver_channel=getattr(args, "deliver_channel", None),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    api_kwargs["generate_audio"] = not args.no_generate_audio

    if content_items is not None:
        api_kwargs["content_items"] = _prepare_content_items(
            content_items, config, dry_run=dry_run,
        )

    # Legacy --reference-files remains image-only.
    references = list(args.reference_urls or [])
    files = list(getattr(args, "reference_files", None) or [])
    _prepare_reference_urls(references, files, "image", config, dry_run=True)
    reference_urls = _prepare_reference_urls(
        references, files, "image", config, dry_run=dry_run,
    )

    if reference_urls:
        api_kwargs["reference_urls"] = reference_urls

    result = submit_and_poll(
        config, "generate-video", api_generate_video, api_kwargs,
        no_wait=args.no_wait, dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_generate_audio(args, config: dict):
    """Generate audio (music, sound effects) via multi-platform providers."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    api_kwargs: Dict[str, Any] = {
        "provider": getattr(args, "provider", "suno") or "suno",
        "model": getattr(args, "model", "Suvo V4.5 ALL") or "Suvo V4.5 ALL",
    }
    api_kwargs.update(_collect_optional_args(args, [
        "prompt", "style", "title", "vocal_gender", "negative_tags",
        "style_weight", "weirdness_constraint", "audio_weight", "persona_id",
        "duration", "callback_url", "operation", "reference_audio_urls",
        "video_url", "video_id", "sound_effect_prompt", "bgm_prompt",
    ]))
    api_kwargs["team_id"] = _resolve_team_id(args, config)
    if getattr(args, "instrumental", None):
        api_kwargs["instrumental"] = True
    if getattr(args, "custom_mode", None):
        api_kwargs["custom_mode"] = True
    if getattr(args, "asmr_mode", None):
        api_kwargs["asmr_mode"] = True

    # Match REST's provider/input shape before uploading any local media.
    references = api_kwargs.get("reference_audio_urls") or []
    provider = api_kwargs["provider"]
    operation = api_kwargs.get("operation") or "generate"
    if provider == "suno":
        if api_kwargs.get("video_url") or api_kwargs.get("video_id"):
            raise ValueError("Suno does not accept Kling video input")
        expected = {"generate": 0, "upload_cover": 1, "mashup": 2}.get(operation)
        if expected is None:
            raise ValueError("unsupported Suno operation")
        if len(references) != expected:
            raise ValueError(f"{operation} requires {expected} reference audio URLs")
    elif provider == "kling":
        if operation != "generate" or references:
            raise ValueError("Kling does not accept Suno reference music")
        if bool(api_kwargs.get("video_url")) == bool(api_kwargs.get("video_id")):
            raise ValueError("Kling requires exactly one video_url or video_id")
    else:
        raise ValueError("unsupported audio provider")

    api_kwargs = _prepare_direct_media("generate-audio", api_kwargs, config, dry_run=True)
    if not _should_spawn(args) and not dry_run:
        api_kwargs = _prepare_direct_media("generate-audio", api_kwargs, config, dry_run=False)

    if _should_spawn(args):
        result = build_spawn_task(
            "generate-audio", api_kwargs,
            deliver_to=getattr(args, "deliver_to", None),
            deliver_channel=getattr(args, "deliver_channel", None),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    result = submit_and_poll(
        config, "generate-audio", api_generate_audio, api_kwargs,
        no_wait=args.no_wait, dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


# --- Text generation ---

def cmd_generate_text(args, config: dict):
    """Generate text / chat response from an LLM (sync by default)."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    api_kwargs: Dict[str, Any] = {"prompt": args.prompt}
    api_kwargs.update(_collect_optional_args(args, [
        "model", "provider", "system_instruction", "response_format",
        "callback_url",
    ]))
    api_kwargs["team_id"] = _resolve_team_id(args, config)

    # Multimodal reference (image/video/audio → text) is Gemi-only. Resolve the
    # display name to see whether it maps to a Gemini model; gate reference_parts
    # on that so non-Gemi models get a clear error instead of a backend 400.
    reference_parts = None
    if getattr(args, "reference_parts", None):
        model = api_kwargs.get("model", "deepseek-v4-pro")
        resolved = MODEL_ALIASES.get(model, model)
        if not resolved.startswith("gemini-"):
            print(json.dumps({
                "error": (
                    "Multimodal reference (--reference-parts) requires a Gemi "
                    "model (e.g. 'Gemi 3.0 Flash'). "
                    f"Got model {model!r}."
                ),
            }), file=sys.stderr)
            sys.exit(1)
        try:
            reference_parts = _build_reference_parts(
                args.reference_parts, config, dry_run=True,
            )
            if not getattr(args, "dry_run", False):
                reference_parts = _build_reference_parts(
                    args.reference_parts, config, dry_run=False,
                )
        except (ValueError, FileNotFoundError) as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)
        api_kwargs["reference_parts"] = reference_parts

    # Text is sync by default (no polling); only submit_and_poll if async
    result = api_generate_text(config, **api_kwargs, dry_run=dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))


# --- Seedance video ---

def cmd_generate_seedance_video(args, config: dict):
    """Generate video with Seedance 2.0/2.5 features."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    api_kwargs: Dict[str, Any] = {"prompt": args.prompt,
                                    "model": getattr(args, "model", "doubao-seedance-2-0-260128")}
    api_kwargs.update(_collect_optional_args(args, [
        "aspect_ratio", "duration", "resolution", "seed",
        "execution_expires_after", "safety_identifier", "priority",
        "callback_url", "output_format",
        "online_search", "watermark",
    ]))
    _validate_video_output_format_model(
        api_kwargs.get("model"), api_kwargs.get("output_format"),
    )
    content_items = _video_content_items(args)
    if content_items is not None:
        api_kwargs["content_items"] = _prepare_content_items(
            content_items, config, dry_run=dry_run,
        )
    # 默认生成音频，--no-generate-audio 禁用
    api_kwargs["generate_audio"] = not args.no_generate_audio
    if getattr(args, "return_last_frame", None):
        api_kwargs["return_last_frame"] = True

    result = submit_and_poll(
        config, "generate-seedance-video", api_generate_seedance_video, api_kwargs,
        no_wait=args.no_wait, dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


# --- Voice STT / TTS (async) ---

def _cmd_voice_async(args, config: dict, subcommand: str, api_fn, api_kwargs: dict):
    """Shared async voice handler: submit → poll."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    result = submit_and_poll(
        config, subcommand, api_fn, api_kwargs,
        no_wait=args.no_wait, dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_stt(args, config: dict):
    """Speech-to-text (async). Submit audio → poll for text."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)
    api_kwargs: Dict[str, Any] = {}
    if getattr(args, "audio_file", None):
        source = f"@{args.audio_file}"
    elif getattr(args, "audio", None):
        source = args.audio
    else:
        print(json.dumps({"error": "Either --audio or --audio-file is required"}), file=sys.stderr)
        sys.exit(1)
    if source.startswith("@"):
        source = _prepare_media_value(source, "audio", config, dry_run=dry_run, cache={})
    api_kwargs["audio"] = source
    if getattr(args, "callback_url", None):
        api_kwargs["callback_url"] = args.callback_url
    _cmd_voice_async(args, config, "stt", api_voice_stt, api_kwargs)


def cmd_tts(args, config: dict):
    """Text-to-speech (async). Submit text → poll for audio."""
    if not getattr(args, "text", None):
        print(json.dumps({"error": "--text is required"}), file=sys.stderr)
        sys.exit(1)
    api_kwargs: Dict[str, Any] = {"text": args.text}
    api_kwargs.update(_collect_optional_args(args, ["speaker", "callback_url"]))
    _cmd_voice_async(args, config, "tts", api_voice_tts, api_kwargs)


# --- Workflow commands ---

def cmd_list_workflows(args, config: dict):
    """List available preset workflows."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)
    result = api_list_workflows(config, dry_run=dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_run_workflow(args, config: dict):
    """Execute a preset workflow."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    try:
        inputs = json.loads(args.inputs)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "error": f"--inputs must be valid JSON: {e}",
            "hint": 'Example: --inputs \'{"prompt": "a cat"}\'',
        }), file=sys.stderr)
        sys.exit(1)
    if not isinstance(inputs, dict):
        raise ValueError("--inputs must be a JSON object")
    inputs = _prepare_workflow_inputs(inputs, config, dry_run=True, cache={})

    api_kwargs: Dict[str, Any] = {
        "workflow_id": args.workflow_id,
        "inputs": inputs,
    }
    api_kwargs.update(_collect_optional_args(args, ["timeout", "callback_url"]))
    api_kwargs["team_id"] = _resolve_team_id(args, config)

    if _should_spawn(args):
        result = build_spawn_task(
            "run-workflow", api_kwargs,
            deliver_to=getattr(args, "deliver_to", None),
            deliver_channel=getattr(args, "deliver_channel", None),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not dry_run:
        api_kwargs["inputs"] = _prepare_workflow_inputs(
            inputs, config, dry_run=False, cache={},
        )
    result = submit_and_poll(
        config, "run-workflow", api_run_workflow, api_kwargs,
        no_wait=args.no_wait, dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_run_workflow_dsl(args, config: dict):
    """Execute a workflow defined by DSL source (compiled server-side)."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)

    try:
        inputs = json.loads(args.inputs)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "error": f"--inputs must be valid JSON: {e}",
            "hint": 'Example: --inputs \'{"prompt": "a cat"}\'',
        }), file=sys.stderr)
        sys.exit(1)
    if not isinstance(inputs, dict):
        raise ValueError("--inputs must be a JSON object")
    inputs = _prepare_workflow_inputs(inputs, config, dry_run=True, cache={})

    if args.dsl and args.dsl_file:
        print(json.dumps({
            "error": "--dsl and --dsl-file are mutually exclusive",
            "hint": "Provide inline YAML via --dsl, or a path via --dsl-file, not both",
        }), file=sys.stderr)
        sys.exit(1)

    dsl = args.dsl
    if args.dsl_file:
        with open(args.dsl_file, "r", encoding="utf-8") as f:
            dsl = f.read()

    if not dsl:
        print(json.dumps({
            "error": "--dsl or --dsl-file is required",
            "hint": 'Provide inline YAML via --dsl, or a path via --dsl-file',
        }), file=sys.stderr)
        sys.exit(1)

    api_kwargs: Dict[str, Any] = {
        "dsl": dsl,
        "inputs": inputs,
    }
    api_kwargs.update(_collect_optional_args(args, ["timeout", "callback_url"]))
    api_kwargs["team_id"] = _resolve_team_id(args, config)

    if _should_spawn(args):
        result = build_spawn_task(
            "run-workflow-dsl", api_kwargs,
            deliver_to=getattr(args, "deliver_to", None),
            deliver_channel=getattr(args, "deliver_channel", None),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not dry_run:
        api_kwargs["inputs"] = _prepare_workflow_inputs(
            inputs, config, dry_run=False, cache={},
        )
    result = submit_and_poll(
        config, "run-workflow-dsl", api_run_workflow_from_dsl, api_kwargs,
        no_wait=args.no_wait, dry_run=dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


# --- Job management commands ---

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


# --- Account commands ---

def cmd_account_info(args, config: dict):
    """Show account balance and usage."""
    _check_api_key(config, allow_dry_run=getattr(args, "dry_run", False))
    result = api_account_info(config,
                              dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, indent=2, ensure_ascii=False))



def cmd_remove_bg(args, config: dict):
    """Remove background from an image."""
    _check_api_key(config, allow_dry_run=getattr(args, "dry_run", False))
    if not args.image:
        print(json.dumps({"error": "Must provide --image (base64 or @local-file)"}), file=sys.stderr)
        sys.exit(1)
    if args.image.startswith("@"):
        dry_run = getattr(args, "dry_run", False)
        image_url = _prepare_media_value(args.image, "image", config,
                                         dry_run=dry_run, cache={})
        payload = {"image_url": image_url}
        team_id = _resolve_team_id(args, config)
        if team_id:
            payload["team_id"] = team_id
        result = api_direct_operation(config, "remove-bg-image", payload,
                                      dry_run=dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    result = api_remove_bg(config, args.image,
                           team_id=_resolve_team_id(args, config),
                           dry_run=getattr(args, "dry_run", False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_direct_operation(args, config: dict):
    """Submit structured direct generation inputs from JSON or a UTF-8 JSON file."""
    dry_run = getattr(args, "dry_run", False)
    _check_api_key(config, allow_dry_run=dry_run)
    raw = args.input
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8-sig")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid --input JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("--input must be a JSON object")
    original_payload = dict(payload)
    operation = args.command
    if operation == "voice-clone":
        _validate_voice_clone_payload(payload)
    if operation in SYNC_DIRECT_OPERATIONS:
        audio = payload.get("audio_base64")
        audio_url = payload.get("audio_url")
        if audio and audio_url:
            raise ValueError("provide exactly one of audio_base64 or audio_url")
        local_audio = audio if isinstance(audio, str) and audio.startswith("@") else audio_url
        if isinstance(local_audio, str) and local_audio.startswith("@"):
            path = _local_media_path(local_audio[1:], "audio")
            if path.suffix.lower() not in {".mp3", ".wav", ".ogg", ".flac", ".m4a"}:
                raise ValueError("synchronous audio supports MP3, WAV, OGG, FLAC or M4A")
            if path.stat().st_size > 10_000_000:
                raise ValueError("local audio exceeds 10 MB for synchronous GPU operations")
            extension = path.suffix.lower().lstrip(".")
            if "audio_extension" in payload and payload["audio_extension"] != extension:
                raise ValueError("audio_extension must match the local audio file")
            payload["audio_extension"] = extension
        if isinstance(audio, str) and audio.startswith("@"):
            payload["audio_url"] = f"@{path}"
            payload.pop("audio_base64")
        if bool(payload.get("audio_base64")) == bool(payload.get("audio_url")):
            raise ValueError("provide exactly one of audio_base64 or audio_url")
    if operation in VIDEO_DIRECT_OPERATIONS:
        _validate_video_direct_payload(operation, payload, allow_local=True)
    payload = _prepare_direct_media(operation, payload, config, dry_run=True)
    if getattr(args, "callback_url", None):
        payload["callback_url"] = args.callback_url
    if _should_spawn(args):
        input_arg = args.input
        if payload != original_payload or not input_arg.startswith("@"):
            input_arg = _spawn_content_argument(payload)
        else:
            input_arg = "@" + str(Path(input_arg[1:]).resolve())
        task = build_spawn_task(
            operation, {"input": input_arg,
                        "team_id": _resolve_team_id(args, config)},
            deliver_to=getattr(args, "deliver_to", None),
            deliver_channel=getattr(args, "deliver_channel", None),
        )
        print(json.dumps(task, indent=2, ensure_ascii=False))
        return
    team_id = _resolve_team_id(args, config)
    if team_id:
        payload["team_id"] = team_id
    if not dry_run:
        payload = _prepare_direct_media(operation, payload, config, dry_run=False)
        if operation in VIDEO_DIRECT_OPERATIONS:
            _validate_video_direct_payload(operation, payload)
    if operation in SYNC_DIRECT_OPERATIONS:
        result = api_direct_operation(config, operation, payload, dry_run=dry_run)
        if dry_run:
            if "audio_base64" in payload:
                result["body"] = {
                    **result["body"],
                    "audio_base64": f"<redacted: {len(payload['audio_base64'])} base64 characters>",
                }
        elif operation == "voice-convert":
            audio, extension = _decode_audio_result(result.get("audio_data"), "wav")
            result = {"audio_file": _save_audio_result(audio, "voice-convert-", extension)}
        else:
            output_format = payload.get("output_format", "mp3")
            vocals, vocals_ext = _decode_audio_result(result.get("vocals"), output_format)
            other, other_ext = _decode_audio_result(result.get("other"), output_format)
            result = {
                "vocals_file": _save_audio_result(vocals, "vocals-", vocals_ext),
                "other_file": _save_audio_result(other, "other-", other_ext),
            }
    else:
        result = submit_and_poll(
            config, operation,
            lambda cfg, *, dry_run=False: api_direct_operation(cfg, operation, payload, dry_run=dry_run),
            {}, no_wait=args.no_wait, dry_run=dry_run,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))


def _validate_voice_clone_payload(payload: dict) -> None:
    """Check model/reference boundaries before any local upload or background spawn."""
    seed_controls = {"speech_rate", "loudness_rate", "pitch_rate", "output_format", "sample_rate"}
    index_controls = {"emo_alpha", "use_random", "use_emo_text", "emo_text", "emo_vector", "emo_audio_url"}
    allowed = {"engine", "text", "speaker", "reference_audio_urls", "team_id", "callback_url"} | seed_controls | index_controls
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unsupported voice-clone field: {sorted(unknown)[0]}")
    engine = payload.get("engine")
    if engine not in ("seed-audio", "indextts"):
        raise ValueError("voice-clone requires engine seed-audio or indextts")
    if not isinstance(payload.get("text"), str) or not payload["text"].strip():
        raise ValueError("voice-clone requires nonblank text")
    if engine == "seed-audio":
        payload["text"] = payload["text"].strip()
    speaker = payload.get("speaker")
    if speaker is not None:
        if not isinstance(speaker, str) or not speaker.strip():
            raise ValueError("speaker must be one nonblank string")
        payload["speaker"] = speaker.strip()
    urls = payload.get("reference_audio_urls", [])
    if not isinstance(urls, list) or not all(isinstance(url, str) and url.strip() for url in urls):
        raise ValueError("reference_audio_urls must be a list of audio URLs or @local-files")
    for key in ("use_random", "use_emo_text"):
        if key in payload and not isinstance(payload[key], bool):
            raise ValueError(f"{key} must be a boolean")
    for key in ("emo_text", "emo_vector", "emo_audio_url"):
        if key in payload and not isinstance(payload[key], str):
            raise ValueError(f"{key} must be a string")
    for url in urls + ([payload["emo_audio_url"]] if payload.get("emo_audio_url") else []):
        if url.startswith("@"):
            continue  # All local paths are checked in the dry media-preparation pass.
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host or parsed.username or parsed.password:
            raise ValueError("reference audio must use public HTTPS or @local-file")
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            raise ValueError("local audio hosts are not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("private audio hosts are not allowed")
    for key, lower, upper in (("speech_rate", -50, 100), ("loudness_rate", -50, 100), ("pitch_rate", -12, 12)):
        value = payload.get(key)
        if value is not None and (type(value) is not int or not lower <= value <= upper):
            raise ValueError(f"{key} must be an integer between {lower} and {upper}")
    if payload.get("output_format") not in (None, "wav", "mp3", "pcm", "ogg_opus"):
        raise ValueError("output_format must be wav, mp3, pcm or ogg_opus")
    if payload.get("sample_rate") not in (None, 8000, 16000, 24000, 32000, 48000):
        raise ValueError("unsupported sample_rate")
    if payload.get("output_format") == "ogg_opus" and payload.get("sample_rate") not in (None, 48000):
        raise ValueError("ogg_opus requires 48000Hz")
    alpha = payload.get("emo_alpha")
    if alpha is not None and (not _finite_number(alpha) or not 0.1 <= alpha <= 2):
        raise ValueError("emo_alpha must be between 0.1 and 2")
    if payload.get("emo_vector"):
        try:
            vector = [float(value) for value in payload["emo_vector"].split(",")]
        except ValueError as exc:
            raise ValueError("emo_vector requires eight numbers between 0 and 1") from exc
        if len(vector) != 8 or any(not math.isfinite(value) or not 0 <= value <= 1 for value in vector):
            raise ValueError("emo_vector requires eight numbers between 0 and 1")
    if engine == "indextts":
        if speaker is not None or len(urls) != 1:
            raise ValueError("IndexTTS requires exactly one reference audio and does not support speaker")
        if any(payload.get(key) is not None for key in seed_controls):
            raise ValueError("Seed Audio controls are not valid for IndexTTS")
    else:
        if not 1 <= len(urls) + int(speaker is not None) <= 3:
            raise ValueError("Seed Audio requires 1–3 references in total: speaker + audio <= 3")
        if payload.get("emo_alpha") is not None or any(payload.get(key) for key in index_controls - {"emo_alpha"}):
            raise ValueError("IndexTTS emotion controls are not valid for Seed Audio")
        text = payload["text"]
        assembled = text if "@音频" in text else f"@音频1 说：“{text}”"
        if len(assembled) > 3000:
            raise ValueError("Seed Audio assembled prompt must not exceed 3000 characters")
        count = len(urls) + int(speaker is not None)
        if any(not 1 <= int(index) <= count for index in re.findall(r"@音频([0-9]+)", text)):
            raise ValueError("Seed Audio reference index must address speaker first, then audio URLs (1–3)")
        for url in urls:
            if url.startswith("@") and _local_media_path(url[1:], "audio").stat().st_size > 10 * 1024 * 1024:
                raise ValueError("Seed Audio reference audio must not exceed 10 MiB")


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _timeline_media_url(value: Any) -> bool:
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


def _validate_video_direct_payload(operation: str, payload: dict, *,
                                   allow_local: bool = False) -> None:
    """Reject obvious invalid video tasks before spawning or submitting a paid POST."""
    def valid_source(value):
        return _timeline_media_url(value) or (
            allow_local and isinstance(value, str) and value.startswith("@")
        )

    if operation == "video-edit":
        allowed = {"source_video_url", "prompt", "start", "end", "resolution",
                   "output_format", "generate_audio", "reference_image_urls",
                   "reference_video_urls", "reference_audio_urls", "team_id", "callback_url"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unsupported video-edit field: {sorted(unknown)[0]}")
        if not valid_source(payload.get("source_video_url")):
            raise ValueError("video-edit requires an https://assets.vicoo.ai source_video_url")
        for field in ("reference_image_urls", "reference_video_urls", "reference_audio_urls"):
            urls = payload.get(field, [])
            if not isinstance(urls, list) or len(urls) > 10 or not all(valid_source(url) for url in urls):
                raise ValueError(f"{field} must contain at most 10 platform asset URLs or @local-files")
        if not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip():
            raise ValueError("video-edit requires a non-empty prompt")
        start, end = payload.get("start"), payload.get("end")
        if not _finite_number(start) or not _finite_number(end) or start < 0 or not 4 <= end - start <= 30:
            raise ValueError("video-edit interval must be 4 to 30 seconds")
        if payload.get("resolution", "720p") not in {"480p", "720p", "1080p"}:
            raise ValueError("unsupported video-edit resolution")
        if payload.get("output_format", "source") not in {"source", "mp4", "mov"}:
            raise ValueError("unsupported video-edit output_format")
        if "generate_audio" in payload and not isinstance(payload["generate_audio"], bool):
            raise ValueError("video-edit generate_audio must be boolean")
        return
    if operation == "video-extend":
        allowed = {"source_video_url", "prompt", "resolution", "output_format", "generate_audio",
                   "reference_image_urls", "reference_video_urls", "reference_audio_urls",
                   "team_id", "callback_url"}
        if operation == "video-extend":
            allowed.add("duration")
        unknown = set(payload) - allowed - {"start", "end", "segments", "cut", "duration"}
        if unknown:
            raise ValueError(f"unsupported {operation} field: {sorted(unknown)[0]}")
        if not isinstance(payload.get("source_video_url"), str) or not payload["source_video_url"]:
            raise ValueError(f"{operation} requires source_video_url")
        if not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip():
            raise ValueError(f"{operation} requires a non-empty prompt")
        if any(key in payload for key in ("start", "end", "segments", "cut")):
            raise ValueError("interval AI retake is not whole-video edit or timeline cutting")
        if operation == "video-extend":
            duration = payload.get("duration", 5)
            if isinstance(duration, bool) or not isinstance(duration, int) or not 4 <= duration <= 30:
                raise ValueError("video-extend duration must be an integer from 4 to 30 seconds")
        return

    unknown = set(payload) - {"cut", "video_clips", "team_id", "callback_url"}
    if unknown:
        raise ValueError(f"unsupported video-timeline field: {sorted(unknown)[0]}")
    cut = payload.get("cut")
    clips = payload.get("video_clips", [])
    if cut is not None:
        if not isinstance(cut, dict) or clips:
            raise ValueError("video-timeline cut must not be combined with clips")
        unknown = set(cut) - {"source_video_url", "source_duration", "start", "end"}
        if unknown:
            raise ValueError(f"unsupported video-timeline cut field: {sorted(unknown)[0]}")
        if not valid_source(cut.get("source_video_url")):
            raise ValueError("video-timeline cut requires an https://assets.vicoo.ai source_video_url")
        start, end, duration = cut.get("start"), cut.get("end"), cut.get("source_duration")
        if not _finite_number(start) or not _finite_number(end):
            raise ValueError("video-timeline cut boundaries must be finite numbers")
        if duration is not None and (not _finite_number(duration) or duration <= 0):
            raise ValueError("video-timeline source_duration must be a positive finite number")
        if not (0 <= start < end) or (duration is not None and (end > duration or (start == 0 and end == duration))):
            raise ValueError("video-timeline cut must leave some source video")
    elif not isinstance(clips, list) or not clips:
        raise ValueError("video-timeline requires cut or non-empty video_clips")
    else:
        for clip in clips:
            if not isinstance(clip, dict) or not valid_source(clip.get("url")):
                raise ValueError("video-timeline video clip requires an https://assets.vicoo.ai url")
            unknown = set(clip) - {"url", "trim_start", "trim_end"}
            if unknown:
                raise ValueError(f"unsupported video clip field: {sorted(unknown)[0]}")


def _decode_audio_result(value: Any, default_extension: str) -> Tuple[bytes, str]:
    """Decode a synchronous GPU audio response without exposing it in CLI output."""
    if not isinstance(value, str) or not value:
        raise ArtClawApiError("GPU returned no audio data", code="invalid_audio_result")
    extension = default_extension
    if value.startswith("data:"):
        header, separator, value = value.partition(",")
        match = re.fullmatch(r"data:audio/([\w.+-]+);base64", header, re.IGNORECASE)
        if not separator or not match:
            raise ArtClawApiError("GPU returned an invalid audio data URI", code="invalid_audio_result")
        extension = {
            "wav": "wav", "x-wav": "wav", "wave": "wav",
            "mpeg": "mp3", "mp3": "mp3", "ogg": "ogg",
            "flac": "flac", "x-flac": "flac", "mp4": "m4a",
            "x-m4a": "m4a",
        }.get(match.group(1).lower(), default_extension)
    try:
        audio = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArtClawApiError("GPU returned invalid Base64 audio", code="invalid_audio_result") from exc
    if not audio:
        raise ArtClawApiError("GPU returned empty audio data", code="invalid_audio_result")
    return audio, extension


def _save_audio_result(audio: bytes, prefix: str, extension: str) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=prefix, suffix=f".{extension}", dir=OUTPUT_DIR, delete=False,
    ) as handle:
        handle.write(audio)
        return str(Path(handle.name).resolve())


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

# --- Config commands ---

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


# --- Spawn task command ---

def cmd_spawn_task(args, config: dict):
    """Build sessions_spawn payload for async generation."""
    _check_api_key(config)

    quality = getattr(args, "quality", None)
    if quality is not None and args.subcommand != "generate-image":
        print(json.dumps({"error": "--quality is only supported for generate-image"}), file=sys.stderr)
        sys.exit(1)

    background = getattr(args, "background", None)
    if background is not None and args.subcommand != "generate-image":
        print(json.dumps({"error": "--background is only supported for generate-image"}), file=sys.stderr)
        sys.exit(1)

    args_dict: Dict[str, Any] = {"prompt": args.prompt}
    args_dict.update(_collect_optional_args(args, [
        "aspect_ratio", "resolution", "duration", "model",
        "workflow_id", "dsl", "inputs", "quality", "background",
    ]))
    if args.dsl_file:
        args_dict["dsl_file"] = str(Path(args.dsl_file).resolve())
    if args.subcommand in {"run-workflow", "run-workflow-dsl"}:
        args_dict["team_id"] = _resolve_team_id(args, config)
    if args.inputs and args.subcommand in {"run-workflow", "run-workflow-dsl"}:
        try:
            workflow_inputs = json.loads(args.inputs)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--inputs must be valid JSON: {exc}") from exc
        if not isinstance(workflow_inputs, dict):
            raise ValueError("--inputs must be a JSON object")
        args_dict["inputs"] = json.dumps(_prepare_workflow_inputs(
            workflow_inputs, config, dry_run=True, cache={},
        ), ensure_ascii=False)
    if args.reference_urls:
        args_dict["reference_urls"] = _prepare_reference_urls(
            args.reference_urls, [], "image", config, dry_run=True,
        )
    if args.no_wait:
        args_dict["no_wait"] = True

    result = build_spawn_task(
        args.subcommand,
        args_dict,
        deliver_to=getattr(args, "deliver_to", None),
        deliver_channel=getattr(args, "deliver_channel", None),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


# --- Self-update ---

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


# --- History commands ---

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


# ---------------------------------------------------------------------------
# Section 10: CLI Parser
# ---------------------------------------------------------------------------

def _add_wait_and_dry_run(parser):
    """Add --no-wait and --dry-run flags to async command parsers."""
    parser.add_argument("--no-wait", action="store_true", default=False,
                        help="Return immediately after submission (don't poll)")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Print what would be sent without making API calls")
    parser.add_argument("--callback-url", default=None,
                        help="Webhook URL for completion notification")


def _add_dry_run(parser):
    """Add --dry-run flag to sync command parsers."""
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Print what would be sent without making API calls")


def _add_online_search(parser):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--online-search", dest="online_search", action="store_true",
                       default=None, help="Enable web search (supported models only)")
    group.add_argument("--no-online-search", dest="online_search", action="store_false",
                       help="Explicitly disable web search")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="artclaw",
        description="ArtClaw AI Creative Suite CLI — agent-friendly API client",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # --- generate-image ---
    p = sub.add_parser("generate-image", help="Generate image from prompt")
    p.add_argument("--prompt", required=True, help="Text description of the image")
    p.add_argument("--aspect-ratio", default=None,
                   type=_aspect_ratio,
                   help="Aspect ratio (default: 16:9)")
    p.add_argument("--resolution", default=None,
                   choices=sorted(VALID_RESOLUTIONS_IMAGE),
                   help="Resolution: 512, 1K, 2K, 3K, 4K (model-dependent)")
    p.add_argument("--reference-urls", nargs="+", default=None,
                   help="Reference images (HTTPS URLs, base64 data URIs or @local-file)")
    p.add_argument("--reference-files", nargs="+", default=None,
                   help="Reference images (local files uploaded to Creator before generation)")
    p.add_argument("--model", default=None, help="Model ID override")
    p.add_argument("--n", type=int, choices=[1, 2, 4], default=None,
                   help="Output images per job: 1, 2 or 4")
    p.add_argument("--quality", default=None,
                   help="GT-Image-2.5 / GT-Image-2.5-Flash: low/medium/high/xhigh/max (default: high); "
                        "GT-Image-2: auto/low/medium/high. Validated by the API.")
    p.add_argument("--background", default=None,
                   help="GT-Image family background mode: auto/opaque/transparent. "
                        "transparent needs an alpha-capable output format (png/webp), not jpeg. "
                        "Validated by the API.")
    p.add_argument("--seed", type=int, default=None, help="Random seed (null=random)")
    p.add_argument("--output-format", default=None, help="Output format: png/jpeg/webp")
    p.add_argument("--negative-prompt", default=None, help="Legacy option; ignored by current REST")
    p.add_argument("--style", default=None, help="Legacy option; ignored by current REST")
    _add_online_search(p)
    p.add_argument("--moderation", choices=["auto", "low"], default=None,
                   help="GT-Image moderation mode")
    p.add_argument("--sequential-image-generation", choices=["auto", "disabled"], default=None,
                   help="Seedream sequential image generation strategy")
    p.add_argument("--guidance-scale", type=float, default=None,
                   help="Text weight 1-10 (Seedance image models)")
    p.add_argument("--watermark", action="store_true", default=False,
                   help="Add explicit watermark (Seedance)")
    p.add_argument("--team-id", default=None,
                   help="Team UUID — bill to the team's credits instead of the account")
    p.add_argument("--spawn", action="store_true", default=False,
                   help="Output sessions_spawn_args payload instead of running directly")
    p.add_argument("--deliver-to", default=None,
                   help="Delivery target (ou_xxx, chat_id, channel_id) — used with --spawn")
    p.add_argument("--deliver-channel", default=None,
                   choices=["feishu", "telegram", "discord"],
                   help="Delivery channel — used with --spawn")
    _add_wait_and_dry_run(p)

    # --- generate-video ---
    p = sub.add_parser("generate-video", help="Generate video from prompt")
    p.add_argument("--prompt", required=True, help="Text description of the video")
    p.add_argument("--aspect-ratio", default=None,
                   type=_aspect_ratio,
                   help="Aspect ratio (default: 16:9)")
    p.add_argument("--duration", type=int, default=None,
                   help="Video duration in seconds (model-dependent; Seedance 2.0: 4-15, Seedance 2.5: 4-30)")
    p.add_argument("--resolution", default=None,
                   choices=sorted(VALID_RESOLUTIONS_VIDEO),
                   help="Resolution: 480p, 720p, 1080p, 4k (model-dependent)")
    p.add_argument("--content-items", default=None,
                   help="Ordered role/url JSON or @JSON-file; all media roles accept @local-file uploads")
    p.add_argument("--seed", type=int, default=None, help="Random seed")
    _add_online_search(p)
    p.add_argument("--omni-reference-task-type", choices=["auto", "reference", "edit", "extend"],
                   default=None, help="Seedance 2.5 reference task; edit allows duration -1 and adaptive ratio")
    p.add_argument("--reference-urls", nargs="+", default=None,
                   help="Reference images for I2V (HTTPS URLs, base64 or @local-file)")
    p.add_argument("--reference-files", nargs="+", default=None,
                   help="Reference images for I2V (local files uploaded to Creator)")
    p.add_argument("--model", default=None, help="Model ID override")
    p.add_argument("--output-format", default=None, choices=["mp4", "mov"],
                   help="Output container for Seedance 2.5: mp4 or mov (default: mp4)")
    p.add_argument("--no-generate-audio", action="store_true", default=False,
                   help="Disable generated audio/BGM (video audio is enabled by default)")
    p.add_argument("--team-id", default=None,
                   help="Team UUID — bill to the team's credits instead of the account")
    p.add_argument("--spawn", action="store_true", default=False,
                   help="Output sessions_spawn_args payload instead of running directly")
    p.add_argument("--deliver-to", default=None,
                   help="Delivery target (ou_xxx, chat_id, channel_id) — used with --spawn")
    p.add_argument("--deliver-channel", default=None,
                   choices=["feishu", "telegram", "discord"],
                   help="Delivery channel — used with --spawn")
    _add_wait_and_dry_run(p)

    # --- generate-audio ---
    p = sub.add_parser("generate-audio",
                       help="Generate standalone audio/BGM")
    p.add_argument("--prompt", default=None, help="Music description or lyrics")
    p.add_argument("--provider", default="suno", help="Audio provider (default: suno)")
    p.add_argument("--model", default="Suvo V4.5 ALL", help="Model name (default: Suvo V4.5 ALL)")
    p.add_argument("--instrumental", action="store_true", default=False,
                   help="Instrumental only, no vocals")
    p.add_argument("--custom-mode", action="store_true", default=False,
                   help="Custom mode for precise control")
    p.add_argument("--style", default=None, help="Music style: pop, rock, jazz, etc.")
    p.add_argument("--title", default=None, help="Music title")
    p.add_argument("--vocal-gender", default=None, choices=["m", "f"],
                   help="Vocal gender: m/f")
    p.add_argument("--negative-tags", default=None, help="Style tags to exclude")
    p.add_argument("--style-weight", type=float, default=None, help="Style weight 0-1")
    p.add_argument("--weirdness-constraint", type=float, default=None,
                   help="Weirdness constraint 0-1")
    p.add_argument("--audio-weight", type=float, default=None, help="Audio weight 0-1")
    p.add_argument("--persona-id", default=None, help="Persona ID for voice cloning")
    p.add_argument("--duration", type=int, default=None,
                   help="Audio duration in seconds (10-360, Suvo V5.5 custom mode only)")
    p.add_argument("--operation", choices=["generate", "upload_cover", "mashup"], default=None)
    p.add_argument("--reference-audio-urls", nargs="+", default=None)
    p.add_argument("--video-url", default=None)
    p.add_argument("--video-id", default=None)
    p.add_argument("--sound-effect-prompt", default=None)
    p.add_argument("--bgm-prompt", default=None)
    p.add_argument("--asmr-mode", action="store_true", default=False)
    p.add_argument("--team-id", default=None,
                   help="Team UUID — bill to the team's credits instead of the account")
    p.add_argument("--spawn", action="store_true", default=False,
                   help="Output sessions_spawn_args payload instead of running directly")
    p.add_argument("--deliver-to", default=None,
                   help="Delivery target (ou_xxx, chat_id, channel_id) — used with --spawn")
    p.add_argument("--deliver-channel", default=None,
                   choices=["feishu", "telegram", "discord"],
                   help="Delivery channel — used with --spawn")
    _add_wait_and_dry_run(p)


    # --- remove-bg ---
    p = sub.add_parser("remove-bg", help="Remove background from an image")
    p.add_argument("--image", default=None, help="Base64-encoded image data")
    p.add_argument("--team-id", default=None,
                   help="Team UUID — bill to the team's credits instead of the account")
    _add_dry_run(p)

    for command in DIRECT_OPERATIONS:
        p = sub.add_parser(command, help=f"Direct {command} generation operation")
        p.add_argument("--input", required=True,
                       help="JSON object or @UTF-8-JSON-file with operation-specific fields")
        p.add_argument("--team-id", default=None,
                       help="Team UUID; overrides ARTCLAW_TEAM_ID and config")
        if command in SYNC_DIRECT_OPERATIONS:
            _add_dry_run(p)
        else:
            _add_wait_and_dry_run(p)
            p.add_argument("--spawn", action="store_true", default=False)
            p.add_argument("--deliver-to", default=None)
            p.add_argument("--deliver-channel", default=None,
                           choices=["feishu", "telegram", "discord"])

    # --- generate-text ---
    p = sub.add_parser("generate-text", help="Generate text / chat response from an LLM")
    p.add_argument("--prompt", required=True, help="Text prompt / instruction")
    p.add_argument("--model", default="deepseek-v4-pro",
                   choices=sorted(VALID_TEXT_MODELS),
                   help="Model ID (default: deepseek-v4-pro). Gemi models support multimodal input.")
    p.add_argument("--provider", default="deepseek",
                   choices=["deepseek"],
                   help="LLM provider (default: deepseek)")
    p.add_argument("--system-instruction", default=None, help="System prompt")
    p.add_argument("--response-format", default=None,
                   choices=["text", "json_object"], help="Output format")
    p.add_argument("--reference-parts", nargs="+", default=None, metavar="TYPE=URL|@FILE",
                   help=("Multimodal reference (Gemi models only). "
                         "One or more 'type=url' or 'type=@file' specs, "
                         "e.g. 'video=@./clip.mp4' or 'image=https://example.com/a.png'. "
                         "Local media is uploaded to Creator before generation."))
    p.add_argument("--callback-url", default=None,
                   help="If set, makes generation async → returns job_id")
    p.add_argument("--team-id", default=None,
                   help="Team UUID — bill to the team's credits instead of the account")
    _add_dry_run(p)

    # --- generate-seedance-video ---
    p = sub.add_parser("generate-seedance-video",
                       help="Generate video with Seedance 2.0/2.5 features")
    p.add_argument("--prompt", required=True, help="Video description")
    p.add_argument("--model", default="doubao-seedance-2-0-260128",
                   choices=["doubao-seedance-2-0-260128",
                            "doubao-seedance-2-0-fast-260128",
                            "doubao-seedance-2-0-mini-260615",
                            "doubao-seedance-2-5-260628"],
                   help="Seedance 2.0/2.5 model")
    p.add_argument("--aspect-ratio", default="16:9",
                   type=_aspect_ratio,
                   help="Aspect ratio")
    p.add_argument("--duration", type=int, default=5,
                   help="Duration in seconds (Seedance 2.0: 4-15; Seedance 2.5: 4-30)")
    p.add_argument("--resolution", default="720p",
                   choices=sorted(VALID_RESOLUTIONS_VIDEO),
                   help="Resolution")
    p.add_argument("--content-items", default=None,
                   help="Ordered role/url JSON or @JSON-file; all media roles accept @local-file uploads")
    _add_online_search(p)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--watermark", action="store_true", default=None)
    group.add_argument("--no-watermark", dest="watermark", action="store_false")
    p.add_argument("--output-format", default=None, choices=["mp4", "mov"],
                   help="Output container for Seedance 2.5: mp4 or mov (default: mp4)")
    p.add_argument("--seed", type=int, default=None, help="Random seed")
    p.add_argument("--no-generate-audio", action="store_true", default=False,
                   help="Disable audio generation")
    p.add_argument("--return-last-frame", action="store_true", default=False,
                   help="Return the generated video's last frame image")
    p.add_argument("--execution-expires-after", type=int, default=None,
                   help="Task timeout in seconds (3600-259200)")
    p.add_argument("--safety-identifier", default=None,
                   help="End-user unique identifier for safety monitoring")
    p.add_argument("--priority", type=int, default=0, help="Execution priority 0-9")
    _add_wait_and_dry_run(p)

    # --- stt (voice → text) ---
    p = sub.add_parser("stt", help="Speech-to-text (async) — submit audio, poll for text")
    p.add_argument("--audio", default=None,
                   help="Audio data, public URL or @local-file")
    p.add_argument("--audio-file", default=None, help="Local audio file uploaded before STT")
    _add_wait_and_dry_run(p)

    # --- tts (text → voice) ---
    p = sub.add_parser("tts", help="Text-to-speech (async) — submit text, poll for audio")
    p.add_argument("--text", required=True, help="Text to synthesize")
    p.add_argument("--speaker", default="en_male_tim_uranus_bigtts",
                   help="Speaker ID (default: en_male_tim_uranus_bigtts)")
    _add_wait_and_dry_run(p)

    # --- list-workflows ---
    p = sub.add_parser("list-workflows", help="List available preset workflows")
    _add_dry_run(p)

    # --- run-workflow ---
    p = sub.add_parser("run-workflow", help="Execute a preset workflow")
    p.add_argument("--workflow-id", required=True, help="Workflow ID to run")
    p.add_argument("--inputs", required=True,
                   help='Workflow inputs as JSON string (e.g. \'{"prompt": "a cat"}\')')
    p.add_argument("--timeout", type=int, default=None,
                   help="Workflow timeout in seconds")
    p.add_argument("--team-id", default=None,
                   help="Team UUID — bill workflow generation to the team's credits")
    p.add_argument("--spawn", action="store_true", default=False,
                   help="Output sessions_spawn_args payload instead of running directly")
    p.add_argument("--deliver-to", default=None,
                   help="Delivery target (ou_xxx, chat_id, channel_id) — used with --spawn")
    p.add_argument("--deliver-channel", default=None,
                   choices=["feishu", "telegram", "discord"],
                   help="Delivery channel — used with --spawn")
    _add_wait_and_dry_run(p)

    # --- run-workflow-dsl ---
    p = sub.add_parser("run-workflow-dsl",
                       help="Execute a workflow defined by DSL source (server-side compile)")
    p.add_argument("--dsl", default=None,
                   help="Workflow DSL YAML source (inline)")
    p.add_argument("--dsl-file", default=None,
                   help="Path to a workflow DSL YAML file")
    p.add_argument("--inputs", required=True,
                   help='Workflow inputs as JSON string (e.g. \'{"prompt": "a cat"}\')')
    p.add_argument("--timeout", type=int, default=None,
                   help="Workflow timeout in seconds")
    p.add_argument("--team-id", default=None,
                   help="Team UUID — bill workflow generation to the team's credits")
    p.add_argument("--spawn", action="store_true", default=False,
                   help="Output sessions_spawn_args payload instead of running directly")
    p.add_argument("--deliver-to", default=None,
                   help="Delivery target (ou_xxx, chat_id, channel_id) — used with --spawn")
    p.add_argument("--deliver-channel", default=None,
                   choices=["feishu", "telegram", "discord"],
                   help="Delivery channel — used with --spawn")
    _add_wait_and_dry_run(p)

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

    # --- spawn-task ---
    p = sub.add_parser("spawn-task",
                       help="Build sessions_spawn payload for async agent execution")
    p.add_argument("--subcommand", required=True,
                   choices=["generate-image", "generate-video",
                            "generate-audio", "run-workflow",
                            "run-workflow-dsl"],
                   help="Which generation command to spawn")
    p.add_argument("--prompt", default=None, help="Generation prompt")
    p.add_argument("--aspect-ratio", default=None, help="Aspect ratio")
    p.add_argument("--resolution", default=None, help="Resolution")
    p.add_argument("--duration", type=int, default=None,
                   help="Video duration (seconds)")
    p.add_argument("--model", default=None, help="Model ID override")
    p.add_argument("--workflow-id", default=None, help="Workflow ID")
    p.add_argument("--dsl", default=None, help="Workflow DSL YAML source")
    p.add_argument("--dsl-file", default=None, help="Workflow DSL YAML file")
    p.add_argument("--quality", default=None,
                   help="Image generation only; GT-Image-2.5 / GT-Image-2.5-Flash: "
                        "low/medium/high/xhigh/max (default: high); GT-Image-2: auto/low/medium/high")
    p.add_argument("--background", default=None,
                   help="Image generation only; GT-Image family background mode: auto/opaque/transparent")
    p.add_argument("--inputs", default=None, help="Workflow inputs (JSON)")
    p.add_argument("--team-id", default=None,
                   help="Team UUID for workflow billing")
    p.add_argument("--reference-urls", nargs="+", default=None,
                   help="Reference URLs")
    p.add_argument("--no-wait", action="store_true", default=False,
                   help="Don't wait for completion in spawned task")
    p.add_argument("--deliver-to", default=None,
                   help="Delivery target (ou_xxx, chat_id, channel_id)")
    p.add_argument("--deliver-channel", default=None,
                   choices=["feishu", "telegram", "discord"],
                   help="Delivery channel")

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


# ---------------------------------------------------------------------------
# Section 11: Command Map & Main Entry Point
# ---------------------------------------------------------------------------

COMMAND_MAP = {
    "remove-bg": cmd_remove_bg,
    "generate-image": cmd_generate_image,
    "generate-video": cmd_generate_video,
    "generate-seedance-video": cmd_generate_seedance_video,
    "generate-audio": cmd_generate_audio,
    "generate-text": cmd_generate_text,
    "stt": cmd_stt,
    "tts": cmd_tts,
    "list-workflows": cmd_list_workflows,
    "run-workflow": cmd_run_workflow,
    "run-workflow-dsl": cmd_run_workflow_dsl,
    "job-status": cmd_job_status,
    "list-jobs": cmd_list_jobs,
    "list-voices": cmd_list_voices,
    "cancel-job": cmd_cancel_job,
    "account-info": cmd_account_info,
    "verify-key": cmd_verify_key,
    "config": cmd_config,
    "config-init": cmd_config_init,
    "spawn-task": cmd_spawn_task,
    "last-job": cmd_last_job,
    "history": cmd_history,
    "self-update": cmd_self_update,
    **{name: cmd_direct_operation for name in DIRECT_OPERATIONS},
}


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    config = _get_config()

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
        if args.command in DIRECT_OPERATIONS and (e.status_code >= 500 or e.code == "network_error"):
            print(json.dumps({
                "error": "outcome_unknown", "message": str(e),
                "retryable": False, "do_not_resubmit": True,
                "guidance": "Check billing and result before manually submitting again.",
            }, indent=2, ensure_ascii=False), file=sys.stderr)
            sys.exit(1)
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
