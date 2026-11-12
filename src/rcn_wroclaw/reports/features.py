"""Stage-3 report: location features – dictionary, distributions, links with price/m²."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from rcn_wroclaw import config  # noqa: E402
from rcn_wroclaw.features.build import FEATURES_PATH  # noqa: E402
from rcn_wroclaw.features.euclidean import location_id  # noqa: E402
from rcn_wroclaw.rcn.derive import add_price_per_m2, add_transaction_counts  # noqa: E402
from rcn_wroclaw.reports.eda_rcn import (  # noqa: E402
    SEQ_BLUE,
    SURFACE,
    TEXT_SECONDARY,
    _md_table,
    _style,
)

POS = "#2a78d6"  # diverging poles (blue / red) for correlation sign
NEG = "#e34948"

DICTIONARY = [
    ("Lokalizacja", "osiedle", "osiedle (48, uchwała 2016); kategoria"),
    ("Lokalizacja", "x_2180, y_2180", "współrzędne punktu (EPSG:2180)"),
    ("Centrum", "dist_rynek_m, walk_rynek_m", "odległość do Rynku: prosta / piesza"),
    ("Centrum", "dir_rynek_sin/cos", "kierunek od Rynku (azymut jako sin/cos)"),
    ("Komunikacja", "dist_stop_m, walk_stop_m", "najbliższy obsługiwany słupek (prosta / pieszo)"),
    ("Komunikacja", "dist_tram_stop_m, walk_tram_stop_m", "najbliższy przystanek tramwajowy"),
    ("Komunikacja", "n_lines_{r}, n_lines_tram_{r}", "liczba różnych linii w promieniu r"),
    ("Komunikacja", "dep_day_{r}, dep_peak_{r}", "odjazdy w dzień powszedni / 7–9 w promieniu r"),
    ("POI punktowe", "dist_<kat>_m", "odległość do najbliższego obiektu kategorii"),
    ("POI punktowe", "cnt_<kat>_{300,500,1000}", "liczba obiektów kategorii w promieniu"),
    ("POI punktowe", "walk_<kat>_m", "odległość piesza (wybrane kategorie)"),
    ("Tereny", "dist_park_m / forest / water", "do parku ≥0,5 ha / lasu ≥1 ha / wody ≥1 ha"),
    ("Tereny", "walk_park_m / forest / water", "pieszo do węzła sieci ≤30 m od terenu"),
    ("Tereny", "share_green_{500,1000}", "udział parków+lasów w buforze"),
    ("Uciążliwości", "dist_industrial_m, share_industrial_500", "teren przemysłowy"),
    ("Uciążliwości", "dist_major_road_m, dist_railway_line_m, dist_cemetery_m", "drogi, kolej"),
    ("Sieć", "walk_snap_m", "odległość punktu od najbliższego węzła sieci pieszej"),
]


def working_sample(rcn: gpd.GeoDataFrame) -> pd.DataFrame:
    """Exploratory sample (NOT the final cleaning): free-market single-flat transactions,
    full ownership, deeds from 2023, price/m² 3–50k PLN."""
    df = add_price_per_m2(add_transaction_counts(rcn))
    m = (
        (df["lok_funkcja"] == "mieszkalna")
        & (df["tran_rodzaj_trans"] == "wolnyRynek")
        & (df["n_flats_in_trans"] == 1)
        & (df["nier_udzial"] == "1/1")
        & (df["dok_data"] >= "2023-01-01")
        & df["price_m2"].between(3000, 50000)
    )
    out = df[m].copy()
    out["location_id"] = location_id(out.geometry.x.to_numpy(), out.geometry.y.to_numpy())
    return out


def _fig_map(
    feats: gpd.GeoDataFrame, col: str, title: str, label: str, path: Path, clip_q: float = 0.98
) -> None:
    v = feats[col].astype(float)
    hi = v.quantile(clip_q)
    boundary = gpd.read_parquet(config.BOUNDARY_CITY)
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    boundary.boundary.plot(ax=ax, color=TEXT_SECONDARY, linewidth=1)
    sc = ax.scatter(
        feats.geometry.x, feats.geometry.y, c=v.clip(upper=hi), cmap=SEQ_BLUE, s=5, linewidths=0
    )
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title(title)
    cb = fig.colorbar(sc, ax=ax, shrink=0.65)
    cb.set_label(label, color=TEXT_SECONDARY)
    cb.outline.set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_corr(rho: pd.Series, path: Path) -> None:
    top = rho.reindex(rho.abs().sort_values(ascending=True).index)
    fig, ax = plt.subplots(figsize=(8, 0.28 * len(top) + 1.2))
    colors = [POS if v > 0 else NEG for v in top]
    ax.barh(top.index, top.to_numpy(), color=colors, height=0.7, edgecolor=SURFACE)
    ax.axvline(0, color=TEXT_SECONDARY, lw=0.8)
    ax.set_xlabel("Spearman ρ z ceną/m² (dodatni = wyższa cena)")
    ax.set_title("Najsilniej powiązane cechy lokalizacyjne")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def build_report(out_md: Path, fig_dir: Path) -> Path:
    _style()
    fig_dir.mkdir(parents=True, exist_ok=True)
    feats = gpd.read_parquet(FEATURES_PATH)
    num = feats.drop(columns=["location_id", "osiedle", "geometry"]).select_dtypes("number")
    sample = working_sample(gpd.read_parquet(config.RCN_LOKALE_INTERIM))
    tx = sample[["location_id", "price_m2"]].merge(
        feats.drop(columns="geometry"), on="location_id", how="left"
    )

    L: list[str] = []
    add = L.append
    add("# Etap 3 – cechy lokalizacyjne\n")
    add(
        f"Wygenerowano: {dt.datetime.now():%Y-%m-%d %H:%M}. Plik: "
        f"`{FEATURES_PATH.relative_to(config.PROJECT_ROOT)}`.\n"
    )
    add(
        f"- Unikalne lokalizacje (punkty RCN): **{len(feats):,}**; cechy liczbowe: "
        f"**{num.shape[1]}** + `osiedle`"
    )
    add(
        f"- Braki: {int(num.isna().sum().sum())} wartości; lokalizacje bez osiedla: "
        f"{feats['osiedle'].isna().sum()}"
    )
    add(
        f"- Próba robocza do korelacji: {len(tx):,} transakcji (wolny rynek, 1 lokal, udział 1/1, "
        "akt od 2023, 3–50 tys. zł/m²) – **to nie jest jeszcze docelowe czyszczenie**.\n"
    )

    add("## 1. Słownik cech\n")
    add(
        _md_table(pd.DataFrame(DICTIONARY, columns=["grupa", "cechy", "opis"]).set_index("grupa"))
        + "\n"
    )
    add(
        "Promienie r = 300 / 500 / 1000 m. Odległości piesze liczone po grafie OSM `walk` "
        "(OSMnx), ograniczone do 5 km.\n"
    )

    add("## 2. Rozkłady\n")
    stats = num.describe(percentiles=[0.05, 0.5, 0.95]).T[["5%", "50%", "95%", "max"]]
    stats["% zer"] = (100 * (num == 0).mean()).round(1)
    add(_md_table(stats, floatfmt=",.1f") + "\n")

    walk_cols = [c for c in num.columns if c.startswith("walk_") and c != "walk_snap_m"]
    if walk_cols:
        add("## 3. Odległość piesza vs. prosta (współczynnik objazdu)\n")
        rows = []
        for w in walk_cols:
            base = w.replace("walk_", "dist_")
            if base in num.columns:
                ok = (num[base] > 50) & (num[w] < 5000)
                ratio = num.loc[ok, w] / num.loc[ok, base]
                rows.append(
                    {
                        "cecha": w.removeprefix("walk_").removesuffix("_m"),
                        "mediana prosta [m]": num[base].median(),
                        "mediana piesza [m]": num[w].median(),
                        "objazd (mediana)": ratio.median(),
                        "objazd q95": ratio.quantile(0.95),
                        "% ≥ 5 km (limit dla POI)": 100 * (num[w] >= 5000).mean(),
                    }
                )
        add(_md_table(pd.DataFrame(rows).set_index("cecha"), floatfmt=",.2f") + "\n")
        add("Objazd = piesza / prosta, dla punktów z odległością prostą > 50 m.\n")

    add("## 4. Związek z ceną/m² (Spearman, próba robocza)\n")
    rho = tx[num.columns].corrwith(tx["price_m2"], method="spearman").dropna()
    top = rho.reindex(rho.abs().sort_values(ascending=False).index).head(30)
    f_corr = fig_dir / "feat_corr_price.png"
    _fig_corr(top, f_corr)
    add(f"![Korelacje]({fig_dir.name}/{f_corr.name})\n")
    add(_md_table(top.rename("rho").to_frame(), floatfmt=".3f") + "\n")
    os_med = tx.groupby("osiedle")["price_m2"].median()
    add(
        f"Dla porównania: rozrzut median osiedli {os_med.min():,.0f}–{os_med.max():,.0f} zł/m²; "
        f"η² (udział wariancji ceny/m² wyjaśniony przez osiedle) = "
        f"{_eta_squared(tx['price_m2'], tx['osiedle']):.3f}.\n"
    )

    add("## 5. Współliniowość\n")
    c = num.corr(method="spearman").abs()
    pairs = c.where(np.triu(np.ones(c.shape, dtype=bool), k=1)).stack().sort_values(ascending=False)
    strong = pairs[pairs > 0.9]
    add(f"Pary cech z |ρ| > 0,9: **{len(strong)}** (na {len(pairs):,}). Najsilniejsze:\n")
    add(_md_table(strong.head(20).rename("|rho|").to_frame(), floatfmt=".3f") + "\n")
    groups = pd.Series([re.sub(r"_(300|500|1000)$", "", n) for n in num.columns]).value_counts()
    add(
        f"Rodziny cech z wieloma promieniami: {int((groups > 1).sum())} – w modelu liniowym "
        "warto wybrać jeden promień na rodzinę lub użyć regularyzacji (Ridge).\n"
    )

    add("## 6. Mapy\n")
    for col, title, label, name in [
        (
            "dep_day_500",
            "Odjazdy w promieniu 500 m (dzień powszedni)",
            "odjazdy",
            "feat_map_dep500",
        ),
        (
            "walk_tram_stop_m" if "walk_tram_stop_m" in feats else "dist_tram_stop_m",
            "Dojście do przystanku tramwajowego",
            "m",
            "feat_map_tram",
        ),
        ("share_green_500", "Udział zieleni w promieniu 500 m", "udział", "feat_map_green"),
    ]:
        f = fig_dir / f"{name}.png"
        _fig_map(feats, col, title, label, f)
        add(f"![{title}]({fig_dir.name}/{f.name})\n")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L), encoding="utf-8")
    return out_md


def _eta_squared(y: pd.Series, groups: pd.Series) -> float:
    ok = groups.notna()
    y, groups = y[ok].astype(float), groups[ok]
    grand = y.mean()
    ss_between = y.groupby(groups).apply(lambda s: len(s) * (s.mean() - grand) ** 2).sum()
    ss_total = ((y - grand) ** 2).sum()
    return float(ss_between / ss_total)
