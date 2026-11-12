"""Small HTTP helpers: cached file downloads."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

USER_AGENT = "rcn-wroclaw-research/0.1 (python-httpx)"


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, min=2, max=30))
def download(url: str, dest: Path, *, force: bool = False, timeout: float = 300.0) -> Path:
    """Stream ``url`` to ``dest`` unless it already exists (atomic via a .part file)."""
    if dest.exists() and not force:
        log.info("cached: %s", dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream(
        "GET", url, follow_redirects=True, timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
    tmp.replace(dest)
    log.info("downloaded %s -> %s (%.1f MB)", url, dest, dest.stat().st_size / 1e6)
    return dest
