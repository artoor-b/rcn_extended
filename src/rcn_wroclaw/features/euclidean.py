"""Straight-line location features: nearest distances, buffer counts, land-cover shares.

All inputs must be in the projected working CRS (EPSG:2180, metres).
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from rcn_wroclaw import config


def location_id(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Stable id from coordinates rounded to 0.1 m (``L<x_dm>_<y_dm>``)."""
    xd = np.round(np.asarray(x) * 10).astype(np.int64)
    yd = np.round(np.asarray(y) * 10).astype(np.int64)
    return np.char.add(np.char.add(np.char.add("L", xd.astype(str)), "_"), yd.astype(str))


def unique_locations(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Unique point locations of ``gdf`` with a ``location_id`` column (EPSG:2180)."""
    pts = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty]
    ids = location_id(pts.geometry.x.to_numpy(), pts.geometry.y.to_numpy())
    out = gpd.GeoDataFrame({"location_id": ids}, geometry=pts.geometry.to_numpy(), crs=gdf.crs)
    return out.drop_duplicates("location_id").reset_index(drop=True)


def dedup_points(poi: gpd.GeoDataFrame, max_dist_m: float = 100.0) -> gpd.GeoDataFrame:
    """Drop point POI that duplicate an earlier POI of the same category and (case-insensitive)
    name within ``max_dist_m`` – typical OSM double mapping (node + building outline).
    Unnamed features are never treated as duplicates."""
    poi = poi.reset_index(drop=True)
    keep = np.ones(len(poi), dtype=bool)
    named = poi["name"].notna() & (poi["kind"] == "point")
    for (_, _), grp in poi[named].groupby(["category", poi.loc[named, "name"].str.lower()]):
        if len(grp) < 2:
            continue
        xy = np.column_stack([grp.geometry.x, grp.geometry.y])
        idx = grp.index.to_numpy()
        tree = cKDTree(xy)
        for i, j in sorted(tree.query_pairs(max_dist_m)):
            if keep[idx[i]]:
                keep[idx[j]] = False
    return poi[keep].reset_index(drop=True)


def _xy(g: gpd.GeoSeries | gpd.GeoDataFrame) -> np.ndarray:
    geom = g.geometry if isinstance(g, gpd.GeoDataFrame) else g
    return np.column_stack([geom.x.to_numpy(), geom.y.to_numpy()])


def nearest_point_distance(points: gpd.GeoDataFrame, targets: gpd.GeoDataFrame) -> np.ndarray:
    """Distance from each point to the nearest target point (NaN if no targets)."""
    if len(targets) == 0:
        return np.full(len(points), np.nan)
    d, _ = cKDTree(_xy(targets)).query(_xy(points), k=1)
    return d


def nearest_geometry_distance(points: gpd.GeoDataFrame, targets: gpd.GeoDataFrame) -> np.ndarray:
    """Distance to the nearest polygon/line (0 inside a polygon)."""
    if len(targets) == 0:
        return np.full(len(points), np.nan)
    left = gpd.GeoDataFrame(geometry=points.geometry.to_numpy(), crs=points.crs)
    j = gpd.sjoin_nearest(left, targets[["geometry"]], distance_col="_d")
    return j.groupby(level=0)["_d"].min().reindex(range(len(points))).to_numpy()


def count_within(points: gpd.GeoDataFrame, targets: gpd.GeoDataFrame, radius: float) -> np.ndarray:
    """Number of target points within ``radius`` of each point."""
    if len(targets) == 0:
        return np.zeros(len(points), dtype=np.int32)
    tree = cKDTree(_xy(targets))
    return tree.query_ball_point(_xy(points), r=radius, return_length=True).astype(np.int32)


def area_share(points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, radius: float) -> np.ndarray:
    """Share (0–1) of the circular buffer around each point covered by ``polygons``."""
    if len(polygons) == 0:
        return np.zeros(len(points))
    cover = polygons.geometry.union_all()
    cover_parts = gpd.GeoDataFrame(geometry=[cover], crs=polygons.crs).explode(index_parts=False)
    buffers = gpd.GeoDataFrame(
        {"_i": np.arange(len(points))},
        geometry=points.geometry.buffer(radius, 32).to_numpy(),
        crs=points.crs,
    )
    inter = gpd.overlay(buffers, cover_parts, how="intersection", keep_geom_type=True)
    covered = inter.assign(_a=inter.area).groupby("_i")["_a"].sum()
    full = np.pi * radius**2
    return (covered.reindex(np.arange(len(points))).fillna(0.0) / full).clip(0, 1).to_numpy()


def poi_features(
    points: gpd.GeoDataFrame,
    poi: gpd.GeoDataFrame,
    radii: tuple[int, ...] = (300, 500, 1000),
    min_area_m2: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Euclidean POI features for ``points``.

    - point categories: ``dist_<cat>_m`` and ``cnt_<cat>_<r>`` for each radius,
    - area / line categories: ``dist_<cat>_m`` (areas filtered by ``min_area_m2``),
    - ``share_green_<r>`` (park ∪ forest) and ``share_industrial_500``.
    """
    min_area_m2 = min_area_m2 or {"park": 5_000, "forest": 10_000, "water": 10_000}
    poi = dedup_points(poi.to_crs(config.CRS))
    out: dict[str, np.ndarray] = {}
    for cat, grp in poi.groupby("category"):
        kind = grp["kind"].iloc[0]
        if kind == "point":
            out[f"dist_{cat}_m"] = nearest_point_distance(points, grp)
            for r in radii:
                out[f"cnt_{cat}_{r}"] = count_within(points, grp, r)
        else:
            if kind == "area" and cat in min_area_m2:
                grp = grp[grp.area >= min_area_m2[cat]]
            out[f"dist_{cat}_m"] = nearest_geometry_distance(points, grp)
    green = poi[poi["category"].isin(["park", "forest"])]
    for r in (500, 1000):
        out[f"share_green_{r}"] = area_share(points, green, r)
    out["share_industrial_500"] = area_share(points, poi[poi["category"] == "industrial"], 500)
    return pd.DataFrame(out, index=points.index)


def centre_features(points: gpd.GeoDataFrame, centre=config.RYNEK_2180) -> pd.DataFrame:
    """Distance and direction to Rynek (direction as sin/cos of the bearing)."""
    dx = points.geometry.x.to_numpy() - centre[0]
    dy = points.geometry.y.to_numpy() - centre[1]
    bearing = np.arctan2(dx, dy)  # 0 = north, clockwise
    return pd.DataFrame(
        {
            "dist_rynek_m": np.hypot(dx, dy),
            "dir_rynek_sin": np.sin(bearing),
            "dir_rynek_cos": np.cos(bearing),
        },
        index=points.index,
    )
