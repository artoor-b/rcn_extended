"""Points of interest and other location layers from OpenStreetMap (Overpass via OSMnx).

One Overpass query fetches every feature matching any tag in :data:`QUERY_TAGS` within the
buffered city boundary. Features are then classified locally into categories (a feature
may belong to several). Output is long format: one row per (feature, category).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

from rcn_wroclaw import config

log = logging.getLogger(__name__)

Mask = Callable[[pd.DataFrame], pd.Series]


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df.columns else pd.Series(pd.NA, index=df.index, dtype="object")


def _isin(name: str, values: list[str]) -> Mask:
    return lambda df: _col(df, name).isin(values)


def _any(*masks: Mask) -> Mask:
    def f(df: pd.DataFrame) -> pd.Series:
        out = pd.Series(False, index=df.index)
        for m in masks:
            out |= m(df).fillna(False).astype(bool)
        return out

    return f


def _name_contains(pattern: str) -> Mask:
    return lambda df: _col(df, "name").astype("string").str.contains(pattern, case=False, na=False)


def _not(mask: Mask) -> Mask:
    return lambda df: ~mask(df).fillna(False).astype(bool)


def _all(*masks: Mask) -> Mask:
    def f(df: pd.DataFrame) -> pd.Series:
        out = pd.Series(True, index=df.index)
        for m in masks:
            out &= m(df).fillna(False).astype(bool)
        return out

    return f


@dataclass(frozen=True)
class Category:
    """A POI category: how to select it and how to represent its geometry.

    ``kind``: "point" -> representative point; "area" -> polygon kept (distance to edge);
    "line" -> linear features kept (distance to line).
    """

    name: str
    mask: Mask
    kind: str = "point"
    description: str = ""


_NURSERY_NAME = _name_contains(r"żłob")

CATEGORIES: list[Category] = [
    Category(
        "rail_station",
        _all(
            _isin("railway", ["station", "halt"]), _not(_isin("station", ["subway", "light_rail"]))
        ),
        description="railway=station|halt (train)",
    ),
    Category("school", _isin("amenity", ["school"]), description="amenity=school"),
    Category(
        "kindergarten",
        _all(_isin("amenity", ["kindergarten"]), _not(_NURSERY_NAME)),
        description="amenity=kindergarten (excluding names with 'żłob')",
    ),
    Category(
        "nursery",
        _any(
            _isin("amenity", ["childcare"]), _all(_isin("amenity", ["kindergarten"]), _NURSERY_NAME)
        ),
        description="żłobek: amenity=childcare or kindergarten named 'żłob…'",
    ),
    Category(
        "university",
        _isin("amenity", ["university", "college"]),
        description="amenity=university|college",
    ),
    Category("hospital", _isin("amenity", ["hospital"]), description="amenity=hospital"),
    Category(
        "clinic",
        _any(
            _isin("amenity", ["clinic", "doctors"]),
            _isin("healthcare", ["clinic", "centre", "doctor"]),
        ),
        description="amenity=clinic|doctors, healthcare=clinic|centre|doctor",
    ),
    Category("pharmacy", _isin("amenity", ["pharmacy"]), description="amenity=pharmacy"),
    Category("supermarket", _isin("shop", ["supermarket"]), description="shop=supermarket"),
    Category("convenience", _isin("shop", ["convenience"]), description="shop=convenience"),
    Category("mall", _isin("shop", ["mall"]), description="shop=mall"),
    Category(
        "food_drink",
        _isin("amenity", ["restaurant", "cafe", "fast_food", "bar", "pub"]),
        description="amenity=restaurant|cafe|fast_food|bar|pub",
    ),
    Category(
        "culture",
        _isin("amenity", ["theatre", "cinema", "library", "arts_centre"]),
        description="amenity=theatre|cinema|library|arts_centre",
    ),
    Category("post_office", _isin("amenity", ["post_office"]), description="amenity=post_office"),
    Category(
        "parcel_locker", _isin("amenity", ["parcel_locker"]), description="amenity=parcel_locker"
    ),
    Category(
        "sport",
        _isin("leisure", ["sports_centre", "fitness_centre", "swimming_pool"]),
        description="leisure=sports_centre|fitness_centre|swimming_pool",
    ),
    Category("playground", _isin("leisure", ["playground"]), description="leisure=playground"),
    Category("park", _isin("leisure", ["park"]), kind="area", description="leisure=park"),
    Category(
        "forest",
        _any(_isin("landuse", ["forest"]), _isin("natural", ["wood"])),
        kind="area",
        description="landuse=forest, natural=wood",
    ),
    Category("water", _isin("natural", ["water"]), kind="area", description="natural=water"),
    Category(
        "industrial",
        _isin("landuse", ["industrial"]),
        kind="area",
        description="landuse=industrial",
    ),
    Category(
        "cemetery", _isin("landuse", ["cemetery"]), kind="area", description="landuse=cemetery"
    ),
    Category("railway_line", _isin("railway", ["rail"]), kind="line", description="railway=rail"),
    Category(
        "major_road",
        _isin("highway", ["motorway", "trunk", "primary"]),
        kind="line",
        description="highway=motorway|trunk|primary",
    ),
]

QUERY_TAGS: dict[str, list[str] | bool] = {
    "railway": ["station", "halt", "rail"],
    "amenity": [
        "school", "kindergarten", "childcare", "university", "college", "hospital", "clinic",
        "doctors", "pharmacy", "restaurant", "cafe", "fast_food", "bar", "pub", "theatre",
        "cinema", "library", "arts_centre", "post_office", "parcel_locker",
    ],
    "healthcare": ["clinic", "centre", "doctor"],
    "shop": ["supermarket", "convenience", "mall"],
    "leisure": ["sports_centre", "fitness_centre", "swimming_pool", "playground", "park"],
    "landuse": ["forest", "industrial", "cemetery"],
    "natural": ["wood", "water"],
    "highway": ["motorway", "trunk", "primary"],
}  # fmt: skip

KEEP_TAGS = ["name", *QUERY_TAGS.keys(), "station"]


def configure_osmnx(cache_dir: Path) -> None:
    """Point the OSMnx HTTP cache at the raw-data folder and relax timeouts."""
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(cache_dir)
    ox.settings.requests_timeout = 600
    ox.settings.log_console = False


def fetch_features(area_2180, snapshot_dir: Path) -> gpd.GeoDataFrame:
    """Run the Overpass query for ``area_2180`` (EPSG:2180 polygon); cache raw result."""
    import osmnx as ox

    raw_path = snapshot_dir / "features.parquet"
    if raw_path.exists():
        return gpd.read_parquet(raw_path)
    configure_osmnx(snapshot_dir / "http_cache")
    # The PRG boundary has thousands of vertices -> Overpass answers 413. Simplifying a
    # 2 km buffer by 200 m keeps the whole city covered.
    simple = area_2180.simplify(200, preserve_topology=True)
    poly_4326 = gpd.GeoSeries([simple], crs=config.CRS).to_crs("EPSG:4326").iloc[0]
    feats = ox.features_from_polygon(poly_4326, tags=QUERY_TAGS)
    feats = feats.reset_index()  # element / id -> columns
    keep = ["element", "id", *[c for c in KEEP_TAGS if c in feats.columns], "geometry"]
    feats = feats[keep].copy()
    for c in keep:
        if c not in ("id", "geometry"):
            feats[c] = feats[c].astype("string")
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(raw_path)
    log.info("OSM: %d features -> %s", len(feats), raw_path)
    return feats


def classify(feats: gpd.GeoDataFrame, categories: list[Category] = CATEGORIES) -> gpd.GeoDataFrame:
    """Return long-format POI table (EPSG:2180): category, osm ids, name, kind, geometry."""
    feats = feats.to_crs(config.CRS)
    parts = []
    for cat in categories:
        sub = feats[cat.mask(feats).to_numpy()].copy()
        if sub.empty:
            log.warning("OSM category %s is empty", cat.name)
            continue
        if cat.kind == "point":
            sub["geometry"] = sub.geometry.representative_point()
        elif cat.kind == "area":
            sub = sub[sub.geom_type.isin(["Polygon", "MultiPolygon"])]
        elif cat.kind == "line":
            sub = sub[sub.geom_type.isin(["LineString", "MultiLineString"])]
        sub["category"] = cat.name
        sub["kind"] = cat.kind
        parts.append(sub[["category", "kind", "element", "id", "name", "geometry"]])
    out = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=config.CRS)
    return out.rename(columns={"element": "osm_type", "id": "osm_id"})
