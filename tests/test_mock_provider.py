from autostudio.ffmpeg_utils import video_info
from autostudio.providers import MockProvider
from autostudio.runs import Run

CFG = {"scene_no": 2, "duration_s": 2.0, "width": 270, "height": 480, "fps": 30}


def test_mock_clip_properties(tmp_path):
    p = MockProvider(tmp_path / "jobs")
    job = p.submit_job("a rocket launch", CFG)
    assert p.check_status(job) == "done"
    out = p.retrieve_video(job, tmp_path / "clip.mp4")
    info = video_info(out)
    assert (info["width"], info["height"]) == (270, 480)
    assert round(info["fps"]) == 30
    assert abs(info["duration"] - 2.0) < 0.2
    assert info["has_audio"]


def test_mock_is_free_and_idempotent(tmp_path):
    p = MockProvider(tmp_path / "jobs")
    assert p.estimate_cost(CFG) == 0.0
    assert p.submit_job("same", CFG) == p.submit_job("same", CFG)


def test_state_saves_job_immediately(tmp_path):
    run = Run.create(tmp_path, "t1")
    run.save_job(1, "job-abc", cost=0.0)
    assert Run(tmp_path, "t1").get_job(1)["job_id"] == "job-abc"
    assert run.step_status("clips") == "pending"
