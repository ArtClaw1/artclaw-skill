"""ARTCLAW AI Creative Suite — Skill Package

This package provides AI content creation capabilities via the ARTCLAW REST API,
including image generation, video generation, workflow execution,
multimodal analysis, and prompt enhancement.

REST API Base: https://artclaw.com/api/v1
Auth: API Key (starts with vk_) passed as `X-API-KEY` HTTP header
Get your key: https://artclaw.com/settings
"""

SKILL_NAME = "artclaw-creative-suite"
SKILL_VERSION = "1.5.0"
API_BASE_URL = "https://artclaw.com/api/v1"

API_CONFIG = {
    "base_url": API_BASE_URL,
    "auth_header": "X-API-KEY",
}

ENDPOINTS = {
    # Generation (async, returns job_id)
    "generate_image": ("POST", "/generate/image"),
    "generate_video": ("POST", "/generate/video"),
    "generate_seedance_video": ("POST", "/volcengine/video"),
    "generate_audio": ("POST", "/generate/audio"),
    "generate_text": ("POST", "/generate/text"),
    "remove_bg": ("POST", "/generate/remove-bg"),
    "remove_bg_image": ("POST", "/generate/remove-bg/image"),
    "remove_bg_video": ("POST", "/generate/remove-bg/video"),
    "voice_clone": ("POST", "/generate/voice/clone"),
    "voice_convert": ("POST", "/generate/voice/convert"),
    "stem_separate": ("POST", "/generate/voice/separate"),
    "video_retalk": ("POST", "/generate/video-retalk"),
    "image_enhance": ("POST", "/generate/image-enhance"),
    "smart_erase": ("POST", "/generate/smart-erase"),
    "smart_subtitles": ("POST", "/generate/smart-subtitles"),
    "ai_dubbing": ("POST", "/generate/ai-dubbing"),
    "video_enhance": ("POST", "/generate/video-enhance"),
    # Voice (async)
    "voice_stt": ("POST", "/generate/voice/stt"),
    "voice_tts": ("POST", "/generate/voice/tts"),
    # Workflows
    "list_workflows": ("GET", "/workflows/"),
    "run_workflow": ("POST", "/workflows/{workflow_id}/run"),
    # Job management
    "get_job": ("GET", "/jobs/{job_id}"),
    "list_jobs": ("GET", "/jobs/"),
    "cancel_job": ("POST", "/jobs/{job_id}/cancel"),
    # Account
    "account_info": ("GET", "/account/info"),
    # Auth
    "verify_key": ("POST", "/auth/verify"),
}


def get_api_config() -> dict:
    """Return the REST API configuration for this skill."""
    return API_CONFIG
