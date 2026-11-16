"""`autostudio doctor`: checks ffmpeg, keys, OAuth, disk, browser engine.

Exit code 1 only on blockers; the browser engine only ever warns, because
web research is optional.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys

from autostudio.config import Settings
from autostudio.ffmpeg_utils import find_binary

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _nvenc_works() -> bool:
    exe = find_binary("ffmpeg")
    if not exe:
        return False
    try:
        enc = subprocess.run([exe, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=20).stdout
        if "h264_nvenc" not in enc:
            return False
        t = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.2",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30)
        return t.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _engine_check(s: Settings) -> tuple[str, str, str]:
    """Is the browser engine up? Research is optional, so this only ever warns."""
    label = "Browser engine"
    if not _has("httpx"):
        return (WARN, label, "httpx missing: pip install -r requirements.txt (research is optional)")
    if not s.browser_engine_api_key:
        return (WARN, label, "BROWSER_ENGINE_API_KEY not set (research is optional)")
    from autostudio.browser import BrowserClient, BrowserError

    try:
        client = BrowserClient(base_url=s.browser_engine_url, api_key=s.browser_engine_api_key, timeout_s=3.0)
    except BrowserError as e:
        return (WARN, label, str(e))
    try:
        client.health()
    except BrowserError as e:
        return (WARN, label, f"not reachable at {s.browser_engine_url} ({e}); research is optional")
    finally:
        client.shutdown()
    return (OK, label, f"healthy at {s.browser_engine_url}")


def run_checks(s: Settings) -> list[tuple[str, str, str]]:
    r: list[tuple[str, str, str]] = []
    v = sys.version_info
    r.append((OK if v >= (3, 11) else FAIL, "Python", f"{v.major}.{v.minor}.{v.micro}" + ("" if v >= (3, 11) else " (need 3.11+)")))
    for tool in ("ffmpeg", "ffprobe"):
        path = find_binary(tool)
        r.append((OK, tool, path) if path else (FAIL, tool, "not on PATH (see README: Windows setup)"))
    if find_binary("ffmpeg"):
        r.append((OK, "GPU encode", "h264_nvenc available") if _nvenc_works()
                 else (OK, "GPU encode", "not available, will use libx264"))
    for mod, label, level in [("pydantic", "pydantic", FAIL), ("dotenv", "python-dotenv", FAIL), ("PIL", "Pillow", FAIL),
                              ("google.genai", "google-genai", WARN), ("googleapiclient", "google-api-python-client", WARN),
                              ("google_auth_oauthlib", "google-auth-oauthlib", WARN), ("faster_whisper", "faster-whisper", WARN),
                              ("pyttsx3", "pyttsx3 (offline TTS)", WARN)]:
        r.append((OK, label, "installed") if _has(mod) else (level, label, "missing: pip install -r requirements.txt"))
    r.append((OK, "GEMINI_API_KEY", "set") if s.gemini_api_key and "your_" not in s.gemini_api_key
             else (WARN, "GEMINI_API_KEY", "not set (fine for --mock)"))
    r.append((OK, "client_secret.json", str(s.youtube_client_secrets_file)) if s.youtube_client_secrets_file.exists()
             else (WARN, "client_secret.json", "missing (only needed for upload)"))
    r.append((OK, "YouTube token", "present") if s.youtube_token_file.exists()
             else (WARN, "YouTube token", "missing: run `python -m autostudio auth youtube`"))
    free_gb = shutil.disk_usage(s.runs_dir.parent).free / 1e9
    r.append((OK if free_gb >= 2 else WARN, "Disk free", f"{free_gb:.1f} GB"))
    r.append(_engine_check(s))
    return r


def doctor(s: Settings) -> int:
    results = run_checks(s)
    for level, label, detail in results:
        print(f"[{level}] {label:<28} {detail}")
    fails = [x for x in results if x[0] == FAIL]
    print("\n" + ("Blocking problems found." if fails else "Ready for --mock runs.") )
    return 1 if fails else 0
