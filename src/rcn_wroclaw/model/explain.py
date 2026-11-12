"""SHAP explanations (LightGBM native TreeSHAP, ``pred_contrib=True``) and feature groups.

Contributions are in log(price/m²) units; a contribution s corresponds to roughly a
100·(exp(s) − 1) % change of the predicted price relative to the model's average.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from rcn_wroclaw import config
from rcn_wroclaw.model.evaluate import _with_index, temporal_frames, train_lgbm
from rcn_wroclaw.model.models import LGBMModel, _lgb_frame

MODELS_DIR = config.PROCESSED_DIR / "models"
SHAP_PATH = config.PROCESSED_DIR / "shap_temporal.parquet"

GROUP_RULES: list[tuple[str, str]] = [
    ("Czas", r"^(t_month|price_index)$"),
    ("Osiedle", r"^osiedle$"),
    ("Współrzędne", r"^(x_2180|y_2180)$"),
    ("Lokal (RCN)", r"^(lok_|area_per_room|has_pow_przyn|has_parking_note|is_primary)"),
    ("Strony transakcji (RCN)", r"^tran_"),
    ("Centrum", r"(rynek)"),
    ("Komunikacja", r"(stop|n_lines|dep_|rail_station)"),
    ("Edukacja", r"(school|kindergarten|nursery|university)"),
    ("Zdrowie", r"(clinic|hospital|pharmacy)"),
    ("Handel i usługi", r"(supermarket|convenience|mall|food_drink|culture|post_office|"
                        r"parcel_locker|sport|playground)"),
    ("Zieleń i woda", r"(park|forest|water|share_green)"),
    ("Uciążliwości", r"(industrial|major_road|railway_line|cemetery)"),
    ("Inne", r"."),
]  # fmt: skip
RCN_GROUPS = {"Czas", "Osiedle", "Lokal (RCN)", "Strony transakcji (RCN)"}


def feature_group(name: str) -> str:
    for group, pattern in GROUP_RULES:
        if re.search(pattern, name):
            return group
    return "Inne"


def shap_values(model: LGBMModel, X: pd.DataFrame) -> pd.DataFrame:
    """Exact TreeSHAP contributions per feature (+ ``bias`` column)."""
    Xf = _lgb_frame(X[model.features], model.cat, model.categories)
    contrib = model.model.booster_.predict(Xf, pred_contrib=True)
    return pd.DataFrame(contrib, columns=[*model.features, "bias"], index=X.index)


def group_importance(shap: pd.DataFrame) -> pd.DataFrame:
    """Mean |SHAP| per feature group (contributions summed within a group first)."""
    feats = [c for c in shap.columns if c != "bias"]
    groups = pd.Series({f: feature_group(f) for f in feats})
    summed = shap[feats].T.groupby(groups).sum().T  # rows = observations
    imp = summed.abs().mean().sort_values(ascending=False)
    out = imp.rename("mean_abs_shap").to_frame()
    out["share_%"] = 100 * out["mean_abs_shap"] / out["mean_abs_shap"].sum()
    out["approx_%_price"] = 100 * (np.exp(out["mean_abs_shap"]) - 1)
    return out


def explain_temporal(df: pd.DataFrame, num: list[str], cat: list[str], name: str):
    """Train the temporal-split LightGBM for a variant, save it, return SHAP on the test set."""
    train, test, val_mask = temporal_frames(df)
    train, test = _with_index(train, test)
    model = train_lgbm(train, val_mask, num, cat)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model.model.booster_.save_model(str(MODELS_DIR / f"lgbm_{name}_temporal.txt"))
    shap = shap_values(model, test)
    check = shap.sum(axis=1).to_numpy() - model.predict(test)
    if np.abs(check).max() > 1e-6:
        raise AssertionError("SHAP contributions do not add up to predictions")
    return model, test, shap
