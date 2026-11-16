"""Step 3: one video clip per scene. This is the only step that spends money.

Safety rules, in order, for every scene:
  1. A finished clip on disk is never regenerated (reruns resume).
  2. A job id already saved in state.json is polled again, never resubmitted.
  3. The cost cap is charged BEFORE submit_job is called.
  4. The job id is saved the moment submit_job returns.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from autostudio.costs import CostGuard
from autostudio.manifest import Manifest
from autostudio.providers.base import VideoProvider
from autostudio.runs import Run
from autostudio.script import Script


class ClipsError(RuntimeError):
    pass


def clip_path(run: Run, scene_no: int) -> Path:
    return run.dir / "clips" / f"scene_{scene_no:02d}.mp4"


def get_video_provider(settings, mock: bool) -> VideoProvider:
    if mock:
        from autostudio.providers.mock import MockProvider  # imported late: needs Pillow

        return MockProvider()
    raise RuntimeError("The real Veo provider is not built yet. Use --mock, or add --until script to stop before clips.")


def _wait(provider: VideoProvider, job_id: str, poll_s: float, timeout_s: float, sleep) -> str:
    waited = 0.0
    while True:
        status = provider.check_status(job_id)
        if status in ("done", "failed"):
            return status
        if waited >= timeout_s:
            provider.cancel_job(job_id)
            return "failed"
        sleep(poll_s)
        waited += poll_s


def run_clips_step(run: Run, manifest: Manifest, provider: VideoProvider, guard: CostGuard, *,
                   max_retries: int = 2, workers: int = 2, poll_interval_s: float = 5.0,
                   timeout_s: float = 900.0, sleep=time.sleep) -> list[Path]:
    if not manifest.script:
        msg = f"No script yet. Run: python -m autostudio step script --run {run.id}"
        run.set_step("clips", "failed", error=msg)
        raise ClipsError(msg)
    script = Script.from_saved(manifest.script)
    lock = threading.Lock()  # state.json is read-modify-write, so one writer at a time
    run.set_step("clips", "running", provider=provider.name, error=None)

    def generate(scene_no: int) -> None:
        out = clip_path(run, scene_no)
        if out.exists() and out.stat().st_size > 0:
            return
        config = {"scene_no": scene_no, "duration_s": manifest.clip_duration_s, "width": manifest.width,
                  "height": manifest.height, "fps": manifest.fps, "aspect": manifest.aspect}
        prompt = script.clip_prompt(scene_no)
        with lock:
            saved = run.get_job(scene_no)
        job_id = saved["job_id"] if saved and saved.get("status") in ("submitted", "done") else None
        for attempt in range(1, max_retries + 2):
            if job_id is None:
                cost = provider.estimate_cost(config)
                with lock:
                    guard.charge(cost, f"scene {scene_no} (attempt {attempt})")  # raises CostCapExceeded
                job_id = provider.submit_job(prompt, config)
                with lock:
                    run.save_job(scene_no, job_id, status="submitted", cost=cost)
            if _wait(provider, job_id, poll_interval_s, timeout_s, sleep) == "done":
                tmp = out.with_suffix(".partial.mp4")
                provider.retrieve_video(job_id, tmp)
                tmp.replace(out)  # a crash never leaves a half-written clip that looks finished
                with lock:
                    run.save_job(scene_no, job_id, status="done")
                    run.log(f"scene {scene_no} done")
                return
            with lock:
                run.save_job(scene_no, job_id, status="failed")
                run.log(f"scene {scene_no} attempt {attempt} failed")
            job_id = None
        raise ClipsError(f"scene {scene_no} failed after {max_retries + 1} attempts")

    errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {sc.no: pool.submit(generate, sc.no) for sc in script.scenes}
        for no, fut in futures.items():
            try:
                fut.result()
            except Exception as e:
                errors[no] = str(e)
    if errors:
        msg = "; ".join(f"scene {no}: {err}" for no, err in sorted(errors.items()))
        run.set_step("clips", "failed", error=msg)
        run.log(f"clips failed: {msg}")
        raise ClipsError(msg + ". Finished clips are kept; rerun the step to resume.")
    run.set_step("clips", "done", provider=provider.name, spent_usd=guard.spent_usd, error=None)
    return [clip_path(run, sc.no) for sc in script.scenes]
