"""Derived columns on RCN unit records used for exploration (no filtering here)."""

from __future__ import annotations

import pandas as pd


def add_transaction_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Add the number of unit records per transaction.

    ``n_units_in_trans`` counts all ``ms:lokale`` records sharing ``tran_lokalny_id_iip``
    (any function: apartments, garages, ...); ``n_flats_in_trans`` counts residential ones.
    """
    out = df.copy()
    key = "tran_lokalny_id_iip"
    out["n_units_in_trans"] = out.groupby(key)[key].transform("size").astype("Int64")
    is_flat = (out["lok_funkcja"] == "mieszkalna").fillna(False).astype(int)
    out["n_flats_in_trans"] = is_flat.groupby(out[key]).transform("sum").astype("Int64")
    return out


def add_price_per_m2(df: pd.DataFrame) -> pd.DataFrame:
    """Add candidate price-per-m² columns.

    - ``price_m2_lok``: unit price (``lok_cena_brutto``) / usable area.
    - ``price_m2_tran``: whole-transaction price / usable area, only for transactions that
      consist of exactly one unit record (otherwise the price covers other units too).
    - ``price_m2``: ``price_m2_lok`` if available, else ``price_m2_tran``;
      ``price_m2_source`` tells which one was used.

    Requires ``n_units_in_trans`` (see :func:`add_transaction_counts`).
    """
    out = df.copy()
    area = out["lok_pow_uzyt"].where(out["lok_pow_uzyt"] > 0)
    out["price_m2_lok"] = out["lok_cena_brutto"].where(out["lok_cena_brutto"] > 0) / area
    single = out["n_units_in_trans"] == 1
    out["price_m2_tran"] = (
        out["tran_cena_brutto"].where(out["tran_cena_brutto"] > 0) / area
    ).where(single)
    out["price_m2"] = out["price_m2_lok"].fillna(out["price_m2_tran"])
    src = pd.Series(pd.NA, index=out.index, dtype="string")
    src = src.mask(out["price_m2_tran"].notna(), "tran")
    src = src.mask(out["price_m2_lok"].notna(), "lok")
    out["price_m2_source"] = src
    return out
