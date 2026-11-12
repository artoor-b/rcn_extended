"""Ridge baseline and LightGBM, both trained on log(price/m²)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

LOG_PREFIXES = ("dist_", "walk_", "cnt_", "n_lines", "dep_", "lok_pow_uzyt", "lok_pow_przyn")
RIDGE_ALPHAS = np.logspace(-2, 3, 21)

LGBM_PARAMS = dict(
    objective="regression",
    learning_rate=0.03,
    num_leaves=63,
    min_child_samples=30,
    subsample=0.8,
    subsample_freq=1,
    colsample_bytree=0.8,
    reg_lambda=1.0,
    n_estimators=5000,
    random_state=42,
    verbose=-1,
)
EARLY_STOPPING_ROUNDS = 200


def make_ridge(num: list[str], cat: list[str]) -> Pipeline:
    """Median imputation, log1p on skewed distance/count features, scaling, one-hot."""
    log_cols = [c for c in num if c.startswith(LOG_PREFIXES)]
    lin_cols = [c for c in num if c not in log_cols]
    pre = ColumnTransformer(
        [
            (
                "log",
                make_pipeline(
                    SimpleImputer(strategy="median"),
                    FunctionTransformer(np.log1p, feature_names_out="one-to-one"),
                    StandardScaler(),
                ),
                log_cols,
            ),
            ("lin", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), lin_cols),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), cat),
        ]
    )
    return Pipeline([("pre", pre), ("ridge", RidgeCV(alphas=RIDGE_ALPHAS))])


def _lgb_frame(X: pd.DataFrame, cat: list[str], categories: dict[str, list] | None) -> pd.DataFrame:
    X = X.copy()
    for c in cat:
        X[c] = pd.Categorical(X[c], categories=categories[c] if categories else None)
    return X


class LGBMModel:
    """LightGBM with early stopping on a caller-provided validation set."""

    def __init__(self, num: list[str], cat: list[str], params: dict | None = None):
        self.num, self.cat = num, cat
        self.params = {**LGBM_PARAMS, **(params or {})}
        self.categories: dict[str, list] = {}
        self.model = None  # lightgbm.LGBMRegressor (imported lazily: needs libomp on macOS)

    @property
    def features(self) -> list[str]:
        return self.num + self.cat

    def fit(self, X: pd.DataFrame, y, X_val: pd.DataFrame, y_val) -> LGBMModel:
        import lightgbm as lgb

        self.categories = {c: sorted(X[c].dropna().unique().tolist()) for c in self.cat}
        Xt = _lgb_frame(X[self.features], self.cat, self.categories)
        Xv = _lgb_frame(X_val[self.features], self.cat, self.categories)
        self.model = lgb.LGBMRegressor(**self.params)
        self.model.fit(
            Xt,
            y,
            eval_X=(Xv,),
            eval_y=(y_val,),
            eval_metric="l1",
            callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
        )
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict(_lgb_frame(X[self.features], self.cat, self.categories))

    @property
    def best_iteration(self) -> int:
        return int(self.model.best_iteration_ or self.model.n_estimators)

    def importance(self) -> pd.Series:
        booster = self.model.booster_
        return pd.Series(
            booster.feature_importance("gain"), index=booster.feature_name()
        ).sort_values(ascending=False)
