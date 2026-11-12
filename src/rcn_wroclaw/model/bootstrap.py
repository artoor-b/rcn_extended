"""Paired cluster bootstrap for comparing two models evaluated on the same transactions.

Transactions in one building (or one spatial block) are not independent, so whole clusters
are resampled. Both models are evaluated on the same resampled clusters (paired design),
which gives confidence intervals for the metric *difference*.

Implementation: per-cluster sufficient statistics (n, Σ|e|, Σe², Σ|e|/y, Σy, Σy²) and
multinomial cluster weights, so B replicates are one matrix product.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

METRICS = ("MAE", "RMSE", "MAPE", "R2")


def _cluster_stats(y: np.ndarray, pred: np.ndarray, codes: np.ndarray, n_clusters: int):
    e = pred - y
    cols = [np.ones_like(y), np.abs(e), e**2, np.abs(e) / np.abs(y), y, y**2]
    return np.column_stack([np.bincount(codes, weights=c, minlength=n_clusters) for c in cols])


def _metrics_from_stats(s: np.ndarray) -> dict[str, np.ndarray]:
    """``s``: (..., 6) aggregated statistics -> metric arrays."""
    n, sae, sse, sape, sy, sy2 = (s[..., i] for i in range(6))
    sst = sy2 - sy**2 / n
    return {
        "MAE": sae / n,
        "RMSE": np.sqrt(sse / n),
        "MAPE": 100 * sape / n,
        "R2": 1 - sse / sst,
    }


def paired_cluster_bootstrap(
    y: np.ndarray,
    pred_a: np.ndarray,
    pred_b: np.ndarray,
    clusters: np.ndarray,
    n_boot: int = 2000,
    seed: int = 0,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """Metrics of A and B and of the difference B − A with percentile CIs.

    Returns one row per metric with columns: ``A``, ``B``, ``diff``, ``diff_lo``,
    ``diff_hi``, ``A_lo``, ``A_hi``, ``B_lo``, ``B_hi`` and ``p_b_not_better`` – the
    bootstrap share of replicates in which B is not better than A (MAE/RMSE/MAPE: diff ≥ 0,
    R²: diff ≤ 0), a one-sided bootstrap p-value.
    """
    y, pred_a, pred_b = (np.asarray(v, float) for v in (y, pred_a, pred_b))
    codes, _ = pd.factorize(pd.Series(clusters))
    c = int(codes.max()) + 1
    sa = _cluster_stats(y, pred_a, codes, c)
    sb = _cluster_stats(y, pred_b, codes, c)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(c, np.full(c, 1.0 / c), size=n_boot).astype(float)  # (B, C)
    ma, mb = _metrics_from_stats(w @ sa), _metrics_from_stats(w @ sb)
    pa, pb = _metrics_from_stats(sa.sum(0)), _metrics_from_stats(sb.sum(0))
    q = [100 * alpha / 2, 100 * (1 - alpha / 2)]
    rows = []
    for m in METRICS:
        d = mb[m] - ma[m]
        worse = d <= 0 if m == "R2" else d >= 0
        rows.append(
            {
                "metric": m,
                "A": float(pa[m]),
                "B": float(pb[m]),
                "diff": float(pb[m] - pa[m]),
                "diff_lo": float(np.percentile(d, q[0])),
                "diff_hi": float(np.percentile(d, q[1])),
                "A_lo": float(np.percentile(ma[m], q[0])),
                "A_hi": float(np.percentile(ma[m], q[1])),
                "B_lo": float(np.percentile(mb[m], q[0])),
                "B_hi": float(np.percentile(mb[m], q[1])),
                "p_b_not_better": float(worse.mean()),
            }
        )
    return pd.DataFrame(rows).set_index("metric")


def compare(
    pred: pd.DataFrame,
    split: str,
    model: str,
    variant_a: str,
    variant_b: str,
    cluster_col: str,
    mask: pd.Series | None = None,
    **kwargs,
) -> pd.DataFrame:
    """Align two variants' predictions on ``gid`` within a split/model and bootstrap them."""
    sub = pred[(pred["split"] == split) & (pred["model"] == model)]
    if mask is not None:
        sub = sub[mask.reindex(sub.index, fill_value=False)]
    a = sub[sub["variant"] == variant_a].set_index("gid")
    b = sub[sub["variant"] == variant_b].set_index("gid")
    common = a.index.intersection(b.index)
    a, b = a.loc[common], b.loc[common]
    out = paired_cluster_bootstrap(
        a["price_m2"].to_numpy(), a["pred_m2"].to_numpy(), b["pred_m2"].to_numpy(),
        a[cluster_col].to_numpy(), **kwargs,
    )  # fmt: skip
    return out.assign(n=len(common), n_clusters=a[cluster_col].nunique())
