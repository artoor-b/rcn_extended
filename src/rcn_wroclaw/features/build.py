"""Assemble the location feature table (one row per unique location point)."""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from rcn_wroclaw import config
from rcn_wroclaw.features.euclidean import (
    centre_features,
    dedup_points,
    poi_features,
    unique_locations,
)
from rcn_wroclaw.features.network import WalkGraph, walk_features
from rcn_wroclaw.features.transit import transit_features

log = logging.getLogger(__name__)

# Categories that get a walking distance (others only euclidean).
WALK_POINT_CATEGORIES = [
    "rail_station", "school", "kindergarten", "nursery", "university", "clinic", "pharmacy",
    "supermarket", "convenience", "mall",
]  # fmt: skip
WALK_AREA_CATEGORIES = {"park": 5_000, "forest": 10_000, "water": 10_000}  # min area m²
FEATURES_PATH = config.PROCESSED_DIR / "location_features.parquet"


def assign_osiedle(points: gpd.GeoDataFrame, osiedla: gpd.GeoDataFrame) -> pd.Series:
    """Osiedle name for each point (nearest polygon if a point falls on/outside an edge)."""
    left = gpd.GeoDataFrame(geometry=points.geometry.to_numpy(), crs=points.crs)
    j = gpd.sjoin_nearest(left, osiedla[["osiedle", "geometry"]], max_distance=500)
    j = j[~j.index.duplicated(keep="first")]
    return j["osiedle"].reindex(range(len(points))).set_axis(points.index)


def walk_targets(poi: gpd.GeoDataFrame, stops: gpd.GeoDataFrame) -> dict[str, gpd.GeoDataFrame]:
    """Target layers for walking distances."""
    poi = dedup_points(poi)
    served = stops[stops["dep_day"] > 0]
    t: dict[str, gpd.GeoDataFrame] = {
        "stop": served,
        "tram_stop": served[served["n_lines_tram"] > 0],
        "rynek": gpd.GeoDataFrame(
            geometry=gpd.points_from_xy([config.RYNEK_2180[0]], [config.RYNEK_2180[1]]),
            crs=config.CRS,
        ),
    }
    for cat in WALK_POINT_CATEGORIES:
        t[cat] = poi[poi["category"] == cat]
    for cat, min_area in WALK_AREA_CATEGORIES.items():
        a = poi[poi["category"] == cat]
        t[cat] = a[a.area >= min_area]
    return t


def build_location_features(
    points: gpd.GeoDataFrame,
    poi: gpd.GeoDataFrame,
    stops: gpd.GeoDataFrame,
    osiedla: gpd.GeoDataFrame,
    graph: WalkGraph | None = None,
) -> gpd.GeoDataFrame:
    """Compute all location features for ``points`` (must contain ``location_id``)."""
    points = points.reset_index(drop=True)
    parts = [
        points[["location_id"]],
        pd.DataFrame({"osiedle": assign_osiedle(points, osiedla)}),
        centre_features(points),
        transit_features(points, stops),
        poi_features(points, poi),
    ]
    if graph is not None:
        parts.append(
            walk_features(points, graph, walk_targets(poi, stops), caps={"rynek": 30_000.0})
        )
    df = pd.concat(parts, axis=1)
    df["x_2180"] = points.geometry.x
    df["y_2180"] = points.geometry.y
    return gpd.GeoDataFrame(df, geometry=points.geometry, crs=config.CRS)


def rcn_locations() -> gpd.GeoDataFrame:
    """Unique locations of all RCN unit records."""
    return unique_locations(gpd.read_parquet(config.RCN_LOKALE_INTERIM))
