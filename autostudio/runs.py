"""Run directories and resumable state.json."""
from __future__ import annotations

import json
import time
from pathlib import Path

STEPS = ["research", "idea", "script", "clips", "edit", "qc", "upload"]
SUBDIRS = ["clips", "audio", "subs", "research"]


def new_run_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


class Run:
    def __init__(self, runs_dir: Path, run_id: str):
        self.id = run_id
        self.dir = Path(runs_dir) / run_id
        self.state_path = self.dir / "state.json"

    @classmethod
    def create(cls, runs_dir: Path, run_id: str | None = None) -> "Run":
        run = cls(runs_dir, run_id or new_run_id())
        run.dir.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (run.dir / sub).mkdir(exist_ok=True)
        if not run.state_path.exists():
            run._write({
                "run_id": run.id,
                "steps": {s: {"status": "pending"} for s in STEPS},
                "jobs": {},
                "costs": {"spent_usd": 0.0, "entries": []},
            })
        return run

    @classmethod
    def open(cls, runs_dir: Path, run_id: str) -> "Run":
        """Attach to an existing run; never creates anything."""
        run = cls(runs_dir, run_id)
        if not run.state_path.exists():
            raise FileNotFoundError(f"No run '{run_id}' in {runs_dir}")
        return run

    def _write(self, state: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)  # atomic: a crash never leaves half a file

    def state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def set_step(self, step: str, status: str, **extra) -> None:
        st = self.state()
        st["steps"].setdefault(step, {}).update({"status": status, **extra})
        self._write(st)

    def step_status(self, step: str) -> str:
        return self.state()["steps"].get(step, {}).get("status", "pending")

    def save_job(self, scene_no: int, job_id: str, **extra) -> None:
        """Persist a job id immediately after submit so a crash never double-pays."""
        st = self.state()
        st["jobs"][str(scene_no)] = {"job_id": job_id, **extra}
        self._write(st)

    def get_job(self, scene_no: int) -> dict | None:
        return self.state()["jobs"].get(str(scene_no))

    def invalidate(self, from_step: str) -> None:
        """Mark from_step and every later step pending because their input changed.

        Stale clip files and job records are dropped so the clips get regenerated.
        Money already spent stays in the cost ledger.
        """
        first = STEPS.index(from_step)
        st = self.state()
        for step in STEPS[first:]:
            st["steps"][step] = {"status": "pending"}
        if first <= STEPS.index("clips"):
            st["jobs"] = {}
            for f in (self.dir / "clips").glob("scene_*.mp4"):
                f.unlink()
        self._write(st)

    def add_cost(self, label: str, usd: float) -> float:
        """Append a spend entry and return the new running total."""
        st = self.state()
        costs = st.setdefault("costs", {"spent_usd": 0.0, "entries": []})
        costs["entries"].append({"label": label, "usd": round(float(usd), 4), "at": time.strftime("%H:%M:%S")})
        costs["spent_usd"] = round(sum(e["usd"] for e in costs["entries"]), 4)
        self._write(st)
        return costs["spent_usd"]

    def spent_usd(self) -> float:
        return float(self.state().get("costs", {}).get("spent_usd", 0.0))

    def log(self, message: str) -> None:
        with (self.dir / "log.txt").open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
