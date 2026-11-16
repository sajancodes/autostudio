import dataclasses
import json

import pytest

from autostudio.cli import main
from autostudio.config import load_settings
from autostudio.costs import CostCapExceeded, CostGuard, estimate_clips
from autostudio.ideas import IdeaError, generate_idea
from autostudio.manifest import Manifest, build_manifest
from autostudio.providers.text import MockTextProvider, TextProvider, parse_json_object
from autostudio.runs import Run


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(
        load_settings(), runs_dir=tmp_path / "runs", gemini_api_key="",
        veo_cost_per_second=0.20, veo_duration_seconds=5, max_cost_usd=5.0, max_retries=2,
    )


class Scripted(TextProvider):
    name = "scripted"

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def generate_json(self, prompt):
        self.calls += 1
        r = self.replies.pop(0)
        return parse_json_object(r) if isinstance(r, str) else r


GOOD = {"title": "T", "hook": "H", "summary": "S", "tags": ["#a", "b"]}


def test_cost_guard_blocks_and_persists(settings):
    run = Run.create(settings.runs_dir)
    guard = CostGuard(run, 1.0)
    assert guard.charge(0.6, "scene 1") == 0.6
    with pytest.raises(CostCapExceeded):
        guard.charge(0.5, "scene 2")
    assert Run.open(settings.runs_dir, run.id).spent_usd() == 0.6  # blocked charge was not recorded
    assert guard.remaining_usd == 0.4
    with pytest.raises(ValueError):
        guard.charge(-1, "refund")


def test_estimates(settings):
    assert estimate_clips(settings, 4, mock=True).total_usd == 0.0
    assert estimate_clips(settings, 4, mock=False).total_usd == 4.0


def test_manifest_roundtrip_and_validation(settings, tmp_path):
    m = build_manifest(settings, "r1", " space ", 3, "9:16", True, 5.0)
    assert (m.niche, m.width, m.height, m.publish_targets) == ("space", 1080, 1920, ["youtube"])
    m.save(tmp_path)
    assert Manifest.load(tmp_path) == m
    assert build_manifest(settings, "r1", "x", 3, "9:16", True, 5.0, upload=False).publish_targets == []
    for bad in (dict(aspect="4:3"), dict(scenes=0), dict(scenes=99), dict(niche="  ")):
        kw = dict(niche="x", scenes=3, aspect="9:16") | bad
        with pytest.raises(ValueError):
            build_manifest(settings, "r1", kw["niche"], kw["scenes"], kw["aspect"], True, 5.0)


def test_mock_idea_is_deterministic():
    a = generate_idea(MockTextProvider(), "space facts", 4, 5)
    b = generate_idea(MockTextProvider(), "space facts", 4, 5)
    assert a == b and "space facts" in a.title


def test_idea_retries_then_succeeds_or_fails():
    p = Scripted(["not json", {"title": "", "hook": "h", "summary": "s"}, GOOD])
    idea = generate_idea(p, "x", 4, 5, retries=2)
    assert p.calls == 3 and idea.tags == ["a", "b"]
    with pytest.raises(IdeaError):
        generate_idea(Scripted(["bad"] * 3), "x", 4, 5, retries=2)


def test_parse_json_strips_code_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")


def test_cli_run_mock_end_to_end(settings, capsys):
    assert main(["run", "--niche", "space facts", "--mock", "--until", "script"], settings=settings) == 0
    (run_dir,) = list(settings.runs_dir.iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert manifest["idea"]["title"] and state["steps"]["idea"]["status"] == "done"
    assert (run_dir / "idea.json").exists()
    assert "space facts" in capsys.readouterr().out


def test_cli_refuses_over_cap_before_creating_anything(settings):
    assert main(["run", "--niche", "x", "--max-cost", "1"], settings=settings) == 3
    assert not settings.runs_dir.exists()


def test_cli_missing_key_creates_no_run(settings):
    assert main(["run", "--niche", "x"], settings=settings) == 2  # real Gemini, no key
    assert not settings.runs_dir.exists()


def test_cli_step_idea_rerun_and_unknown_run(settings):
    main(["run", "--niche", "space facts", "--mock", "--until", "script"], settings=settings)
    (run_dir,) = list(settings.runs_dir.iterdir())
    assert main(["step", "idea", "--run", run_dir.name], settings=settings) == 0
    assert main(["step", "idea", "--run", "nope"], settings=settings) == 2
    assert main(["step", "edit", "--run", run_dir.name], settings=settings) == 2
