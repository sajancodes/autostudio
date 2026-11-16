from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class VideoProvider(ABC):
    """Contract every clip generator must follow."""

    name = "base"

    @abstractmethod
    def submit_job(self, prompt: str, config: dict) -> str: ...

    @abstractmethod
    def check_status(self, job_id: str) -> str:
        """Return 'pending', 'done' or 'failed'."""

    @abstractmethod
    def retrieve_video(self, job_id: str, dest_path: Path) -> Path: ...

    @abstractmethod
    def cancel_job(self, job_id: str) -> None: ...

    @abstractmethod
    def estimate_cost(self, config: dict) -> float: ...
