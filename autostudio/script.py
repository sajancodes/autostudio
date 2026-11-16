"""Step 2: turn the chosen idea into per-scene narration and shot descriptions."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from autostudio.manifest import Manifest
from autostudio.providers.text import TextProvider
from autostudio.runs import Run

WORDS_PER_SECOND = 2.5   # comfortable spoken pace; the prompt asks for this
HARD_WORDS_PER_SECOND = 3.0  # above this the voice-over will overrun its clip
MAX_VISUAL_CHARS = 600


class ScriptError(RuntimeError):
    pass


def _clean(text) -> str:
    return " ".join(str(text or "").split())


@dataclass
class Scene:
    no: int
    narration: str
    visual_prompt: str


@dataclass
class Script:
    style: str
    scenes: list

    def clip_prompt(self, scene_no: int) -> str:
        """The text sent to the video provider: the shot plus the shared style."""
        scene = self.scenes[scene_no - 1]
        return f"{scene.visual_prompt} {self.style}".strip()

    @classmethod
    def from_saved(cls, data: dict) -> "Script":
        """Rebuild a Script from what asdict() saved in script.json / the manifest."""
        return cls(style=data["style"], scenes=[Scene(**sc) for sc in data["scenes"]])

    @classmethod
    def from_dict(cls, data: dict, scenes: int, clip_seconds: int) -> "Script":
        raw = data.get("scenes")
        if not isinstance(raw, list) or len(raw) != scenes:
            got = len(raw) if isinstance(raw, list) else "none"
            raise ValueError(f"expected exactly {scenes} scenes, got {got}")
        hard_limit = int(clip_seconds * HARD_WORDS_PER_SECOND)
        out = []
        for i, item in enumerate(raw, 1):
            if not isinstance(item, dict):
                raise ValueError(f"scene {i} is not an object")
            narration = _clean(item.get("narration"))
            visual = _clean(item.get("visual_prompt"))
            if not narration or not visual:
                raise ValueError(f"scene {i} needs both narration and visual_prompt")
            words = len(narration.split())
            if words > hard_limit:
                raise ValueError(f"scene {i} narration is {words} words; max is {hard_limit} for {clip_seconds}s")
            if len(visual) > MAX_VISUAL_CHARS:
                raise ValueError(f"scene {i} visual_prompt is {len(visual)} characters; max is {MAX_VISUAL_CHARS}")
            out.append(Scene(no=i, narration=narration, visual_prompt=visual))  # numbering is ours, not the model's
        return cls(style=_clean(data.get("style")), scenes=out)


def build_script_prompt(niche: str, idea: dict, scenes: int, clip_seconds: int) -> str:
    max_words = int(clip_seconds * WORDS_PER_SECOND)
    return (
        "Task: script\n"
        "You are a short-form video scriptwriter. Write the script for this idea.\n"
        f"Niche: {_clean(niche)}\n"
        f"Title: {_clean(idea.get('title'))}\n"
        f"Hook: {_clean(idea.get('hook'))}\n"
        f"Summary: {_clean(idea.get('summary'))}\n"
        f"Scenes: {scenes}\n"
        f"Seconds per scene: {clip_seconds}\n"
        f"Max words per scene: {max_words}\n"
        "Rules: scene 1 opens with the hook; the last scene ends with a short call to action; narration is "
        "spoken aloud in plain sentences with no emojis or stage directions; each visual_prompt describes ONE "
        "continuous shot (subject, action, camera, lighting) with no on-screen text, no real people and no "
        "logos or brands; style is one sentence of look-and-feel shared by every shot.\n"
        'Reply with ONLY a JSON object: {"style": str, "scenes": [{"narration": str, "visual_prompt": str}, ...]} '
        f"with exactly {scenes} scenes."
    )


def generate_script(provider: TextProvider, niche: str, idea: dict, scenes: int, clip_seconds: int,
                    retries: int = 2) -> Script:
    prompt = build_script_prompt(niche, idea, scenes, clip_seconds)
    attempts = max(retries, 0) + 1
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return Script.from_dict(provider.generate_json(prompt), scenes, clip_seconds)
        except ValueError as e:  # bad JSON or failed validation: ask again
            last_error = e
    raise ScriptError(f"Could not get a valid script after {attempts} attempts: {last_error}")


def run_script_step(run: Run, manifest: Manifest, provider: TextProvider, retries: int = 2) -> Script:
    if not manifest.idea:
        msg = "No idea yet. Run the idea step first: python -m autostudio step idea --run " + run.id
        run.set_step("script", "failed", error=msg)
        raise ScriptError(msg)
    run.set_step("script", "running", provider=provider.name, error=None)
    try:
        script = generate_script(provider, manifest.niche, manifest.idea, manifest.scenes,
                                 manifest.clip_duration_s, retries)
    except Exception as e:
        run.set_step("script", "failed", error=str(e))
        run.log(f"script failed: {e}")
        raise
    (run.dir / "script.json").write_text(json.dumps(asdict(script), indent=2, ensure_ascii=False), encoding="utf-8")
    manifest.script = asdict(script)
    manifest.save(run.dir)
    run.invalidate("clips")  # clips made from an older script no longer match
    run.set_step("script", "done", provider=provider.name, error=None)
    run.log(f"script done: {len(script.scenes)} scenes")
    return script
