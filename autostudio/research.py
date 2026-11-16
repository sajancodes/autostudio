"""Step 0 (optional): read-only web research through the local browser engine.

Fetches a handful of pages, stores their text next to the run and records the
sources, so the idea step can use them as reference material.

Safety rules for this step:
  1. Read-only: pages are opened and read, never submitted to.
  2. No logins, no forms, no uploads, no downloads.
  3. The profile is always closed again, even when a page fails.
  4. Every page is truncated, so a huge page cannot eat the machine's memory.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from autostudio.browser import DEFAULT_LOAD_TIMEOUT_MS, DEFAULT_PROFILE
from autostudio.manifest import Manifest
from autostudio.runs import Run

RESEARCH_DIR = "research"
SOURCES_FILE = "sources.json"
MAX_PAGE_CHARS = 4000      # stored per page
MAX_EXCERPT_CHARS = 1200   # handed to the idea prompt per page
MAX_EXCERPTS = 5           # pages handed to the idea prompt


class ResearchError(RuntimeError):
    pass


@dataclass
class Page:
    url: str
    title: str
    text: str
    fetched_at: str


def normalize_url(url: str) -> str:
    """Only plain http(s) pages may be fetched."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ResearchError(f"Only http:// and https:// URLs can be researched: {url!r}")
    return url


def slugify(url: str) -> str:
    parsed = urlparse(url)
    raw = f"{parsed.netloc}{parsed.path}"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return (slug or "page")[:60]


def fetch_pages(browser, urls: list[str], profile: str = DEFAULT_PROFILE,
                load_timeout_ms: int = DEFAULT_LOAD_TIMEOUT_MS) -> list[Page]:
    """Open the profile, read every URL, and always close the profile again."""
    pages: list[Page] = []
    opened = False
    try:
        try:
            browser.close(profile)  # a profile left open by a crashed run would block the open below
        except Exception:
            pass
        browser.open(profile, headless=True)
        opened = True
        for url in urls:
            nav = browser.goto(profile, url)
            browser.wait_for_load(profile, state="load", timeout_ms=load_timeout_ms)
            data = browser.read_text(profile)
            nav = nav if isinstance(nav, dict) else {}
            data = data if isinstance(data, dict) else {}
            text = " ".join(str(data.get("text") or "").split())
            pages.append(Page(
                url=str(data.get("url") or nav.get("url") or url),
                title=str(nav.get("title") or "").strip(),
                text=text[:MAX_PAGE_CHARS],
                fetched_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            ))
    finally:
        if opened:
            try:
                browser.close(profile)
            except Exception:  # a failed close must never hide the real error
                pass
    return pages


def write_research(run_dir: Path, pages: list[Page], profile: str = DEFAULT_PROFILE) -> list[dict]:
    """Save the page texts and the source list; returns the source list."""
    research_dir = Path(run_dir) / RESEARCH_DIR
    research_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict] = []
    for i, page in enumerate(pages, 1):
        name = f"{i:02d}-{slugify(page.url)}.txt"
        (research_dir / name).write_text(page.text, encoding="utf-8")
        sources.append({
            "url": page.url,
            "title": page.title,
            "fetched_at": page.fetched_at,
            "chars": len(page.text),
            "file": name,
        })
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile": profile,
        "sources": sources,
    }
    (research_dir / SOURCES_FILE).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return sources


def load_excerpts(run_dir: Path, max_pages: int = MAX_EXCERPTS,
                  max_chars: int = MAX_EXCERPT_CHARS) -> list[dict]:
    """Short excerpts of the stored research, for the idea prompt.

    Returns [] when the run has no research, so the idea prompt stays unchanged.
    """
    research_dir = Path(run_dir) / RESEARCH_DIR
    sources_path = research_dir / SOURCES_FILE
    if not sources_path.exists():
        return []
    try:
        data = json.loads(sources_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sources = data.get("sources", []) if isinstance(data, dict) else data
    if not isinstance(sources, list):
        return []
    excerpts: list[dict] = []
    for src in sources[:max_pages]:
        if not isinstance(src, dict):
            continue
        text = ""
        name = src.get("file")
        if name:
            path = research_dir / str(name)
            if path.exists():
                text = " ".join(path.read_text(encoding="utf-8", errors="replace").split())
        if not text:
            continue
        excerpts.append({
            "url": str(src.get("url") or ""),
            "title": str(src.get("title") or ""),
            "text": text[:max_chars],
        })
    return excerpts


def run_research_step(run: Run, manifest: Manifest, browser, urls: list[str]) -> list[dict]:
    """Fetch the URLs, store them with the run and record the sources."""
    clean = [normalize_url(u) for u in urls]
    if not clean:
        raise ResearchError("No URLs to research. Use: python -m autostudio research --url <URL>")
    run.set_step("research", "running", provider=browser.name, error=None)
    try:
        pages = fetch_pages(browser, clean)
        sources = write_research(run.dir, pages)
        manifest.research_sources = sources
        manifest.save(run.dir)
        run.invalidate("idea")  # the idea was made without this material: it must be made again
    except Exception as e:
        run.set_step("research", "failed", error=str(e))
        run.log(f"research failed: {e}")
        if isinstance(e, ResearchError):
            raise
        raise ResearchError(f"Research failed: {e}") from e
    run.set_step("research", "done", provider=browser.name, error=None, sources=len(sources))
    run.log(f"research done: {len(sources)} page(s)")
    return sources
