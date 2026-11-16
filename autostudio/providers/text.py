"""Text providers (used by the idea step now, the script step next)."""
from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod


class TextProvider(ABC):
    name = "base"

    @abstractmethod
    def generate_json(self, prompt: str) -> dict:
        """Return one parsed JSON object. Raise ValueError if the reply is not one."""


def parse_json_object(text: str) -> dict:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    data = json.loads(cleaned)  # JSONDecodeError is a ValueError
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


class MockTextProvider(TextProvider):
    """Free, offline, deterministic: the same niche always gives the same idea."""

    name = "mock"
    _TEMPLATES = [
        ("3 {n} facts that sound fake but are real", "Stop scrolling: number two about {n} is the one nobody believes."),
        ("The {n} mistake almost everyone makes", "You are probably doing this {n} thing wrong right now."),
        ("{n} in 30 seconds", "Everything you need to know about {n}, and I will not waste your time."),
        ("What nobody tells you about {n}", "I looked into {n} so you do not have to. Here is the surprising part."),
    ]

    _MIDDLE = ["First, the part almost everyone gets wrong.", "Then the detail that changes everything.",
               "And here is why it matters more than you think."]

    def generate_json(self, prompt: str) -> dict:
        if re.search(r"^Task:\s*script\s*$", prompt, flags=re.MULTILINE):
            return self._script(prompt)
        return self._idea(prompt)

    @staticmethod
    def _field(prompt: str, name: str, default: str) -> str:
        m = re.search(rf"^{name}:\s*(.+)$", prompt, flags=re.MULTILINE)
        return m.group(1).strip() if m else default

    def _script(self, prompt: str) -> dict:
        niche = self._field(prompt, "Niche", "this topic")
        n = int(self._field(prompt, "Scenes", "4"))
        max_words = int(self._field(prompt, "Max words per scene", "12"))
        lines = []
        for i in range(1, n + 1):
            if i == 1:
                text = f"Here is something about {niche} you will remember."
            elif i == n:
                text = "That is the whole story. Follow for more."
            else:
                text = self._MIDDLE[(i - 2) % len(self._MIDDLE)]
            lines.append(" ".join(text.split()[:max_words]))
        return {
            "style": "Consistent cinematic look, soft natural lighting, vertical framing, no on-screen text.",
            "scenes": [
                {"narration": t, "visual_prompt": f"Shot {i}: slow camera move over a scene that illustrates {niche}."}
                for i, t in enumerate(lines, 1)
            ],
        }

    def _idea(self, prompt: str) -> dict:
        m = re.search(r"^Niche:\s*(.+)$", prompt, flags=re.MULTILINE)
        niche = m.group(1).strip() if m else "this topic"
        title_t, hook_t = self._TEMPLATES[int(hashlib.sha1(niche.encode()).hexdigest(), 16) % len(self._TEMPLATES)]
        slug = re.sub(r"[^a-z0-9]+", "", niche.lower()) or "shorts"
        return {
            "title": title_t.format(n=niche),
            "hook": hook_t.format(n=niche),
            "summary": f"A short, punchy video about {niche}, built to hook in the first two seconds.",
            "tags": [slug, "shorts", "mock"],
        }


class GeminiTextProvider(TextProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set. Add it to .env, or use --mock.")
        from google import genai  # imported late so --mock never needs the key or network

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_json(self, prompt: str) -> dict:
        resp = self._client.models.generate_content(
            model=self._model, contents=prompt, config={"response_mime_type": "application/json"}
        )
        return parse_json_object(resp.text or "")


def get_text_provider(settings, mock: bool) -> TextProvider:
    if mock:
        return MockTextProvider()
    return GeminiTextProvider(settings.gemini_api_key, settings.gemini_model)
