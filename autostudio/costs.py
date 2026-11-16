"""Cost estimates and the hard spending cap.

The cap is checked BEFORE money is spent: once as a pre-flight on the whole
plan, and again per charge via CostGuard (call charge() before submit_job).
"""
from __future__ import annotations

from dataclasses import dataclass

from autostudio.config import Settings
from autostudio.runs import Run


class CostCapExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class CostEstimate:
    scenes: int
    per_scene_usd: float
    total_usd: float


def estimate_clips(settings: Settings, scenes: int, mock: bool) -> CostEstimate:
    per_scene = 0.0 if mock else round(settings.veo_cost_per_second * settings.veo_duration_seconds, 4)
    return CostEstimate(scenes=scenes, per_scene_usd=per_scene, total_usd=round(per_scene * scenes, 4))


def ensure_within_cap(spent_usd: float, amount_usd: float, cap_usd: float, label: str = "") -> None:
    if amount_usd < 0:
        raise ValueError("cost amounts cannot be negative")
    if spent_usd + amount_usd > cap_usd + 1e-9:
        what = f" for {label}" if label else ""
        raise CostCapExceeded(
            f"Cost cap would be exceeded{what}: spent ${spent_usd:.2f} + ${amount_usd:.2f} "
            f"> cap ${cap_usd:.2f}. Raise MAX_COST_USD in .env or pass --max-cost."
        )


class CostGuard:
    def __init__(self, run: Run, cap_usd: float):
        self.run = run
        self.cap_usd = float(cap_usd)

    @property
    def spent_usd(self) -> float:
        return self.run.spent_usd()

    @property
    def remaining_usd(self) -> float:
        return round(max(self.cap_usd - self.spent_usd, 0.0), 4)

    def check(self, amount_usd: float, label: str = "") -> None:
        ensure_within_cap(self.spent_usd, amount_usd, self.cap_usd, label)

    def charge(self, amount_usd: float, label: str) -> float:
        """Check the cap, then record the spend. Returns the new total."""
        self.check(amount_usd, label)
        if amount_usd == 0:
            return self.spent_usd
        return self.run.add_cost(label, amount_usd)
