"""Temporal hold-out and spatial block cross-validation for all model × variant pairs."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

from rcn_wroclaw import config
from rcn_wroclaw.model.dataset import apply_price_index, fit_price_index, spatial_blocks
from rcn_wroclaw.model.models import LGBMModel, make_ridge

log = logging.getLogger(__name__)

PREDICTIONS_PATH = config.PROCESSED_DIR / "predictions.parquet"
IMPORTANCE_PATH = config.PROCESSED_DIR / "lgbm_importance.parquet"
TEST_START = pd.Timestamp("2026-01-01")
VAL_MONTHS = 3
N_SPATIAL_FOLDS = 5
BLOCK_SIZE_M = 1000.0


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """MAE, RMSE, MAPE (%), R² on the original scale."""
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    err = y_pred - y_true
    ss_res = np.sum(err**2)
    ss_tot = np.sum((y_true - y_true.mean()) ** 2)
    return {
        "MAE": float(np.mean(np.abs(err))),
        "RMSE": float(np.sqrt(np.mean(err**2))),
        "MAPE": float(100 * np.mean(np.abs(err) / np.abs(y_true))),
        "R2": float(1 - ss_res / ss_tot),
    }


@dataclass
class SplitResult:
    predictions: list[pd.DataFrame] = field(default_factory=list)
    importance: list[pd.DataFrame] = field(default_factory=list)


def _with_index(
    train: pd.DataFrame, *others: pd.DataFrame, extrapolate: str = "carry"
) -> list[pd.DataFrame]:
    """Fit the monthly price index on ``train`` and apply it to all frames."""
    idx = fit_price_index(train["month"], train["log_price_m2"])
    out = []
    for df in (train, *others):
        df = df.copy()
        df["price_index"] = apply_price_index(df["month"], idx, extrapolate=extrapolate)
        out.append(df)
    return out


def fit_predict(
    train: pd.DataFrame,
    val_mask: np.ndarray,
    test: pd.DataFrame,
    variants: dict[str, tuple[list[str], list[str]]],
    split: str,
    fold: int,
    result: SplitResult,
    extrapolate: str = "carry",
) -> None:
    """Train Ridge + LightGBM for every variant; append test predictions to ``result``."""
    train, test = _with_index(train, test, extrapolate=extrapolate)
    y = train["log_price_m2"].to_numpy()
    base = test[["gid", "month", "lok_pow_uzyt", "price_m2", "is_primary", "osiedle"]].copy()
    base = base.assign(split=split, fold=fold)
    for name, (num, cat) in variants.items():
        t0 = time.time()
        ridge = make_ridge(num, cat).fit(train[num + cat], y)
        p_ridge = np.exp(ridge.predict(test[num + cat]))

        lgbm = train_lgbm(train, val_mask, num, cat)
        n_trees = lgbm.params["n_estimators"]
        p_lgbm = np.exp(lgbm.predict(test))

        for model, pred in (("ridge", p_ridge), ("lgbm", p_lgbm)):
            result.predictions.append(base.assign(model=model, variant=name, pred_m2=pred))
        imp = lgbm.importance().rename_axis("feature").reset_index(name="gain")
        result.importance.append(imp.assign(split=split, fold=fold, variant=name))
        m = metrics(test["price_m2"], p_lgbm)
        log.info(
            "%s fold %s %-16s ridge MAE %.0f | lgbm MAE %.0f (trees %d, alpha %.3g) %.0fs",
            split, fold, name, metrics(test["price_m2"], p_ridge)["MAE"], m["MAE"],
            n_trees, ridge.named_steps["ridge"].alpha_, time.time() - t0,
        )  # fmt: skip


def temporal_frames(df: pd.DataFrame, test_start=TEST_START):
    """Train / test frames of the temporal hold-out and the early-stopping validation mask
    (last VAL_MONTHS months of the training period)."""
    train = df[df["month"] < test_start].reset_index(drop=True)
    test = df[df["month"] >= test_start].reset_index(drop=True)
    last = train["month"].max()
    val_mask = (train["month"] > last - pd.DateOffset(months=VAL_MONTHS)).to_numpy()
    return train, test, val_mask


def train_lgbm(train: pd.DataFrame, val_mask: np.ndarray, num: list[str], cat: list[str]):
    """Same procedure as in :func:`fit_predict`: early stopping on the validation part, then
    refit on the whole training set with 1.1 × the best number of trees."""
    tr_in, val = train[~val_mask], train[val_mask]
    probe = LGBMModel(num, cat).fit(tr_in, tr_in["log_price_m2"], val, val["log_price_m2"])
    n_trees = max(50, int(probe.best_iteration * 1.1))
    model = LGBMModel(num, cat, params={"n_estimators": n_trees})
    return model.fit(train, train["log_price_m2"], val, val["log_price_m2"])


def temporal_holdout(
    df, variants, result: SplitResult, test_start=TEST_START, extrapolate: str = "carry"
) -> None:
    train, test, val_mask = temporal_frames(df, test_start)
    log.info("temporal: train %d (%s..%s), test %d", len(train), train["month"].min().date(),
             train["month"].max().date(), len(test))  # fmt: skip
    split = "temporal" if extrapolate == "carry" else f"temporal_{extrapolate}"
    fit_predict(train, val_mask, test, variants, split, 0, result, extrapolate=extrapolate)


def spatial_cv(df, variants, result: SplitResult, test_start=TEST_START) -> None:
    """Group K-fold over 1 km blocks, on the training period only (no future data)."""
    data = df[df["month"] < test_start].reset_index(drop=True)
    groups = spatial_blocks(data["x_2180"], data["y_2180"], BLOCK_SIZE_M)
    for k, (tr, te) in enumerate(GroupKFold(N_SPATIAL_FOLDS).split(data, groups=groups)):
        train, test = data.iloc[tr].reset_index(drop=True), data.iloc[te].reset_index(drop=True)
        g_tr = groups.iloc[tr].reset_index(drop=True)
        inner = GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=k)
        _, val_idx = next(inner.split(train, groups=g_tr))
        val_mask = np.zeros(len(train), dtype=bool)
        val_mask[val_idx] = True
        log.info("spatial fold %d: train %d, test %d (%d blocks)", k, len(train), len(test),
                 groups.iloc[te].nunique())  # fmt: skip
        fit_predict(train, val_mask, test, variants, "spatial_cv", k, result)


def summarize(pred: pd.DataFrame) -> pd.DataFrame:
    """Metrics per split × model × variant on price/m² and on total price."""
    rows = []
    for (split, model, variant), g in pred.groupby(["split", "model", "variant"]):
        m2 = metrics(g["price_m2"], g["pred_m2"])
        tot = metrics(g["price_m2"] * g["lok_pow_uzyt"], g["pred_m2"] * g["lok_pow_uzyt"])
        rows.append(
            {"split": split, "model": model, "variant": variant, "n": len(g), **m2,
             **{f"{k}_total": v for k, v in tot.items()}}
        )  # fmt: skip
    return pd.DataFrame(rows)
