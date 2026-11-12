"""Cleaning of RCN residential transactions (rules agreed with the project owner, stage 4).

Every rule is a named step; :func:`clean_transactions` returns the cleaned frame and a log
of how many rows each step removed (reported in 04_models.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from rcn_wroclaw.rcn.derive import add_price_per_m2, add_transaction_counts


@dataclass(frozen=True)
class CleaningConfig:
    min_date: str = "2023-01-01"
    min_area_m2: float = 15.0
    max_area_m2: float = 300.0
    min_price_m2: float = 3_000.0
    max_price_m2: float = 50_000.0
    yearly_trim_q: float = 0.005  # trimmed on each side, per deed year
    transaction_types: tuple[str, ...] = ("wolnyRynek",)


PARKING_PATTERN = r"postoj|garaż|garaz|parking|hal[ai] garażow"


@dataclass
class CleaningLog:
    steps: list[tuple[str, int, int]] = field(default_factory=list)

    def record(self, name: str, before: int, after: int) -> None:
        self.steps.append((name, before, after))

    def to_frame(self) -> pd.DataFrame:
        df = pd.DataFrame(self.steps, columns=["krok", "przed", "po"])
        df["usunięte"] = df["przed"] - df["po"]
        return df


def parking_note(df: pd.DataFrame) -> pd.Series:
    """True when unit/property remarks mention a parking space or garage."""
    txt = (df["lok_info"].fillna("") + " " + df["nier_opis"].fillna("")).str.lower()
    return txt.str.contains(PARKING_PATTERN, regex=True)


def trim_by_year(df: pd.DataFrame, col: str, q: float, year: pd.Series) -> pd.Series:
    """Boolean mask keeping values within [q, 1-q] quantiles computed separately per year."""
    lo = df.groupby(year)[col].transform(lambda s: s.quantile(q))
    hi = df.groupby(year)[col].transform(lambda s: s.quantile(1 - q))
    return df[col].between(lo, hi)


def clean_transactions(
    rcn: pd.DataFrame, cfg: CleaningConfig | None = None
) -> tuple[pd.DataFrame, CleaningLog]:
    """Apply the cleaning rules to raw RCN unit records (``ms:lokale``, all functions)."""
    cfg = cfg or CleaningConfig()
    log = CleaningLog()
    # Transaction-level counts must be computed on all unit records (incl. garages).
    df = add_price_per_m2(add_transaction_counts(rcn))

    def step(name: str, mask: pd.Series) -> None:
        nonlocal df
        before = len(df)
        df = df[mask.fillna(False).to_numpy()]
        log.record(name, before, len(df))

    log.record("rekordy ms:lokale (TERYT 0264)", len(df), len(df))
    step("lokal mieszkalny", df["lok_funkcja"] == "mieszkalna")
    step("rodzaj transakcji: wolny rynek", df["tran_rodzaj_trans"].isin(cfg.transaction_types))
    step("udział 1/1", df["nier_udzial"] == "1/1")
    step("1 lokal mieszkalny w transakcji", df["n_flats_in_trans"] == 1)
    step("znany rynek (pierwotny/wtórny)", df["tran_rodzaj_rynku"].notna())
    step(f"akt od {cfg.min_date}", df["dok_data"] >= pd.Timestamp(cfg.min_date))
    step(
        f"powierzchnia {cfg.min_area_m2:g}–{cfg.max_area_m2:g} m²",
        df["lok_pow_uzyt"].between(cfg.min_area_m2, cfg.max_area_m2),
    )
    step("znana cena/m² (lok_cena lub tran_cena dla 1 lokalu)", df["price_m2"].notna())
    step(
        f"cena/m² {cfg.min_price_m2:,.0f}–{cfg.max_price_m2:,.0f} zł",
        df["price_m2"].between(cfg.min_price_m2, cfg.max_price_m2),
    )
    step(
        f"obcięcie {100 * cfg.yearly_trim_q:g}% ogonów ceny/m² w każdym roku",
        trim_by_year(df, "price_m2", cfg.yearly_trim_q, df["dok_data"].dt.year),
    )
    before = len(df)
    df = df.sort_values("tran_wersja_id").drop_duplicates(
        ["lok_id_lokalu", "dok_data", "price_m2"], keep="last"
    )
    log.record("duplikaty (lokal, data, cena)", before, len(df))

    df = df.assign(
        has_parking_note=parking_note(df),
        price_total=df["price_m2"] * df["lok_pow_uzyt"],
        month=df["dok_data"].dt.to_period("M").dt.to_timestamp(),
    )
    return df.sort_values("dok_data").reset_index(drop=True), log
