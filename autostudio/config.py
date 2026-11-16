"""Settings loaded from .env (python-dotenv). No secrets are ever hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # doctor reports this; keep imports light
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    veo_model: str
    veo_aspect_ratio: str
    veo_duration_seconds: int
    veo_cost_per_second: float
    max_cost_usd: float
    tts_provider: str
    gemini_tts_model: str
    youtube_client_secrets_file: Path
    youtube_token_file: Path
    max_concurrent_generations: int
    max_retries: int
    runs_dir: Path
    browser_engine_url: str
    browser_engine_api_key: str
    fps: int = 30


def load_settings(env_file: Path | None = None) -> Settings:
    if load_dotenv is not None:
        load_dotenv(env_file or PROJECT_ROOT / ".env")
    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        veo_model=os.getenv("VEO_MODEL", "veo-2.0-generate-001"),
        veo_aspect_ratio=os.getenv("VEO_ASPECT_RATIO", "9:16"),
        veo_duration_seconds=_i("VEO_DURATION_SECONDS", 5),
        veo_cost_per_second=_f("VEO_COST_PER_SECOND", 0.20),
        max_cost_usd=_f("MAX_COST_USD", 5.00),
        tts_provider=os.getenv("TTS_PROVIDER", "offline"),
        gemini_tts_model=os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash"),
        youtube_client_secrets_file=PROJECT_ROOT / os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secret.json"),
        youtube_token_file=PROJECT_ROOT / os.getenv("YOUTUBE_TOKEN_FILE", "youtube_token.json"),
        max_concurrent_generations=_i("MAX_CONCURRENT_GENERATIONS", 2),
        max_retries=_i("MAX_RETRIES", 2),
        runs_dir=PROJECT_ROOT / "runs",
        browser_engine_url=os.getenv("BROWSER_ENGINE_URL", "http://127.0.0.1:8765").rstrip("/"),
        browser_engine_api_key=os.getenv("BROWSER_ENGINE_API_KEY", "").strip(),
    )
