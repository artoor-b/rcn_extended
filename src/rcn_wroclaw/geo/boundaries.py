"""City boundary (PRG via the RCN WFS) and Wrocław osiedla boundaries."""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import httpx
from lxml import etree
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

from rcn_wroclaw import config
from rcn_wroclaw.http import USER_AGENT, download

log = logging.getLogger(__name__)

GML_NS = "http://www.opengis.net/gml/3.2"


def _ring(pos_list: str) -> list[tuple[float, float]]:
    """Convert a 2D ``gml:posList`` in (northing, easting) order into (easting, northing)."""
    vals = [float(v) for v in pos_list.split()]
    if len(vals) % 2:
        raise ValueError("posList with odd number of values; expected srsDimension=2")
    return [(vals[i + 1], vals[i]) for i in range(0, len(vals), 2)]


def parse_gml_polygons(path: Path) -> MultiPolygon | Polygon:
    """Parse all ``gml:Polygon`` elements of a WFS 2.0 response (urn EPSG::2180)."""
    tree = etree.parse(str(path))
    polys = []
    for poly in tree.iter(f"{{{GML_NS}}}Polygon"):
        ext = poly.find(f"{{{GML_NS}}}exterior//{{{GML_NS}}}posList")
        holes = [
            _ring(el.text)
            for el in poly.findall(f"{{{GML_NS}}}interior//{{{GML_NS}}}posList")
            if el.text
        ]
        polys.append(Polygon(_ring(ext.text), holes))
    if not polys:
        raise ValueError(f"no gml:Polygon in {path}")
    geom = unary_union(polys)
    return geom


def fetch_city_boundary(out: Path = config.BOUNDARY_CITY) -> gpd.GeoDataFrame:
    """Download the county polygon containing Rynek and save it as GeoParquet (EPSG:2180)."""
    raw = config.BOUNDARY_RAW_DIR / f"powiat_{config.TERYT_WROCLAW}.gml"
    if not raw.exists():
        e, n = config.RYNEK_2180
        params = {
            "SERVICE": "WFS",
            "VERSION": "2.0.0",
            "REQUEST": "GetFeature",
            "TYPENAMES": config.RCN_LAYER_POWIATY,
            # Tiny bbox around Rynek; urn EPSG::2180 -> (northing, easting) order.
            "BBOX": f"{n - 50:.0f},{e - 50:.0f},{n + 50:.0f},{e + 50:.0f},"
            "urn:ogc:def:crs:EPSG::2180",
        }
        resp = httpx.get(
            config.RCN_WFS_URL, params=params, timeout=120, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(resp.content)
    geom = parse_gml_polygons(raw)
    gdf = gpd.GeoDataFrame(
        {"teryt": [config.TERYT_WROCLAW], "name": ["Wrocław"]}, geometry=[geom], crs=config.CRS
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out)
    log.info("city boundary: area %.1f km² -> %s", geom.area / 1e6, out)
    return gdf


def fetch_osiedla(out: Path = config.OSIEDLA) -> gpd.GeoDataFrame:
    """Download Wrocław osiedla boundaries and save them in EPSG:2180."""
    zpath = download(config.OSIEDLA_URL, config.BOUNDARY_RAW_DIR / "granice-osiedli.zip")
    gdf = gpd.read_file(f"zip://{zpath}!{config.OSIEDLA_SHP_IN_ZIP}")
    gdf = gdf.rename(columns={"NROSIEDLA": "osiedle_nr", "NAZWAOSIED": "osiedle"})[
        ["osiedle_nr", "osiedle", "geometry"]
    ].to_crs(config.CRS)
    gdf.to_parquet(out)
    log.info("osiedla: %d polygons -> %s", len(gdf), out)
    return gdf


def load_city_boundary() -> Polygon | MultiPolygon:
    """Return the city polygon (EPSG:2180) from the interim GeoParquet."""
    return gpd.read_parquet(config.BOUNDARY_CITY).geometry.iloc[0]
