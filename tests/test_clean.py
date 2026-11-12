"""Tests for RCN transaction cleaning rules."""

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from rcn_wroclaw.clean.rcn import CleaningConfig, clean_transactions, parking_note, trim_by_year


def make(**over):
    base = {
        "gid": 1,
        "tran_lokalny_id_iip": "t1",
        "lok_id_lokalu": "L1",
        "lok_funkcja": "mieszkalna",
        "tran_rodzaj_trans": "wolnyRynek",
        "tran_rodzaj_rynku": "rynekWtorny",
        "nier_udzial": "1/1",
        "dok_data": pd.Timestamp("2024-05-10"),
        "tran_wersja_id": pd.Timestamp("2024-06-01", tz="UTC"),
        "lok_pow_uzyt": 50.0,
        "lok_cena_brutto": 600_000.0,
        "tran_cena_brutto": 600_000.0,
        "lok_info": None,
        "nier_opis": None,
    }
    base.update(over)
    return base


def frame(rows):
    df = pd.DataFrame(rows)
    for c in ("lok_cena_brutto", "tran_cena_brutto", "lok_pow_uzyt"):
        df[c] = df[c].astype("Float64")
    return gpd.GeoDataFrame(df, geometry=[Point(0, 0)] * len(df), crs="EPSG:2180")


def no_trim():
    return CleaningConfig(yearly_trim_q=0.0)


def test_rules_drop_expected_rows():
    rows = [
        make(gid=1, tran_lokalny_id_iip="a"),  # kept
        make(gid=2, tran_lokalny_id_iip="b", tran_rodzaj_trans="sprzedazZBonifikata"),
        make(gid=3, tran_lokalny_id_iip="c", nier_udzial="1/2"),
        make(gid=4, tran_lokalny_id_iip="d", dok_data=pd.Timestamp("2022-12-31")),
        make(gid=5, tran_lokalny_id_iip="e", lok_pow_uzyt=10.0),
        make(gid=6, tran_lokalny_id_iip="f", lok_cena_brutto=50_000.0),  # 1000 zł/m²
        make(gid=7, tran_lokalny_id_iip="g", tran_rodzaj_rynku=None),
        make(gid=8, tran_lokalny_id_iip="h", lok_funkcja="garaz"),
        # two flats in one transaction -> both dropped
        make(gid=9, tran_lokalny_id_iip="i", lok_id_lokalu="L9"),
        make(gid=10, tran_lokalny_id_iip="i", lok_id_lokalu="L10"),
    ]
    out, log = clean_transactions(frame(rows), no_trim())
    assert out["gid"].tolist() == [1]
    assert log.to_frame()["usunięte"].sum() == len(rows) - 1


def test_flat_with_garage_keeps_unit_price_but_not_transaction_fallback():
    rows = [
        make(gid=1, tran_lokalny_id_iip="a", tran_cena_brutto=700_000.0),
        make(gid=2, tran_lokalny_id_iip="a", lok_funkcja="garaz", lok_id_lokalu="G"),
        # no unit price + garage in transaction -> price undefined -> dropped
        make(gid=3, tran_lokalny_id_iip="b", lok_cena_brutto=None, lok_id_lokalu="L3"),
        make(gid=4, tran_lokalny_id_iip="b", lok_funkcja="garaz", lok_id_lokalu="G2"),
    ]
    out, _ = clean_transactions(frame(rows), no_trim())
    assert out["gid"].tolist() == [1]
    assert out.loc[0, "price_m2"] == 12_000  # lok_cena / area, not tran_cena


def test_duplicates_keep_latest_version():
    rows = [
        make(gid=1, tran_lokalny_id_iip="a", tran_wersja_id=pd.Timestamp("2024-06-01", tz="UTC")),
        make(gid=2, tran_lokalny_id_iip="b", tran_wersja_id=pd.Timestamp("2024-07-01", tz="UTC")),
    ]
    out, _ = clean_transactions(frame(rows), no_trim())
    assert out["gid"].tolist() == [2]


def test_derived_columns():
    out, _ = clean_transactions(frame([make(lok_info="prawo do miejsca postojowego")]), no_trim())
    row = out.iloc[0]
    assert row["has_parking_note"]
    assert row["price_total"] == 600_000
    assert row["month"] == pd.Timestamp("2024-05-01")


def test_parking_note_variants():
    df = pd.DataFrame(
        {
            "lok_info": ["Garaż w cenie", None, "balkon"],
            "nier_opis": [None, "miejsce postojowe", None],
        }
    )
    assert parking_note(df).tolist() == [True, True, False]


def test_trim_by_year_is_per_year():
    df = pd.DataFrame({"v": list(range(100)) + list(range(1000, 1100))})
    year = pd.Series([2023] * 100 + [2024] * 100)
    keep = trim_by_year(df, "v", 0.05, year)
    assert keep.sum() == 2 * 90  # interpolated quantiles 4.95 / 94.05 per year
    assert keep.iloc[50] and keep.iloc[150]
