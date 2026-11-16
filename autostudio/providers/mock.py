"""MockProvider: builds a clip locally with FFmpeg. Free, offline, deterministic."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from autostudio.ffmpeg_utils import run_ffmpeg
from autostudio.providers.base import VideoProvider

PALETTE = [("0x1b2a49", "0x4a6fa5"), ("0x3d1e6d", "0xc05299"), ("0x0f4c5c", "0x5fa8a0"),
           ("0x5c2a0f", "0xe0a458"), ("0x1f3d2b", "0x7bc47f"), ("0x4a1c2c", "0xe27d60")]


def _scene_png(path: Path, scene_no: int, width: int) -> None:
    size = width // 3
    img = Image.new("RGBA", (width, size + 40), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    draw.text((width // 2, (size + 40) // 2), str(scene_no), fill=(255, 255, 255, 235), font=font, anchor="mm")
    img.save(path)


class MockProvider(VideoProvider):
    name = "mock"

    def __init__(self, work_dir: Path | None = None):
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "autostudio_mock_jobs"
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str) -> Path:
        return self.work_dir / f"{job_id}.mp4"

    def submit_job(self, prompt: str, config: dict) -> str:
        scene_no = int(config.get("scene_no", 1))
        duration = float(config["duration_s"])
        width, height, fps = int(config["width"]), int(config["height"]), int(config.get("fps", 30))
        job_id = "mock-" + hashlib.sha1(f"{prompt}|{scene_no}|{duration}|{width}x{height}".encode()).hexdigest()[:12]
        out = self._path(job_id)
        if out.exists():
            return job_id
        c0, c1 = PALETTE[(scene_no - 1) % len(PALETTE)]
        png = self.work_dir / f"{job_id}.png"
        _scene_png(png, scene_no, width)
        tmp_out = self.work_dir / f"{job_id}.partial.mp4"
        run_ffmpeg([
            "-f", "lavfi", "-i", f"gradients=s={width}x{height}:c0={c0}:c1={c1}:speed=0.03:d={duration}:r={fps}",
            "-i", str(png),
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-filter_complex", "[0:v][1:v]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]",
            "-map", "[v]", "-map", "2:a", "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-r", str(fps),
            "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
            str(tmp_out),
        ])
        tmp_out.replace(out)
        png.unlink(missing_ok=True)
        return job_id

    def check_status(self, job_id: str) -> str:
        return "done" if self._path(job_id).exists() else "failed"

    def retrieve_video(self, job_id: str, dest_path: Path) -> Path:
        src = self._path(job_id)
        if not src.exists():
            raise FileNotFoundError(f"Mock job {job_id} has no output")
        dest_path = Path(dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest_path)
        return dest_path

    def cancel_job(self, job_id: str) -> None:
        self._path(job_id).unlink(missing_ok=True)

    def estimate_cost(self, config: dict) -> float:
        return 0.0
