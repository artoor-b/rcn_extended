"""Tests for feature grouping and SHAP helpers."""

import numpy as np
import pandas as pd
import pytest

from rcn_wroclaw.model.explain import feature_group, group_importance, shap_values
from rcn_wroclaw.model.models import LGBMModel


@pytest.mark.parametrize(
    ("name", "group"),
    [
        ("lok_pow_uzyt", "Lokal (RCN)"),
        ("is_primary", "Lokal (RCN)"),
        ("tran_sprzedajacy", "Strony transakcji (RCN)"),
        ("t_month", "Czas"),
        ("osiedle", "Osiedle"),
        ("walk_rynek_m", "Centrum"),
        ("dir_rynek_sin", "Centrum"),
        ("walk_tram_stop_m", "Komunikacja"),
        ("dep_day_500", "Komunikacja"),
        ("dist_rail_station_m", "Komunikacja"),
        ("cnt_nursery_500", "Edukacja"),
        ("walk_pharmacy_m", "Zdrowie"),
        ("cnt_parcel_locker_300", "Handel i usługi"),
        ("share_green_500", "Zieleń i woda"),
        ("walk_water_m", "Zieleń i woda"),
        ("dist_railway_line_m", "Uciążliwości"),
        ("share_industrial_500", "Uciążliwości"),
        ("x_2180", "Współrzędne"),
        ("walk_snap_m", "Inne"),
    ],
)
def test_feature_group(name, group):
    assert feature_group(name) == group


def test_group_importance_sums_within_group_first():
    shap = pd.DataFrame(
        {
            "walk_school_m": [0.1, -0.1],
            "cnt_school_500": [-0.1, 0.1],
            "t_month": [0.2, -0.2],
            "bias": [9.0, 9.0],
        }
    )
    imp = group_importance(shap)
    # school effects cancel within the group -> 0; time -> 0.2
    assert imp.loc["Edukacja", "mean_abs_shap"] == pytest.approx(0.0)
    assert imp.loc["Czas", "mean_abs_shap"] == pytest.approx(0.2)
    assert imp["share_%"].sum() == pytest.approx(100)


def test_shap_values_add_up():
    rng = np.random.default_rng(0)
    X = pd.DataFrame(
        {"a": rng.normal(size=300), "b": rng.normal(size=300), "c": rng.choice(["x", "y"], 300)}
    )
    y = 2 * X["a"] + (X["c"] == "x") + rng.normal(0, 0.1, 300)
    m = LGBMModel(["a", "b"], ["c"], params={"n_estimators": 50}).fit(X, y, X, y)
    shap = shap_values(m, X)
    np.testing.assert_allclose(shap.sum(axis=1), m.predict(X), atol=1e-6)
    assert shap["a"].abs().mean() > shap["b"].abs().mean()
