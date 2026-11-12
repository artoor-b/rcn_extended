"""Tests for the paired cluster bootstrap."""

import numpy as np
import pandas as pd
import pytest

from rcn_wroclaw.model.bootstrap import compare, paired_cluster_bootstrap
from rcn_wroclaw.model.evaluate import metrics


def data(n_clusters=200, per=5, seed=0):
    rng = np.random.default_rng(seed)
    clusters = np.repeat(np.arange(n_clusters), per)
    shared = np.repeat(rng.normal(0, 500, n_clusters), per)  # cluster-level error
    y = rng.uniform(8000, 16000, n_clusters * per)
    pred_a = y + shared + rng.normal(0, 800, len(y))
    pred_b = y + 0.5 * shared + rng.normal(0, 600, len(y))
    return y, pred_a, pred_b, clusters


def test_point_estimates_match_metrics():
    y, a, b, cl = data()
    out = paired_cluster_bootstrap(y, a, b, cl, n_boot=200)
    for m, v in metrics(y, a).items():
        assert out.loc[m, "A"] == pytest.approx(v)
    for m, v in metrics(y, b).items():
        assert out.loc[m, "B"] == pytest.approx(v)


def test_identical_models_have_zero_difference():
    y, a, _, cl = data()
    out = paired_cluster_bootstrap(y, a, a, cl, n_boot=200)
    assert (out["diff"].abs() < 1e-9).all()
    assert (out["diff_lo"].abs() < 1e-9).all() and (out["diff_hi"].abs() < 1e-9).all()


def test_better_model_detected():
    y, a, b, cl = data()
    out = paired_cluster_bootstrap(y, a, b, cl, n_boot=500)
    assert out.loc["MAE", "diff_hi"] < 0
    assert out.loc["R2", "diff_lo"] > 0
    assert out.loc["MAE", "p_b_not_better"] < 0.01


def test_clustered_ci_wider_than_iid():
    # errors almost constant within a cluster -> effective sample size = number of clusters
    rng = np.random.default_rng(3)
    cl = np.repeat(np.arange(100), 10)
    y = np.full(len(cl), 10_000.0)
    a = y + np.repeat(rng.exponential(1000, 100), 10) + rng.normal(0, 50, len(cl))
    clustered = paired_cluster_bootstrap(y, a, a + 1, cl, n_boot=1000, seed=1)
    iid = paired_cluster_bootstrap(y, a, a + 1, np.arange(len(y)), n_boot=1000, seed=1)
    w_cl = clustered.loc["MAE", "A_hi"] - clustered.loc["MAE", "A_lo"]
    w_iid = iid.loc["MAE", "A_hi"] - iid.loc["MAE", "A_lo"]
    assert w_cl > 2 * w_iid  # ~sqrt(10) in theory


def test_compare_aligns_on_gid():
    y, a, b, cl = data(n_clusters=50, per=4)
    n = len(y)
    rows = []
    for variant, p in (("base", a), ("loc", b)):
        rows.append(
            pd.DataFrame(
                {
                    "gid": np.arange(n),
                    "split": "temporal",
                    "model": "lgbm",
                    "variant": variant,
                    "price_m2": y,
                    "pred_m2": p,
                    "location_id": cl,
                }
            )
        )
    pred = pd.concat(rows, ignore_index=True).sample(frac=1, random_state=0)  # shuffled
    out = compare(pred, "temporal", "lgbm", "base", "loc", "location_id", n_boot=100)
    assert out.loc["MAE", "A"] == pytest.approx(metrics(y, a)["MAE"])
    assert out["n"].iloc[0] == n and out["n_clusters"].iloc[0] == 50
