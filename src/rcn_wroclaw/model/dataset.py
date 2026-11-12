"""Model dataset: cleaned transactions + location features, feature variants, price index."""

from __future__ import annotations

import re

import geopandas as gpd
import numpy as np
import pandas as pd

from rcn_wroclaw import config
from rcn_wroclaw.clean.rcn import CleaningLog, clean_transactions
from rcn_wroclaw.features.build import FEATURES_PATH
from rcn_wroclaw.features.euclidean import location_id

DATASET_PATH = config.PROCESSED_DIR / "model_dataset.parquet"
TIME_ORIGIN = pd.Timestamp("2023-01-01")

# --- feature groups -----------------------------------------------------------------------
RCN_NUM = [
    "lok_pow_uzyt",
    "lok_nr_kond",
    "lok_liczba_izb",
    "area_per_room",
    "lok_pow_przyn",
    "has_pow_przyn",
    "has_parking_note",
    "is_primary",
    "t_month",
    "price_index",
]
RCN_CAT = ["tran_sprzedajacy", "tran_kupujacy"]
AREA_CAT = ["osiedle"]
XY = ["x_2180", "y_2180"]
NON_LOCATION = {"location_id", "osiedle", "geometry", *XY}


def location_columns(features: pd.DataFrame) -> list[str]:
    """Numeric accessibility features (everything in the location table except ids/x/y)."""
    return [c for c in features.columns if c not in NON_LOCATION]


def select_location_columns(loc_cols: list[str], radius: int = 500) -> list[str]:
    """Compact, less collinear subset: one radius per buffer family, walking distance instead
    of the straight-line one where both exist, departures per day (not peak), no snap offset."""
    walk_targets = {c.removeprefix("walk_") for c in loc_cols if c.startswith("walk_")}
    keep = []
    for c in loc_cols:
        if c == "walk_snap_m" or c.startswith("dep_peak_"):
            continue
        if c.startswith("dist_") and c.removeprefix("dist_") in walk_targets:
            continue
        m = re.search(r"_(300|500|1000)$", c)
        if m and int(m.group(1)) != radius:
            continue
        keep.append(c)
    return keep


def variants(loc_cols: list[str]) -> dict[str, tuple[list[str], list[str]]]:
    """Feature sets compared in the study: name -> (numeric, categorical)."""
    return {
        "rcn_bez_osiedla": (RCN_NUM, RCN_CAT),
        "rcn": (RCN_NUM, RCN_CAT + AREA_CAT),
        "rcn_lok": (RCN_NUM + loc_cols, RCN_CAT + AREA_CAT),
        "rcn_lok_sel": (RCN_NUM + select_location_columns(loc_cols), RCN_CAT + AREA_CAT),
        "rcn_lok_xy": (RCN_NUM + loc_cols + XY, RCN_CAT + AREA_CAT),
    }


# --- price index ----------------------------------------------------------------------------
def fit_price_index(month: pd.Series, log_price: pd.Series, window: int = 3) -> pd.Series:
    """Monthly log price index from TRAINING data: centred rolling median of monthly medians
    (``window`` months), indexed by month start."""
    monthly = log_price.groupby(month).median().sort_index()
    full = pd.date_range(monthly.index.min(), monthly.index.max(), freq="MS")
    monthly = monthly.reindex(full).interpolate()
    return monthly.rolling(window, center=True, min_periods=1).median()


def apply_price_index(
    month: pd.Series, index: pd.Series, extrapolate: str = "carry", trend_months: int = 12
) -> np.ndarray:
    """Look up the index for ``month``. Months before the fitted range take the first value.
    Months after it either carry the last value forward (``"carry"``) or follow a linear
    trend fitted on the last ``trend_months`` of the index (``"trend"``). Only the fitted
    (training) index is used, so no information from the future leaks in."""
    idx = index.sort_index()
    pos = idx.index.searchsorted(month.to_numpy(), side="right") - 1
    pos = np.clip(pos, 0, len(idx) - 1)
    out = idx.to_numpy()[pos].astype(float)
    if extrapolate == "trend":
        tail = idx.iloc[-trend_months:]
        slope = np.polyfit(np.arange(len(tail)), tail.to_numpy(), 1)[0]  # per month
        last = idx.index[-1]
        m = pd.DatetimeIndex(month)
        ahead = (m.year - last.year) * 12 + (m.month - last.month)
        out = out + slope * np.clip(np.asarray(ahead), 0, None)
    elif extrapolate != "carry":
        raise ValueError(f"unknown extrapolate={extrapolate!r}")
    return out


# --- dataset --------------------------------------------------------------------------------
def build_dataset() -> tuple[pd.DataFrame, CleaningLog]:
    """Clean RCN, join location features, add RCN-derived model columns."""
    tx, log = clean_transactions(gpd.read_parquet(config.RCN_LOKALE_INTERIM))
    tx["location_id"] = location_id(tx.geometry.x.to_numpy(), tx.geometry.y.to_numpy())
    feats = pd.DataFrame(gpd.read_parquet(FEATURES_PATH).drop(columns="geometry"))
    df = pd.DataFrame(tx.drop(columns="geometry")).merge(feats, on="location_id", how="left")
    missing = df["dist_rynek_m"].isna().sum()
    if missing:
        raise ValueError(f"{missing} transactions without location features")
    df["log_price_m2"] = np.log(df["price_m2"].astype(float))
    df["is_primary"] = (df["tran_rodzaj_rynku"] == "rynekPierwotny").astype(int)
    df["has_parking_note"] = df["has_parking_note"].astype(int)
    df["has_pow_przyn"] = df["lok_pow_przyn"].notna().astype(int)
    df["lok_pow_przyn"] = df["lok_pow_przyn"].fillna(0).astype(float)
    rooms = df["lok_liczba_izb"].astype(float)
    df["area_per_room"] = df["lok_pow_uzyt"].astype(float) / rooms.where(rooms > 0)
    df["t_month"] = (
        (df["month"].dt.year - TIME_ORIGIN.year) * 12 + df["month"].dt.month - TIME_ORIGIN.month
    )
    for c in ("lok_pow_uzyt", "lok_nr_kond", "lok_liczba_izb", "price_m2", "price_total"):
        df[c] = df[c].astype(float)
    for c in RCN_CAT + AREA_CAT:
        df[c] = df[c].astype("string").fillna("brak")
    df["price_index"] = np.nan  # filled per split from training data only
    return df.reset_index(drop=True), log


def spatial_blocks(x: pd.Series, y: pd.Series, size_m: float = 1000.0) -> pd.Series:
    """Square grid block id used as the group for spatial cross-validation."""
    return (x // size_m).astype(int).astype(str) + "_" + (y // size_m).astype(int).astype(str)
