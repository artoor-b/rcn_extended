"""Download RCN apartment-unit transactions (layer ``ms:lokale``) from the GUGiK WFS.

The server-side attribute filter on ``TERYT`` proved unreliable (returns only a small
subset), so we query by spatial tiles with paging and filter by TERYT while parsing.
Raw GML pages are cached on disk; re-running skips pages that already exist.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from rcn_wroclaw import config

log = logging.getLogger(__name__)

_NUMBER_RETURNED = re.compile(rb'numberReturned="(\d+)"')
_GID = re.compile(rb"<ms:gid>(\d+)</ms:gid>")
_EXCEPTION = re.compile(rb"ExceptionReport|ServiceException")


@dataclass(frozen=True)
class Tile:
    """A rectangular query tile in EPSG:2180 (easting/northing)."""

    i: int
    j: int
    e_min: float
    n_min: float
    e_max: float
    n_max: float

    @property
    def name(self) -> str:
        return f"tile_{self.i:02d}_{self.j:02d}"

    def bbox_param(self) -> str:
        """WFS 2.0 BBOX value; urn EPSG:2180 uses (northing, easting) axis order."""
        return (
            f"{self.n_min:.0f},{self.e_min:.0f},{self.n_max:.0f},{self.e_max:.0f},"
            "urn:ogc:def:crs:EPSG::2180"
        )

    def center(self) -> tuple[float, float]:
        return ((self.e_min + self.e_max) / 2, (self.n_min + self.n_max) / 2)


def make_tiles(
    bbox: tuple[float, float, float, float] = config.WROCLAW_BBOX_2180,
    tile_size_m: float = 2000.0,
) -> list[Tile]:
    """Split an (e_min, n_min, e_max, n_max) bbox into a regular grid of tiles."""
    e_min, n_min, e_max, n_max = bbox
    nx = math.ceil((e_max - e_min) / tile_size_m)
    ny = math.ceil((n_max - n_min) / tile_size_m)
    tiles = []
    for i in range(nx):
        for j in range(ny):
            e0 = e_min + i * tile_size_m
            n0 = n_min + j * tile_size_m
            tiles.append(
                Tile(i, j, e0, n0, min(e0 + tile_size_m, e_max), min(n0 + tile_size_m, n_max))
            )
    return tiles


def central_tiles(tiles: list[Tile], n: int, center=config.RYNEK_2180) -> list[Tile]:
    """Return the ``n`` tiles closest to ``center`` (used for quick samples)."""
    ce, cn = center
    return sorted(tiles, key=lambda t: math.dist(t.center(), (ce, cn)))[:n]


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=2, min=2, max=60))
def _get_page(client: httpx.Client, tile: Tile, start_index: int, page_size: int) -> bytes:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": config.RCN_LAYER_LOKALE,
        "COUNT": str(page_size),
        "STARTINDEX": str(start_index),
        # Without an explicit sort order MapServer paging is unstable: pages overlap and
        # records go missing (verified 2026-11-12: 20 of 5992 lost in one tile).
        "SORTBY": "gid ASC",
        "BBOX": tile.bbox_param(),
    }
    resp = client.get(config.RCN_WFS_URL, params=params)
    resp.raise_for_status()
    content = resp.content
    if _EXCEPTION.search(content[:2000]):
        raise RuntimeError(f"WFS exception for {tile.name} @ {start_index}: {content[:500]!r}")
    return content


def number_returned(content: bytes) -> int:
    """Extract ``numberReturned`` from a WFS 2.0 FeatureCollection header."""
    m = _NUMBER_RETURNED.search(content[:5000])
    if m is None:
        raise ValueError("numberReturned not found in WFS response")
    return int(m.group(1))


def iter_tile_pages(
    client: httpx.Client,
    tile: Tile,
    out_dir: Path,
    page_size: int = config.RCN_PAGE_SIZE,
    delay_s: float = 0.3,
) -> Iterator[Path]:
    """Download all pages for a tile, yielding the cached GML file paths."""
    k = 0
    while True:
        path = out_dir / f"{tile.name}_p{k:03d}.gml"
        if path.exists():
            content = path.read_bytes()
        else:
            content = _get_page(client, tile, k * page_size, page_size)
            path.write_bytes(content)
            time.sleep(delay_s)
        n = number_returned(content)
        yield path
        if n < page_size:
            break
        k += 1


def fetch_lokale(
    out_dir: Path,
    tiles: list[Tile],
    page_size: int = config.RCN_PAGE_SIZE,
    delay_s: float = 0.3,
) -> list[Path]:
    """Download all ``ms:lokale`` features intersecting ``tiles`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    headers = {"User-Agent": "rcn-wroclaw-research/0.1 (python-httpx)"}
    with httpx.Client(timeout=120.0, headers=headers) as client:
        for idx, tile in enumerate(tiles, 1):
            tile_paths = list(iter_tile_pages(client, tile, out_dir, page_size, delay_s))
            paths.extend(tile_paths)
            gids = [g for p in tile_paths for g in _GID.findall(p.read_bytes())]
            if len(gids) != len(set(gids)):
                log.warning(
                    "tile %s: %d duplicate gids across pages – paging may be unstable",
                    tile.name,
                    len(gids) - len(set(gids)),
                )
            log.info("tile %s (%d/%d): %d page(s)", tile.name, idx, len(tiles), len(tile_paths))
    return paths
