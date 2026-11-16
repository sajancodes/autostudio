import dataclasses
import json

import pytest

from autostudio.cli import main
from autostudio.config import load_settings
from autostudio.ideas import run_idea_step
from autostudio.manifest import Manifest, build_manifest
from autostudio.providers.text import MockTextProvider, TextProvider
from autostudio.runs import Run
from autostudio.script import Script, ScriptError, generate_script, run_script_step

IDEA = {"title": "T", "hook": "H", "summary": "S", "tags": []}


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(load_settings(), runs_dir=tmp_path / "runs", gemini_api_key="",
                               veo_duration_seconds=5, max_cost_usd=5.0, max_retries=2)


class Scripted(TextProvider):
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def generate_json(self, prompt):
        self.calls += 1
        return self.replies.pop(0)


def scenes(n, narration="Short line.", visual="A wide shot."):
    return {"style": "Cinematic.", "scenes": [{"narration": narration, "visual_prompt": visual} for _ in range(n)]}


@pytest.mark.parametrize("n", [1, 4, 7])
def test_mock_script_is_valid_for_any_scene_count(n):
    script = generate_script(MockTextProvider(), "space facts", IDEA, n, 5)
    assert [s.no for s in script.scenes] == list(range(1, n + 1))
    assert all(len(s.narration.split()) <= 12 for s in script.scenes)
    assert "cinematic" in script.clip_prompt(1) and script.scenes[0].visual_prompt in script.clip_prompt(1)


def test_validation_rejects_bad_scripts():
    bad = [
        scenes(3),                                                  # wrong count
        scenes(4, narration="word " * 40),                          # overruns the clip
        scenes(4, visual=""),                                       # empty shot
        scenes(4, visual="x" * 700),                                # shot too long
        {"style": "s", "scenes": ["not an object"] * 4},
        {"style": "s"},                                             # no scenes key
    ]
    for data in bad:
        with pytest.raises(ValueError):
            Script.from_dict(data, 4, 5)
    ok = Script.from_dict(scenes(4, narration="  two\n words "), 4, 5)
    assert ok.scenes[0].narration == "two words"


def test_generate_script_retries_then_fails():
    p = Scripted([scenes(2), scenes(4, narration="w " * 99), scenes(4)])
    assert len(generate_script(p, "x", IDEA, 4, 5, retries=2).scenes) == 4 and p.calls == 3
    with pytest.raises(ScriptError):
        generate_script(Scripted([scenes(1)] * 2), "x", IDEA, 4, 5, retries=1)


def test_script_step_needs_an_idea(settings):
    run = Run.create(settings.runs_dir)
    m = build_manifest(settings, run.id, "x", 4, "9:16", True, 5.0)
    with pytest.raises(ScriptError):
        run_script_step(run, m, MockTextProvider())
    assert run.step_status("script") == "failed"


def test_new_idea_clears_old_script(settings):
    main(["run", "--niche", "space facts", "--mock", "--until", "script"], settings=settings)
    (run_dir,) = list(settings.runs_dir.iterdir())
    assert (run_dir / "script.json").exists()
    assert main(["step", "idea", "--run", run_dir.name], settings=settings) == 0
    assert not (run_dir / "script.json").exists()
    assert Manifest.load(run_dir).script is None
    assert Run.open(settings.runs_dir, run_dir.name).step_status("script") == "pending"
    assert main(["step", "script", "--run", run_dir.name], settings=settings) == 0
    assert (run_dir / "script.json").exists()


def test_cli_run_writes_script_for_each_scene(settings, capsys):
    assert main(["run", "--niche", "space facts", "--mock", "--until", "script", "--scenes", "6"], settings=settings) == 0
    (run_dir,) = list(settings.runs_dir.iterdir())
    data = json.loads((run_dir / "script.json").read_text(encoding="utf-8"))
    assert len(data["scenes"]) == 6
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["steps"]["idea"]["status"] == state["steps"]["script"]["status"] == "done"
    out = capsys.readouterr().out
    assert "Script: 6 scenes" in out and "  6. " in out


def test_script_step_on_old_run_without_script_field(settings):
    # manifests written before this step existed have no "script" key
    run = Run.create(settings.runs_dir)
    m = build_manifest(settings, run.id, "x", 4, "9:16", True, 5.0)
    m.save(run.dir)
    data = json.loads((run.dir / "manifest.json").read_text(encoding="utf-8"))
    data.pop("script")
    (run.dir / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    m = Manifest.load(run.dir)
    run_idea_step(run, m, MockTextProvider())
    assert len(run_script_step(run, m, MockTextProvider()).scenes) == 4
