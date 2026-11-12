"""Tests for the model dataset helpers, metrics and a small end-to-end fit."""

import numpy as np
import pandas as pd
import pytest

from rcn_wroclaw.model.dataset import (
    apply_price_index,
    fit_price_index,
    select_location_columns,
    spatial_blocks,
    variants,
)
from rcn_wroclaw.model.evaluate import SplitResult, fit_predict, metrics, summarize
from rcn_wroclaw.model.models import make_ridge


def test_metrics_known_values():
    m = metrics(np.array([100.0, 200.0]), np.array([110.0, 180.0]))
    assert m["MAE"] == pytest.approx(15)
    assert m["RMSE"] == pytest.approx(np.sqrt((100 + 400) / 2))
    assert m["MAPE"] == pytest.approx(100 * (0.1 + 0.1) / 2)
    assert m["R2"] == pytest.approx(1 - 500 / 5000)


def test_price_index_no_future_information():
    months = pd.Series(pd.to_datetime(["2023-01-01", "2023-02-01", "2023-04-01"]))
    logp = pd.Series([1.0, 2.0, 4.0])
    idx = fit_price_index(months, logp, window=1)
    assert idx.loc["2023-03-01"] == pytest.approx(3.0)  # gap interpolated
    looked = apply_price_index(
        pd.Series(pd.to_datetime(["2022-12-01", "2023-02-01", "2026-06-01"])), idx
    )
    np.testing.assert_allclose(looked, [1.0, 2.0, 4.0])  # before -> first, after -> last


def test_spatial_blocks():
    b = spatial_blocks(pd.Series([999.0, 1000.0, 1500.0]), pd.Series([0.0, 0.0, 2500.0]))
    assert b.tolist() == ["0_0", "1_0", "1_2"]


def test_variants_are_nested():
    v = variants(["dist_a_m", "cnt_b_300"])
    assert set(v["rcn"][0]) == set(v["rcn_bez_osiedla"][0])
    assert "osiedle" in v["rcn"][1] and "osiedle" not in v["rcn_bez_osiedla"][1]
    assert set(v["rcn"][0]) < set(v["rcn_lok"][0]) < set(v["rcn_lok_xy"][0])


def synthetic(n=600, seed=0):
    rng = np.random.default_rng(seed)
    dist = rng.uniform(100, 3000, n)
    area = rng.uniform(30, 90, n)
    months = pd.to_datetime("2023-01-01") + pd.to_timedelta(rng.integers(0, 24, n) * 30, "D")
    months = months.to_period("M").to_timestamp()
    price = 15000 - 2 * dist + rng.normal(0, 300, n)
    return pd.DataFrame(
        {
            "gid": np.arange(n),
            "month": months,
            "lok_pow_uzyt": area,
            "price_m2": price,
            "log_price_m2": np.log(price),
            "is_primary": rng.integers(0, 2, n),
            "osiedle": rng.choice(["A", "B", "C"], n),
            "dist_x_m": dist,
            "t_month": 0,
            "price_index": np.nan,
        }
    )


def test_ridge_learns_location_signal():
    df = synthetic()
    df["price_index"] = 0.0
    base = make_ridge(["lok_pow_uzyt"], ["osiedle"]).fit(df, df["log_price_m2"])
    loc = make_ridge(["lok_pow_uzyt", "dist_x_m"], ["osiedle"]).fit(df, df["log_price_m2"])
    mae_base = metrics(df["price_m2"], np.exp(base.predict(df)))["MAE"]
    mae_loc = metrics(df["price_m2"], np.exp(loc.predict(df)))["MAE"]
    assert mae_loc < 0.5 * mae_base


def _lightgbm_available() -> bool:
    try:
        import lightgbm  # noqa: F401
    except (ImportError, OSError):  # OSError: missing libomp on macOS
        return False
    return True


@pytest.mark.skipif(not _lightgbm_available(), reason="lightgbm/libomp not available")
def test_fit_predict_end_to_end():
    df = synthetic()
    train, test = df.iloc[:500].reset_index(drop=True), df.iloc[500:].reset_index(drop=True)
    val_mask = np.zeros(len(train), bool)
    val_mask[-80:] = True
    var = {
        "base": (["lok_pow_uzyt", "price_index"], ["osiedle"]),
        "loc": (["lok_pow_uzyt", "price_index", "dist_x_m"], ["osiedle"]),
    }
    res = SplitResult()
    fit_predict(train, val_mask, test, var, "temporal", 0, res)
    s = summarize(pd.concat(res.predictions)).set_index(["model", "variant"])
    assert len(s) == 4
    for model in ("ridge", "lgbm"):
        assert s.loc[(model, "loc"), "MAE"] < s.loc[(model, "base"), "MAE"]


def test_price_index_trend_extrapolation():
    months = pd.Series(pd.date_range("2023-01-01", periods=12, freq="MS"))
    idx = fit_price_index(months, pd.Series(np.arange(12, dtype=float)), window=1)
    future = pd.Series(pd.to_datetime(["2023-12-01", "2024-03-01"]))
    np.testing.assert_allclose(apply_price_index(future, idx, "carry"), [11, 11])
    np.testing.assert_allclose(apply_price_index(future, idx, "trend"), [11, 14])
    with pytest.raises(ValueError):
        apply_price_index(future, idx, "magic")


def test_select_location_columns():
    cols = [
        "dist_school_m",
        "walk_school_m",
        "cnt_school_300",
        "cnt_school_500",
        "cnt_school_1000",
        "dist_industrial_m",
        "dep_day_500",
        "dep_peak_500",
        "walk_snap_m",
        "share_green_1000",
        "share_green_500",
    ]
    assert select_location_columns(cols) == [
        "walk_school_m",
        "cnt_school_500",
        "dist_industrial_m",
        "dep_day_500",
        "share_green_500",
    ]
