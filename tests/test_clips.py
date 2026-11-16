import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from autostudio.cli import main
from autostudio.clips import ClipsError, clip_path, get_video_provider, run_clips_step
from autostudio.config import load_settings
from autostudio.costs import CostGuard
from autostudio.ideas import run_idea_step
from autostudio.manifest import build_manifest
from autostudio.providers.base import VideoProvider
from autostudio.providers.text import MockTextProvider
from autostudio.runs import Run
from autostudio.script import run_script_step


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(load_settings(), runs_dir=tmp_path / "runs", gemini_api_key="",
                               veo_duration_seconds=5, max_cost_usd=5.0, max_retries=2)


class Fake(VideoProvider):
    """Scripted video provider: no ffmpeg, no network, fully controllable."""

    name = "fake"

    def __init__(self, cost=0.5, fail_attempts=None, always_pending=False):
        self.cost, self.always_pending = cost, always_pending
        self.fail_attempts = fail_attempts or {}   # scene_no -> how many submissions fail
        self.submits, self.cancelled = [], []
        self._scene_of, self._count = {}, {}

    def estimate_cost(self, config):
        return self.cost

    def submit_job(self, prompt, config):
        scene = config["scene_no"]
        job_id = f"job-{len(self.submits) + 1}"
        self.submits.append(scene)
        self._scene_of[job_id] = scene
        self._count[scene] = self._count.get(scene, 0) + 1
        return job_id

    def check_status(self, job_id):
        if self.always_pending:
            return "pending"
        scene = self._scene_of.get(job_id)
        if scene is not None and self._count[scene] <= self.fail_attempts.get(scene, 0):
            return "failed"
        return "done"  # unknown ids (jobs from an earlier crashed session) count as finished

    def retrieve_video(self, job_id, dest_path):
        Path(dest_path).write_bytes(b"fake-mp4")
        return Path(dest_path)

    def cancel_job(self, job_id):
        self.cancelled.append(job_id)


def make_run(settings, scenes=3):
    run = Run.create(settings.runs_dir)
    m = build_manifest(settings, run.id, "space facts", scenes, "9:16", False, 5.0)
    run_idea_step(run, m, MockTextProvider())
    run_script_step(run, m, MockTextProvider())
    return run, m


def go(run, m, provider, cap=5.0, **kw):
    kw = {"workers": 2, "poll_interval_s": 0.01, "timeout_s": 0.05, "sleep": lambda s: None, **kw}
    guard = CostGuard(run, cap)
    return guard, run_clips_step(run, m, provider, guard, **kw)


def test_happy_path_pays_once_per_scene(settings):
    run, m = make_run(settings, 3)
    p = Fake(cost=0.5)
    guard, clips = go(run, m, p)
    assert [c.name for c in clips] == ["scene_01.mp4", "scene_02.mp4", "scene_03.mp4"]
    assert all(c.read_bytes() == b"fake-mp4" for c in clips)
    assert sorted(p.submits) == [1, 2, 3] and guard.spent_usd == 1.5
    assert run.step_status("clips") == "done"
    assert all(run.get_job(n)["status"] == "done" for n in (1, 2, 3))


def test_rerun_resumes_without_paying_again(settings):
    run, m = make_run(settings, 3)
    go(run, m, Fake())
    p2 = Fake()
    guard, _ = go(run, m, p2)
    assert p2.submits == [] and guard.spent_usd == 1.5


def test_missing_file_is_refetched_not_repaid(settings):
    run, m = make_run(settings, 3)
    go(run, m, Fake())
    clip_path(run, 2).unlink()
    p2 = Fake()
    guard, _ = go(run, m, p2)
    assert p2.submits == [] and guard.spent_usd == 1.5 and clip_path(run, 2).exists()


def test_job_saved_before_a_crash_is_resumed_not_resubmitted(settings):
    run, m = make_run(settings, 3)
    run.save_job(2, "job-from-crashed-session", status="submitted", cost=0.5)
    p = Fake()
    guard, _ = go(run, m, p)
    assert sorted(p.submits) == [1, 3] and clip_path(run, 2).exists()
    assert guard.spent_usd == 1.0  # scene 2 was charged in the session that crashed, not now


def test_cap_stops_spending_before_submit(settings):
    run, m = make_run(settings, 3)
    p = Fake(cost=2.0)
    with pytest.raises(ClipsError) as e:
        go(run, m, p, cap=3.0)
    assert len(p.submits) == 1 and run.spent_usd() == 2.0
    assert "cap" in str(e.value).lower() and run.step_status("clips") == "failed"
    assert sum(clip_path(run, n).exists() for n in (1, 2, 3)) == 1  # the finished clip is kept


def test_failed_job_is_retried_and_charged_again(settings):
    run, m = make_run(settings, 3)
    p = Fake(cost=0.5, fail_attempts={2: 1})
    guard, _ = go(run, m, p)
    assert p.submits.count(2) == 2 and guard.spent_usd == 2.0 and clip_path(run, 2).exists()


def test_permanent_failure_keeps_the_good_clips(settings):
    run, m = make_run(settings, 3)
    p = Fake(fail_attempts={1: 99})
    with pytest.raises(ClipsError) as e:
        go(run, m, p, max_retries=1)
    assert p.submits.count(1) == 2 and "scene 1" in str(e.value)
    assert not clip_path(run, 1).exists() and clip_path(run, 2).exists() and clip_path(run, 3).exists()
    assert run.step_status("clips") == "failed"


def test_stuck_job_times_out_and_is_cancelled(settings):
    run, m = make_run(settings, 1)
    p = Fake(always_pending=True)
    with pytest.raises(ClipsError):
        go(run, m, p, max_retries=0, poll_interval_s=0.01, timeout_s=0.03)
    assert p.cancelled == ["job-1"]


def test_clips_need_a_script(settings):
    run = Run.create(settings.runs_dir)
    m = build_manifest(settings, run.id, "x", 3, "9:16", False, 5.0)
    with pytest.raises(ClipsError):
        go(run, m, Fake())
    assert run.step_status("clips") == "failed"


def test_new_script_discards_old_clips_but_not_the_ledger(settings):
    run, m = make_run(settings, 2)
    go(run, m, Fake(cost=0.5))
    assert clip_path(run, 1).exists()
    run_script_step(run, m, MockTextProvider())
    assert not clip_path(run, 1).exists() and run.state()["jobs"] == {}
    assert run.step_status("clips") == "pending" and run.spent_usd() == 1.0


def test_real_provider_is_not_available_yet(settings):
    with pytest.raises(RuntimeError):
        get_video_provider(settings, mock=False)


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="needs ffmpeg")
def test_cli_mock_run_makes_real_clips(settings, capsys):
    assert main(["run", "--niche", "space facts", "--mock", "--scenes", "2"], settings=settings) == 0
    (run_dir,) = list(settings.runs_dir.iterdir())
    for n in (1, 2):
        clip = run_dir / "clips" / f"scene_{n:02d}.mp4"
        assert clip.stat().st_size > 1000
        probe = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width,height:format=duration", "-of", "json", str(clip)],
            capture_output=True, text=True, check=True).stdout)
        assert (probe["streams"][0]["width"], probe["streams"][0]["height"]) == (1080, 1920)
        assert 4.5 < float(probe["format"]["duration"]) < 5.6
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["steps"]["clips"]["status"] == "done"
    assert "Clips: 2 generated" in capsys.readouterr().out
    assert main(["step", "clips", "--run", run_dir.name], settings=settings) == 0  # resume is a no-op
