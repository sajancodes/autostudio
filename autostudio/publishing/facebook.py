"""Facebook publishing is out of scope for the MVP. Interface only."""
from __future__ import annotations

from pathlib import Path


class FacebookPublisher:
    def upload(self, video_path: Path, title: str, description: str) -> str:
        # TODO: implement with the Graph API in a later phase.
        raise NotImplementedError("Facebook publishing is not part of the MVP")
