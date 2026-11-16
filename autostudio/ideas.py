"""Step 1: turn a niche into one validated video idea."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from autostudio.manifest import Manifest
from autostudio.providers.text import TextProvider
from autostudio.runs import Run

MAX_TITLE = 100  # YouTube's title limit


class IdeaError(RuntimeError):
    pass


@dataclass
class Idea:
    title: str
    hook: str
    summary: str
    tags: list

    @classmethod
    def from_dict(cls, data: dict) -> "Idea":
        title = str(data.get("title", "")).strip()
        hook = str(data.get("hook", "")).strip()
        summary = str(data.get("summary", "")).strip()
        raw_tags = data.get("tags", [])
        if not title or not hook or not summary:
            raise ValueError("idea needs a non-empty title, hook and summary")
        if len(title) > MAX_TITLE:
            raise ValueError(f"title is {len(title)} characters; the limit is {MAX_TITLE}")
        if not isinstance(raw_tags, list):
            raise ValueError("tags must be a list")
        tags = [str(t).strip().lstrip("#") for t in raw_tags if str(t).strip()][:10]
        return cls(title=title, hook=hook, summary=summary, tags=tags)


def build_idea_prompt(niche: str, scenes: int, clip_seconds: int) -> str:
    total = scenes * clip_seconds
    return (
        "Task: idea\n"
        "You are a short-form video producer. Invent ONE video idea.\n"
        f"Niche: {niche}\n"
        f"Length: about {total} seconds, {scenes} scenes of {clip_seconds}s each.\n"
        "Rules: a hook that works in the first two seconds, one clear payoff, "
        f"no claims you cannot back up, title under {MAX_TITLE} characters.\n"
        'Reply with ONLY a JSON object: {"title": str, "hook": str, "summary": str, "tags": [str, ...]}'
    )


def generate_idea(provider: TextProvider, niche: str, scenes: int, clip_seconds: int, retries: int = 2) -> Idea:
    prompt = build_idea_prompt(niche, scenes, clip_seconds)
    last_error: Exception | None = None
    for _ in range(max(retries, 0) + 1):
        try:
            return Idea.from_dict(provider.generate_json(prompt))
        except ValueError as e:  # bad JSON or failed validation: ask again
            last_error = e
    raise IdeaError(f"Could not get a valid idea after {max(retries, 0) + 1} attempts: {last_error}")


def run_idea_step(run: Run, manifest: Manifest, provider: TextProvider, retries: int = 2) -> Idea:
    run.set_step("idea", "running", provider=provider.name, error=None)
    try:
        idea = generate_idea(provider, manifest.niche, manifest.scenes, manifest.clip_duration_s, retries)
    except Exception as e:
        run.set_step("idea", "failed", error=str(e))
        run.log(f"idea failed: {e}")
        raise
    (run.dir / "idea.json").write_text(json.dumps(asdict(idea), indent=2, ensure_ascii=False), encoding="utf-8")
    manifest.idea = asdict(idea)
    manifest.script = None  # a new idea makes any earlier script stale
    (run.dir / "script.json").unlink(missing_ok=True)
    run.invalidate("script")
    manifest.save(run.dir)
    run.set_step("idea", "done", provider=provider.name, error=None)
    run.log(f"idea done: {idea.title}")
    return idea
