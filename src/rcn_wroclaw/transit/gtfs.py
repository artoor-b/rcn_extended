"""GTFS (MPK Wrocław) -> stop-level service indicators for a representative weekday.

Output: one row per GTFS stop (stop pole) in EPSG:2180 with the number of distinct lines
and the number of departures, split by mode. Feature engineering (nearest stops, buffers)
happens in a later stage.
"""

from __future__ import annotations

import datetime as dt
import logging
import zipfile
from pathlib import Path

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd

from rcn_wroclaw import config

log = logging.getLogger(__name__)

REQUIRED = ("stops.txt", "routes.txt", "trips.txt", "stop_times.txt", "calendar.txt")
NIGHT_ROUTE_TYPE2 = "40"  # MPK extension: route_type2_id 40 = "Nocna autobusowa"
# A line "regularly serves" a stop if it has at least MIN_DEPARTURES departures there and at
# least MIN_SHARE_OF_TYPICAL of its median per-stop departures. Without this, stops next to
# depots (e.g. Bezpieczna, Paprotna) collect ~44 "lines" from depot runs.
MIN_DEPARTURES = 4
MIN_SHARE_OF_TYPICAL = 0.2
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def extract(zip_path: Path, out_dir: Path | None = None) -> Path:
    """Extract the GTFS text files we need next to the zip; return the folder."""
    out_dir = out_dir or zip_path.parent / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        missing = [n for n in REQUIRED if n not in names]
        if missing:
            raise ValueError(f"GTFS feed lacks {missing}")
        for name in (*REQUIRED, "calendar_dates.txt", "feed_info.txt"):
            if name in names and not (out_dir / name).exists():
                # Only flat, known file names are extracted (no paths from the archive).
                (out_dir / name).write_bytes(zf.read(name))
    return out_dir


def feed_period(gtfs_dir: Path) -> tuple[dt.date, dt.date]:
    """Validity period from calendar.txt (min start_date, max end_date)."""
    cal = pd.read_csv(gtfs_dir / "calendar.txt", dtype=str, encoding="utf-8-sig")
    start = pd.to_datetime(cal["start_date"], format="%Y%m%d").min().date()
    end = pd.to_datetime(cal["end_date"], format="%Y%m%d").max().date()
    return start, end


def representative_weekday(gtfs_dir: Path) -> dt.date:
    """First Wednesday inside the feed validity period (avoids Mon/Fri edge effects)."""
    start, end = feed_period(gtfs_dir)
    d = start
    while d.weekday() != 2:
        d += dt.timedelta(days=1)
    if d > end:
        raise ValueError(f"no Wednesday within feed period {start}..{end}")
    return d


def active_services(gtfs_dir: Path, day: dt.date) -> list[str]:
    """Service ids running on ``day`` (calendar.txt + calendar_dates.txt exceptions)."""
    cal = pd.read_csv(gtfs_dir / "calendar.txt", dtype=str, encoding="utf-8-sig")
    ymd = day.strftime("%Y%m%d")
    weekday_col = WEEKDAYS[day.weekday()]
    on = cal[(cal[weekday_col] == "1") & (cal["start_date"] <= ymd) & (cal["end_date"] >= ymd)]
    services = set(on["service_id"])
    cd_path = gtfs_dir / "calendar_dates.txt"
    if cd_path.exists():
        cd = pd.read_csv(cd_path, dtype=str, encoding="utf-8-sig")
        cd = cd[cd["date"] == ymd] if len(cd) else cd
        services |= set(cd.loc[cd["exception_type"] == "1", "service_id"])
        services -= set(cd.loc[cd["exception_type"] == "2", "service_id"])
    return sorted(services)


def stop_service_levels(
    gtfs_dir: Path,
    day: dt.date | None = None,
    min_departures: int = MIN_DEPARTURES,
    min_share_of_typical: float = MIN_SHARE_OF_TYPICAL,
) -> gpd.GeoDataFrame:
    """Compute per-stop line counts and departures for one service day.

    Only (line, stop) pairs passing the regular-service rule are counted (see constants).
    """
    day = day or representative_weekday(gtfs_dir)
    services = active_services(gtfs_dir, day)
    if not services:
        raise ValueError(f"no active services on {day}")
    log.info("GTFS service day %s: services %s", day, services)

    con = duckdb.connect()
    for name in ("stops", "routes", "trips", "stop_times"):
        con.execute(
            f"CREATE VIEW {name} AS SELECT * FROM read_csv('{gtfs_dir / (name + '.txt')}', "
            "header=true, all_varchar=true)"
        )
    con.execute("CREATE TABLE svc(service_id VARCHAR)")
    con.executemany("INSERT INTO svc VALUES (?)", [(s,) for s in services])
    df = con.execute(
        f"""
        WITH dep_all AS (
            SELECT st.stop_id,
                   r.route_id,
                   r.route_type,
                   coalesce(r.route_type2_id, '') AS route_type2_id,
                   CAST(split_part(st.departure_time, ':', 1) AS INTEGER) AS hour
            FROM stop_times st
            JOIN trips t USING (trip_id)
            JOIN svc USING (service_id)
            JOIN routes r USING (route_id)
            WHERE coalesce(st.pickup_type, '0') <> '1'   -- boarding allowed
        ),
        pair AS (   -- departures of a line at a stop
            SELECT route_id, stop_id, count(*) AS n FROM dep_all GROUP BY ALL
        ),
        route_typical AS (   -- typical (median) departures of a line per served stop
            SELECT route_id, median(n) AS n_med FROM pair GROUP BY route_id
        ),
        regular AS (   -- drops depot runs / short-turn trips that only touch a stop rarely
            SELECT p.route_id, p.stop_id
            FROM pair p JOIN route_typical rt USING (route_id)
            WHERE p.n >= {int(min_departures)} AND p.n >= {float(min_share_of_typical)} * rt.n_med
        ),
        dep AS (
            SELECT d.* FROM dep_all d JOIN regular USING (route_id, stop_id)
        )
        SELECT s.stop_id,
               s.stop_code,
               s.stop_name,
               CAST(s.stop_lat AS DOUBLE) AS lat,
               CAST(s.stop_lon AS DOUBLE) AS lon,
               count(DISTINCT d.route_id) AS n_lines,
               count(DISTINCT d.route_id) FILTER (WHERE d.route_type = '0') AS n_lines_tram,
               count(DISTINCT d.route_id) FILTER (
                   WHERE d.route_type = '3' AND d.route_type2_id <> '{NIGHT_ROUTE_TYPE2}'
               ) AS n_lines_bus_day,
               count(DISTINCT d.route_id) FILTER (
                   WHERE d.route_type2_id = '{NIGHT_ROUTE_TYPE2}'
               ) AS n_lines_night,
               count(d.route_id) AS dep_day,
               count(d.route_id) FILTER (WHERE d.route_type = '0') AS dep_day_tram,
               count(d.route_id) FILTER (WHERE d.hour >= 6 AND d.hour < 22) AS dep_6_22,
               count(d.route_id) FILTER (WHERE d.hour >= 7 AND d.hour < 9) AS dep_peak_7_9,
               -- line sets, needed to count distinct lines over several nearby stops
               list_sort(list(DISTINCT d.route_id) FILTER (WHERE d.route_id IS NOT NULL))
                   AS routes,
               list_sort(list(DISTINCT d.route_id) FILTER (WHERE d.route_type = '0'))
                   AS routes_tram
        FROM stops s
        LEFT JOIN dep d USING (stop_id)
        GROUP BY ALL
        """
    ).df()
    con.close()
    gdf = gpd.GeoDataFrame(
        df, geometry=gpd.points_from_xy(df["lon"], df["lat"]), crs="EPSG:4326"
    ).to_crs(config.CRS)
    gdf["service_date"] = pd.Timestamp(day)
    count_cols = [c for c in gdf.columns if c.startswith(("n_lines", "dep_"))]
    gdf[count_cols] = gdf[count_cols].fillna(0).astype("int32")
    for col in ("routes", "routes_tram"):
        gdf[col] = gdf[col].apply(
            lambda v: [str(x) for x in v] if isinstance(v, list | tuple | np.ndarray) else []
        )
    return gdf.drop(columns=["lat", "lon"])
