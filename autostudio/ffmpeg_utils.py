"""Thin FFmpeg/ffprobe helpers. Always argument lists, never shell=True."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def find_binary(name: str) -> str | None:
    return shutil.which(name)


def run_ffmpeg(args: list[str], timeout: int = 600) -> None:
    exe = find_binary("ffmpeg")
    if not exe:
        raise FFmpegError("ffmpeg not found on PATH. Run `python -m autostudio doctor`.")
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.strip()[-800:]}")


def probe(path: Path) -> dict:
    exe = find_binary("ffprobe")
    if not exe:
        raise FFmpegError("ffprobe not found on PATH. Run `python -m autostudio doctor`.")
    cmd = [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {path}: {proc.stderr.strip()[-400:]}")
    return json.loads(proc.stdout)


def video_info(path: Path) -> dict:
    """Return width, height, fps, duration, has_audio for a media file."""
    data = probe(path)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        raise FFmpegError(f"No video stream in {path}")
    num, _, den = v.get("r_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps,
        "duration": float(data["format"].get("duration", 0)),
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def parse_aspect(aspect: str) -> tuple[int, int]:
    """'9:16' -> (1080, 1920); '16:9' -> (1920, 1080); '1:1' -> (1080, 1080)."""
    try:
        w, h = (int(x) for x in aspect.split(":"))
    except ValueError as e:
        raise ValueError(f"Bad aspect '{aspect}', expected like 9:16") from e
    long_side, short_side = 1920, 1080
    if w == h:
        return short_side, short_side
    return (short_side, long_side) if h > w else (long_side, short_side)
