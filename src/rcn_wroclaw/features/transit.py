"""Public-transport accessibility features from GTFS stop service levels."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree


def transit_features(
    points: gpd.GeoDataFrame,
    stops: gpd.GeoDataFrame,
    radii: tuple[int, ...] = (300, 500, 1000),
) -> pd.DataFrame:
    """Per point: distance to the nearest served stop / tram stop and, within each radius,
    the number of distinct lines (all, tram), departures per weekday and in the 7–9 peak.

    Departures are summed over stop poles, so both directions of a line count (this is
    intended: it measures how many vehicles one can board nearby).
    """
    served = stops[stops["dep_day"] > 0].reset_index(drop=True)
    tram = served[served["n_lines_tram"] > 0].reset_index(drop=True)
    pxy = np.column_stack([points.geometry.x, points.geometry.y])
    sxy = np.column_stack([served.geometry.x, served.geometry.y])
    txy = np.column_stack([tram.geometry.x, tram.geometry.y])

    out: dict[str, np.ndarray] = {}
    out["dist_stop_m"] = cKDTree(sxy).query(pxy)[0]
    out["dist_tram_stop_m"] = cKDTree(txy).query(pxy)[0] if len(tram) else np.nan

    tree = cKDTree(sxy)
    routes = served["routes"].to_numpy()
    routes_tram = served["routes_tram"].to_numpy()
    dep_day = served["dep_day"].to_numpy()
    dep_peak = served["dep_peak_7_9"].to_numpy()
    for r in radii:
        neigh = tree.query_ball_point(pxy, r=r)
        out[f"n_lines_{r}"] = np.array(
            [len(set().union(*(routes[i] for i in nb))) if nb else 0 for nb in neigh], np.int32
        )
        out[f"n_lines_tram_{r}"] = np.array(
            [len(set().union(*(routes_tram[i] for i in nb))) if nb else 0 for nb in neigh],
            np.int32,
        )
        out[f"dep_day_{r}"] = np.array([dep_day[nb].sum() for nb in neigh], np.int32)
        out[f"dep_peak_{r}"] = np.array([dep_peak[nb].sum() for nb in neigh], np.int32)
    return pd.DataFrame(out, index=points.index)
