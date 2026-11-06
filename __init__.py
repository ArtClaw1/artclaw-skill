"""ARTCLAW AI Creative Suite — Skill Package

This package provides AI content creation capabilities via the ARTCLAW REST API,
including image generation, video generation, workflow execution,
multimodal analysis, and prompt enhancement.

REST API Base: https://artclaw.com/api/v1
Auth: API Key (starts with vk_) passed as `X-API-KEY` HTTP header
Get your key: https://artclaw.com/settings
"""

SKILL_NAME = "artclaw-creative-suite"
SKILL_VERSION = "2.0.0"
API_BASE_URL = "https://artclaw.com/api/v1"

API_CONFIG = {
    "base_url": API_BASE_URL,
    "auth_header": "X-API-KEY",
}

ENDPOINTS = {
    "upload_asset": ("POST", "/assets/upload"),
    "list_voices": ("GET", "/voices"),
    "search_nodes": ("GET", "/workflows/nodes"),
    "describe_node": ("GET", "/workflows/nodes/{node_type}"),
    "workflow_guide": ("GET", "/workflows/dsl-guide"),
    "workflow_templates": ("GET", "/workflows/templates"),
    "workflow_template": ("GET", "/workflows/templates/{name}"),
    "validate_workflow": ("POST", "/workflows/validate"),
    "run_workflow": ("POST", "/workflows/run"),
    "recover_workflow": ("GET", "/workflows/requests/{request_id}"),
    "get_job": ("GET", "/jobs/{job_id}"),
    "list_jobs": ("GET", "/jobs/"),
    "cancel_job": ("POST", "/jobs/{job_id}/cancel"),
    "account_info": ("GET", "/account/info"),
    "verify_key": ("POST", "/auth/verify"),
}


def get_api_config() -> dict:
    """Return the REST API configuration for this skill."""
    return API_CONFIG
