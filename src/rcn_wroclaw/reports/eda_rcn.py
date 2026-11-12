"""Exploratory report on RCN unit records for Wrocław (Markdown + PNG figures).

The report text is in Polish (it is read by the project owner); code stays in English.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

from rcn_wroclaw.rcn.derive import add_price_per_m2, add_transaction_counts  # noqa: E402

# Reference palette (dataviz skill, light mode).
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
SERIES = {"rynekPierwotny": "#2a78d6", "rynekWtorny": "#eb6834", "brak": "#1baf7a"}
MARKET_LABEL = {"rynekPierwotny": "pierwotny", "rynekWtorny": "wtórny", "brak": "brak danych"}
SEQ_BLUE = LinearSegmentedColormap.from_list(
    "seq_blue", ["#cde2fb", "#86b6ef", "#3987e5", "#256abf", "#184f95", "#0d366b"]
)

SYMBOLIC_PRICE_M2 = 1000.0  # PLN/m² – below this a price is treated as symbolic in the report
HIGH_PRICE_M2 = 50000.0

ID_FIELDS = {"gid", "tran_lokalny_id_iip", "tran_oznaczenie_trans", "lok_id_lokalu"}


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": GRID,
            "axes.labelcolor": TEXT_SECONDARY,
            "axes.titlecolor": TEXT_PRIMARY,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.titlelocation": "left",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": TEXT_SECONDARY,
            "ytick.color": TEXT_SECONDARY,
            "legend.frameon": False,
            "legend.labelcolor": TEXT_PRIMARY,
            "font.size": 10,
            "figure.dpi": 110,
        }
    )


def _md_table(df: pd.DataFrame | pd.Series, **kwargs) -> str:
    if isinstance(df, pd.Series):
        df = df.to_frame()
    return df.to_markdown(**kwargs)


def _counts(s: pd.Series) -> pd.DataFrame:
    vc = s.fillna("<brak>").value_counts()
    return pd.DataFrame({"liczba": vc, "%": (100 * vc / vc.sum()).round(1)})


def field_profile(df: pd.DataFrame) -> pd.DataFrame:
    """Per-column dtype, missing share, number of unique values and example values."""
    rows = []
    for col in df.columns:
        if col == "geometry":
            continue
        s = df[col]
        nonnull = s.dropna()
        if col in ID_FIELDS:
            example = "(identyfikator)"
        elif s.dtype.kind in "fiu" or str(s.dtype) in {"Float64", "Int64"}:
            example = (
                f"min {nonnull.min():,.2f} / med {nonnull.median():,.2f} / max {nonnull.max():,.2f}"
                if len(nonnull)
                else ""
            )
        elif str(s.dtype).startswith("datetime"):
            example = f"{nonnull.min()} … {nonnull.max()}" if len(nonnull) else ""
        else:
            top = nonnull.astype(str).value_counts().head(3).index.tolist()
            example = "; ".join(t[:40] for t in top)
        rows.append(
            {
                "pole": col,
                "typ": str(s.dtype),
                "% braków": round(100 * s.isna().mean(), 1),
                "unikalne": int(s.nunique(dropna=True)),
                "przykłady / zakres": example.replace("|", "/"),
            }
        )
    return pd.DataFrame(rows).set_index("pole")


def _fig_monthly(flats: pd.DataFrame, path: Path) -> None:
    m = flats.assign(
        month=flats["dok_data"].dt.to_period("M").dt.to_timestamp(),
        market=flats["tran_rodzaj_rynku"].fillna("brak"),
    )
    m = m[m["month"] >= pd.Timestamp("2022-01-01")]
    pivot = m.pivot_table(index="month", columns="market", values="gid", aggfunc="count").fillna(0)
    fig, ax = plt.subplots(figsize=(9, 3.6))
    for market in ["rynekPierwotny", "rynekWtorny", "brak"]:
        if market in pivot:
            ax.plot(
                pivot.index,
                pivot[market],
                color=SERIES[market],
                lw=2,
                label=MARKET_LABEL[market],
            )
    ax.set_title("Lokale mieszkalne – liczba rekordów miesięcznie (od 2022)")
    ax.set_ylabel("rekordy / miesiąc")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_hist(flats: pd.DataFrame, path: Path) -> None:
    p = flats["price_m2"].dropna().astype(float)
    lo, hi = p.quantile([0.005, 0.995])
    fig, ax = plt.subplots(figsize=(9, 3.6))
    bins = np.linspace(lo, hi, 80)
    for market in ["rynekWtorny", "rynekPierwotny"]:
        sub = flats.loc[flats["tran_rodzaj_rynku"] == market, "price_m2"].dropna().astype(float)
        ax.hist(
            sub.clip(lo, hi),
            bins=bins,
            histtype="step",
            color=SERIES[market],
            linewidth=2,
            label=f"{MARKET_LABEL[market]} (n={len(sub):,})",
        )
    ax.set_title("Rozkład ceny za m² (obcięty do kwantyli 0,5–99,5%)")
    ax.set_xlabel("PLN/m² brutto")
    ax.set_ylabel("liczba lokali")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_box_year(flats: pd.DataFrame, path: Path) -> None:
    f = flats.dropna(subset=["price_m2", "dok_data"]).copy()
    f["year"] = f["dok_data"].dt.year
    years = [y for y in sorted(f["year"].unique()) if (f["year"] == y).sum() >= 30 and y >= 2018]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    width = 0.36
    for k, market in enumerate(["rynekPierwotny", "rynekWtorny"]):
        data = [
            f.loc[(f["year"] == y) & (f["tran_rodzaj_rynku"] == market), "price_m2"].astype(float)
            for y in years
        ]
        pos = np.arange(len(years)) + (k - 0.5) * width
        keep = [i for i, d in enumerate(data) if len(d) > 0]
        bp = ax.boxplot(
            [data[i] for i in keep],
            positions=pos[keep],
            widths=width * 0.85,
            showfliers=False,
            patch_artist=True,
            medianprops={"color": TEXT_PRIMARY, "lw": 1.5},
            whiskerprops={"color": TEXT_SECONDARY},
            capprops={"color": TEXT_SECONDARY},
        )
        for box in bp["boxes"]:
            box.set(facecolor=SERIES[market], edgecolor=SURFACE, alpha=0.85)
        ax.plot([], [], color=SERIES[market], lw=6, label=MARKET_LABEL[market])
    ax.set_xticks(np.arange(len(years)), [str(y) for y in years])
    ax.set_title("Cena za m² wg roku aktu i rynku (bez obserwacji odstających)")
    ax.set_ylabel("PLN/m² brutto")
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_map(flats: gpd.GeoDataFrame, path: Path) -> None:
    g = flats.dropna(subset=["price_m2"])
    g = g[g.geometry.notna()]
    lo, hi = g["price_m2"].astype(float).quantile([0.02, 0.98])
    fig, ax = plt.subplots(figsize=(7.5, 7))
    sc = ax.scatter(
        g.geometry.x,
        g.geometry.y,
        c=g["price_m2"].astype(float).clip(lo, hi),
        cmap=SEQ_BLUE,
        s=4,
        linewidths=0,
    )
    ax.set_aspect("equal")
    ax.set_title("Lokalizacja transakcji (punkty RCN), kolor = cena/m²")
    ax.set_xlabel("easting [m, EPSG:2180]")
    ax.set_ylabel("northing [m, EPSG:2180]")
    cb = fig.colorbar(sc, ax=ax, shrink=0.7)
    cb.set_label("PLN/m² (obcięte do kwantyli 2–98%)", color=TEXT_SECONDARY)
    cb.outline.set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def build_report(
    gdf: gpd.GeoDataFrame,
    out_md: Path,
    fig_dir: Path,
    source_note: str = "",
) -> Path:
    """Write the exploratory Markdown report and figures; return the report path."""
    _style()
    fig_dir.mkdir(parents=True, exist_ok=True)
    df = add_price_per_m2(add_transaction_counts(gdf))
    flats = df[df["lok_funkcja"] == "mieszkalna"].copy()

    rel = Path(fig_dir.name)
    figs = {
        "monthly": fig_dir / "rcn_monthly_counts.png",
        "hist": fig_dir / "rcn_price_m2_hist.png",
        "box": fig_dir / "rcn_price_m2_by_year.png",
        "map": fig_dir / "rcn_map_price_m2.png",
    }
    _fig_monthly(flats, figs["monthly"])
    _fig_hist(flats, figs["hist"])
    _fig_box_year(flats, figs["box"])
    _fig_map(flats, figs["map"])

    L: list[str] = []
    add = L.append
    add("# RCN Wrocław – raport eksploracyjny (lokale)\n")
    add(f"Wygenerowano: {dt.datetime.now():%Y-%m-%d %H:%M}. {source_note}\n")
    add(
        "Źródło: WFS GUGiK `ms:lokale`, filtr `TERYT == 0264`, deduplikacja po `gid`. "
        "Pole `dok_tworca` (dane notariuszy) pominięte przy parsowaniu. "
        "Na tym etapie **nic nie jest odrzucane** – to opis surowych danych.\n"
    )

    add("## 1. Liczba rekordów\n")
    add(f"- Wszystkie lokale (każda funkcja): **{len(df):,}**")
    add(f"- Lokale mieszkalne: **{len(flats):,}**")
    add(
        f"- Transakcje (unikalne `tran_lokalny_id_iip`) z ≥1 lokalem mieszkalnym: "
        f"**{flats['tran_lokalny_id_iip'].nunique():,}**\n"
    )
    add("### Funkcja lokalu (wszystkie rekordy)\n")
    add(_md_table(_counts(df["lok_funkcja"])) + "\n")
    for col, title in [
        ("tran_rodzaj_trans", "Rodzaj transakcji"),
        ("tran_rodzaj_rynku", "Rodzaj rynku"),
        ("tran_sprzedajacy", "Sprzedający"),
        ("tran_kupujacy", "Kupujący"),
        ("nier_rodzaj", "Rodzaj nieruchomości"),
        ("nier_prawo", "Prawo"),
    ]:
        add(f"### {title} (lokale mieszkalne)\n")
        add(_md_table(_counts(flats[col])) + "\n")

    add("## 2. Zakres dat (`dok_data`, lokale mieszkalne)\n")
    d = flats["dok_data"]
    add(
        f"- Najstarsza: **{d.min():%Y-%m-%d}**, najnowsza: **{d.max():%Y-%m-%d}**, "
        f"braki: {d.isna().sum():,}"
    )
    by_year = d.dt.year.dropna().astype(int).value_counts().sort_index()
    add("\n" + _md_table(by_year.rename("liczba").to_frame().rename_axis("rok").T) + "\n")
    add(f"![Liczba rekordów miesięcznie]({rel / figs['monthly'].name})\n")
    wv = flats["tran_wersja_id"]
    add(f"- `tran_wersja_id` (wersja wpisu w RCN): {wv.min():%Y-%m-%d} … {wv.max():%Y-%m-%d}\n")

    add("## 3. Profil pól (lokale mieszkalne)\n")
    add(
        _md_table(
            field_profile(
                flats.drop(
                    columns=[
                        c for c in flats.columns if c.startswith(("price_m2", "n_units", "n_flats"))
                    ]
                )
            )
        )
        + "\n"
    )

    add("## 4. Transakcje wielolokalowe i udziały\n")
    nu = flats["n_units_in_trans"].clip(upper=10)
    add(
        "Liczba rekordów `ms:lokale` w transakcji, do której należy lokal mieszkalny "
        "(10 = 10 i więcej):\n"
    )
    add(_md_table(_counts(nu.astype("string"))) + "\n")
    nf = flats["n_flats_in_trans"].clip(upper=10)
    add("Liczba **lokali mieszkalnych** w tej samej transakcji:\n")
    add(_md_table(_counts(nf.astype("string"))) + "\n")
    add("Udział (`nier_udzial`) – 10 najczęstszych wartości:\n")
    add(_md_table(_counts(flats["nier_udzial"]).head(10)) + "\n")
    same_id = (
        flats["tran_oznaczenie_trans"].str.replace("-", "").str.upper()
        == flats["tran_lokalny_id_iip"].str.replace("-", "").str.upper()
    ).mean()
    add(
        f"- `tran_oznaczenie_trans` = `tran_lokalny_id_iip` (bez myślników): "
        f"{100 * same_id:.1f}% rekordów.\n"
    )

    add("## 5. Spójność cen\n")
    single = flats[flats["n_units_in_trans"] == 1]
    both = single.dropna(subset=["lok_cena_brutto", "tran_cena_brutto"])
    eq = (both["lok_cena_brutto"] - both["tran_cena_brutto"]).abs() < 1
    add(
        f"- Lokale mieszkalne z wypełnionym `lok_cena_brutto`: "
        f"{100 * flats['lok_cena_brutto'].notna().mean():.1f}%"
    )
    add(
        f"- Transakcje jednolokalowe: {len(single):,}; z obiema cenami: {len(both):,}; "
        f"w tym `lok_cena == tran_cena`: {100 * eq.mean() if len(both) else float('nan'):.1f}%"
    )
    vat = flats["tran_vat"].notna().mean() * 100
    add(
        f"- `tran_vat` wypełniony: {vat:.1f}%; `lok_vat`: "
        f"{100 * flats['lok_vat'].notna().mean():.1f}%"
    )
    add("- `lok_info` – najczęstsze wartości:\n")
    add(_md_table(_counts(flats["lok_info"]).head(10)) + "\n")

    add("## 6. Cena za m²\n")
    add(
        "`price_m2 = lok_cena_brutto / lok_pow_uzyt`; gdy brak ceny lokalu – "
        "`tran_cena_brutto / lok_pow_uzyt`, ale tylko dla transakcji jednolokalowych.\n"
    )
    add(_md_table(_counts(flats["price_m2_source"])) + "\n")
    q = [0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1]
    qt = pd.DataFrame(
        {
            "wszystkie": flats["price_m2"].astype(float).quantile(q),
            "pierwotny": flats.loc[flats["tran_rodzaj_rynku"] == "rynekPierwotny", "price_m2"]
            .astype(float)
            .quantile(q),
            "wtórny": flats.loc[flats["tran_rodzaj_rynku"] == "rynekWtorny", "price_m2"]
            .astype(float)
            .quantile(q),
        }
    ).round(0)
    qt.index = [f"q{int(x * 100):02d}" for x in q]
    add("Kwantyle (PLN/m² brutto):\n")
    add(_md_table(qt, floatfmt=",.0f") + "\n")
    add("Rodzaj transakcji × rynek (lokale mieszkalne):\n")
    ct = pd.crosstab(
        flats["tran_rodzaj_trans"].fillna("<brak>"), flats["tran_rodzaj_rynku"].fillna("<brak>")
    )
    add(_md_table(ct) + "\n")
    by_tr = (
        flats.groupby(flats["tran_rodzaj_trans"].fillna("<brak>"))["price_m2"]
        .agg(
            n="count",
            q05=lambda s: s.astype(float).quantile(0.05),
            mediana=lambda s: s.astype(float).median(),
            q95=lambda s: s.astype(float).quantile(0.95),
        )
        .sort_values("n", ascending=False)
    )
    add("Cena/m² wg rodzaju transakcji:\n")
    add(_md_table(by_tr, floatfmt=",.0f") + "\n")
    by_seller = (
        flats.groupby(flats["tran_sprzedajacy"].fillna("<brak>"))["price_m2"]
        .agg(n="count", mediana=lambda s: s.astype(float).median())
        .sort_values("n", ascending=False)
    )
    add("Cena/m² wg sprzedającego:\n")
    add(_md_table(by_seller, floatfmt=",.0f") + "\n")
    p = flats["price_m2"].astype(float)
    add(
        f"- `price_m2` < {SYMBOLIC_PRICE_M2:,.0f} PLN/m² (cena symboliczna?): "
        f"**{(p < SYMBOLIC_PRICE_M2).sum():,}**"
    )
    add(f"- `price_m2` > {HIGH_PRICE_M2:,.0f} PLN/m²: **{(p > HIGH_PRICE_M2).sum():,}**")
    add(f"- Brak `price_m2`: **{p.isna().sum():,}**\n")
    yr = flats.dropna(subset=["price_m2"]).assign(rok=lambda x: x["dok_data"].dt.year)
    med = yr.pivot_table(
        index="rok", columns="tran_rodzaj_rynku", values="price_m2", aggfunc=["median", "count"]
    )
    med.columns = [f"{a}_{MARKET_LABEL.get(b, b)}" for a, b in med.columns]
    add("Mediana ceny/m² i liczba wg roku:\n")
    med = med[med.index >= 2015].astype(float)
    med.index = med.index.astype(int).astype(str)
    add(_md_table(med, floatfmt=",.0f") + "\n")
    add(f"![Histogram ceny/m²]({rel / figs['hist'].name})\n")
    add(f"![Cena/m² wg roku]({rel / figs['box'].name})\n")

    add("## 7. Cechy lokalu\n")
    for col in ["lok_pow_uzyt", "lok_liczba_izb", "lok_nr_kond", "lok_pow_przyn"]:
        s = flats[col].astype(float)
        add(
            f"- `{col}`: braki {100 * s.isna().mean():.1f}%, "
            f"q01={s.quantile(0.01):.1f}, mediana={s.median():.1f}, q99={s.quantile(0.99):.1f}, "
            f"max={s.max():.1f}"
        )
    add("")

    add("## 8. Geolokalizacja\n")
    has_geom = flats.geometry.notna() & ~flats.geometry.is_empty
    pts = flats.loc[has_geom].geometry
    n_unique_pts = pts.apply(lambda g: (round(g.x, 1), round(g.y, 1))).nunique()
    add(f"- Rekordy z punktem: {100 * has_geom.mean():.2f}% ({has_geom.sum():,})")
    add(
        f"- Unikalne punkty: {n_unique_pts:,} → średnio "
        f"{has_geom.sum() / max(n_unique_pts, 1):.1f} lokalu na punkt "
        "(punkt = najpewniej budynek)"
    )
    unique_ids = flats["lok_id_lokalu"].str.extract(r"(.*_BUD)")[0].nunique()
    add(f"- Unikalne identyfikatory budynków (prefiks `lok_id_lokalu` do `_BUD`): {unique_ids:,}")
    b = pts.total_bounds if len(pts) else [np.nan] * 4
    add(f"- Zasięg (EPSG:2180, E/N): E {b[0]:,.0f}–{b[2]:,.0f}, N {b[1]:,.0f}–{b[3]:,.0f}\n")
    add(f"![Mapa]({rel / figs['map'].name})\n")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L), encoding="utf-8")
    return out_md
