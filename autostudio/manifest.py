"""manifest.json: the single description of what a run is making."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from autostudio.config import Settings
from autostudio.costs import estimate_clips

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
MAX_SCENES = 10
FILENAME = "manifest.json"


@dataclass
class Manifest:
    run_id: str
    niche: str
    mock: bool
    scenes: int
    aspect: str
    width: int
    height: int
    fps: int
    clip_duration_s: int
    max_cost_usd: float
    estimated_cost_usd: float
    publish_targets: list = field(default_factory=lambda: ["youtube"])
    idea: dict | None = None
    script: dict | None = None
    created_at: str = ""

    def save(self, run_dir: Path) -> Path:
        path = Path(run_dir) / FILENAME
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)  # atomic, same as state.json
        return path

    @classmethod
    def load(cls, run_dir: Path) -> "Manifest":
        data = json.loads((Path(run_dir) / FILENAME).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def build_manifest(settings: Settings, run_id: str, niche: str, scenes: int, aspect: str,
                   mock: bool, max_cost_usd: float, upload: bool = True) -> Manifest:
    niche = niche.strip()
    if not niche:
        raise ValueError("--niche cannot be empty")
    if not 1 <= scenes <= MAX_SCENES:
        raise ValueError(f"--scenes must be between 1 and {MAX_SCENES}")
    if aspect not in ASPECTS:
        raise ValueError(f"--aspect must be one of: {', '.join(ASPECTS)}")
    width, height = ASPECTS[aspect]
    return Manifest(
        run_id=run_id, niche=niche, mock=mock, scenes=scenes, aspect=aspect,
        width=width, height=height, fps=settings.fps,
        clip_duration_s=settings.veo_duration_seconds,
        max_cost_usd=float(max_cost_usd),
        estimated_cost_usd=estimate_clips(settings, scenes, mock).total_usd,
        publish_targets=["youtube"] if upload else [],
        created_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
