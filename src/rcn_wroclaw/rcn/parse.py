"""Parse RCN WFS 2.0 GML pages (layer ``ms:lokale``) into a GeoDataFrame.

Column names are the original RCN attribute names lower-cased (e.g. ``TRAN_CENA_BRUTTO`` ->
``tran_cena_brutto``) so they map 1:1 to the official schema.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

import geopandas as gpd
import pandas as pd
from lxml import etree
from shapely.geometry import Point

from rcn_wroclaw import config

log = logging.getLogger(__name__)

MS_NS = "http://mapserver.gis.umn.edu/mapserver"
GML_NS = "http://www.opengis.net/gml/3.2"

# Attribute types according to DescribeFeatureType (checked 2026-11-12).
FLOAT_FIELDS = {
    "TRAN_CENA_BRUTTO",
    "TRAN_VAT",
    "NIER_POW_GRUNTU",
    "NIER_CENA_BRUTTO",
    "NIER_VAT",
    "LOK_POW_UZYT",
    "LOK_POW_PRZYN",
    "LOK_CENA_BRUTTO",
    "LOK_VAT",
}
INT_FIELDS = {"gid", "LOK_LICZBA_IZB", "LOK_NR_KOND"}
DATE_FIELDS = {"DOK_DATA"}
TIMESTAMP_FIELDS = {"TRAN_WERSJA_ID"}

# Personal data (notary names/addresses) – never stored beyond raw GML.
DROP_FIELDS = {"DOK_TWORCA"}


def parse_pos(text: str) -> Point:
    """Convert a WFS 2.0 ``gml:pos`` for urn EPSG::2180 into an (easting, northing) Point.

    The server writes coordinates in EPSG axis order for EPSG:2180, i.e. ``northing easting``.
    """
    northing, easting = (float(v) for v in text.split())
    return Point(easting, northing)


def _text_value(elem: etree._Element) -> str | None:
    """Return element text, descending into ``gml:timePosition`` for time types."""
    tp = elem.find(f"{{{GML_NS}}}timePosition")
    raw = tp.text if tp is not None else elem.text
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def iter_features(path: Path) -> Iterator[dict]:
    """Yield one dict per ``ms:lokale`` feature from a GML file (raw string values)."""
    for _, elem in etree.iterparse(str(path), events=("end",), tag=f"{{{MS_NS}}}lokale"):
        rec: dict = {"geometry": None}
        for child in elem:
            name = etree.QName(child).localname
            if name == "boundedBy":
                continue
            if name == "GEOMETRIA":
                pos = child.find(f".//{{{GML_NS}}}pos")
                if pos is not None and pos.text:
                    rec["geometry"] = parse_pos(pos.text)
                continue
            if name in DROP_FIELDS:
                continue
            rec[name] = _text_value(child)
        yield rec
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]


def records_to_frame(records: Iterable[dict]) -> gpd.GeoDataFrame:
    """Convert raw feature dicts into a typed GeoDataFrame in EPSG:2180."""
    df = pd.DataFrame.from_records(list(records))
    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs=config.CRS)
    for col in df.columns:
        if col in FLOAT_FIELDS:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Float64")
        elif col in INT_FIELDS:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        elif col in DATE_FIELDS:
            df[col] = pd.to_datetime(df[col], errors="coerce", format="ISO8601")
        elif col in TIMESTAMP_FIELDS:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True, format="ISO8601")
        elif col != "geometry":
            df[col] = df[col].astype("string")
    df.columns = [c if c == "geometry" else c.lower() for c in df.columns]
    return gpd.GeoDataFrame(df, geometry="geometry", crs=config.CRS)


def parse_files(
    paths: Iterable[Path], teryt: str | None = config.TERYT_WROCLAW
) -> gpd.GeoDataFrame:
    """Parse GML pages, drop duplicates (overlapping tiles) and filter by TERYT."""
    paths = list(paths)

    def _all() -> Iterator[dict]:
        for p in paths:
            yield from iter_features(p)

    gdf = records_to_frame(_all())
    n_raw = len(gdf)
    if gdf.empty:
        return gdf
    gdf = gdf.drop_duplicates(subset="gid").reset_index(drop=True)
    n_dedup = len(gdf)
    if teryt is not None:
        gdf = gdf[gdf["teryt"] == teryt].reset_index(drop=True)
    log.info(
        "parsed %d features from %d files; %d after dedup; %d with TERYT=%s",
        n_raw,
        len(paths),
        n_dedup,
        len(gdf),
        teryt,
    )
    return gdf
