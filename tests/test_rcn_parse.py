"""Tests for RCN GML parsing and derived price columns."""

from pathlib import Path

import pandas as pd
import pytest

from rcn_wroclaw.rcn.derive import add_price_per_m2, add_transaction_counts
from rcn_wroclaw.rcn.parse import iter_features, parse_files
from rcn_wroclaw.rcn.wfs import Tile, make_tiles, number_returned

FIXTURE = Path(__file__).parent / "fixtures" / "lokale_sample.gml"


def test_iter_features_reads_all_and_drops_notary():
    recs = list(iter_features(FIXTURE))
    assert len(recs) == 4
    assert all("DOK_TWORCA" not in r for r in recs)
    assert recs[0]["DOK_DATA"] == "2024-02-15"
    assert recs[0]["TRAN_VAT"] is None  # empty element -> None
    assert recs[3]["geometry"] is None


def test_parse_files_types_and_teryt_filter():
    gdf = parse_files([FIXTURE])
    assert len(gdf) == 3
    assert set(gdf["teryt"]) == {"0264"}
    assert gdf.crs.to_epsg() == 2180
    assert str(gdf["lok_pow_uzyt"].dtype) == "Float64"
    assert str(gdf["lok_liczba_izb"].dtype) == "Int64"
    assert pd.api.types.is_datetime64_any_dtype(gdf["dok_data"])
    assert "dok_tworca" not in gdf.columns


def test_parse_files_deduplicates_overlapping_tiles():
    gdf = parse_files([FIXTURE, FIXTURE])
    assert gdf["gid"].is_unique
    assert len(gdf) == 3


def test_parse_files_without_teryt_filter():
    assert len(parse_files([FIXTURE], teryt=None)) == 4


def test_price_per_m2_sources():
    gdf = parse_files([FIXTURE])
    df = add_price_per_m2(add_transaction_counts(gdf)).set_index("gid")
    # unit price available
    assert df.loc[1, "price_m2"] == pytest.approx(10_000)
    assert df.loc[1, "price_m2_source"] == "lok"
    # no unit price, transaction has 2 unit records (flat + garage) -> no fallback
    assert df.loc[2, "n_units_in_trans"] == 2
    assert df.loc[2, "n_flats_in_trans"] == 1
    assert pd.isna(df.loc[2, "price_m2"])


def test_price_per_m2_fallback_single_unit():
    df = pd.DataFrame(
        {
            "tran_lokalny_id_iip": ["a", "b"],
            "lok_funkcja": ["mieszkalna", "mieszkalna"],
            "lok_pow_uzyt": [50.0, 0.0],
            "lok_cena_brutto": [pd.NA, pd.NA],
            "tran_cena_brutto": [400_000.0, 100_000.0],
        }
    ).astype({"lok_cena_brutto": "Float64"})
    out = add_price_per_m2(add_transaction_counts(df))
    assert out.loc[0, "price_m2"] == pytest.approx(8_000)
    assert out.loc[0, "price_m2_source"] == "tran"
    assert pd.isna(out.loc[1, "price_m2"])  # zero area -> undefined


def test_number_returned():
    assert number_returned(FIXTURE.read_bytes()) == 4


def test_tiles_cover_bbox_and_bbox_param_is_northing_first():
    tiles = make_tiles((0, 0, 4500, 2000), 2000)
    assert len(tiles) == 3
    assert tiles[-1].e_max == 4500
    t = Tile(0, 0, e_min=345000, n_min=354000, e_max=347000, n_max=356000)
    assert t.bbox_param().startswith("354000,345000,356000,347000,")
