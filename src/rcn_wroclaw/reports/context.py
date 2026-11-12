"""Stage-2 report: boundaries, GTFS stops, OSM POI and RCN spatial coverage (Polish text)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from rcn_wroclaw import config  # noqa: E402
from rcn_wroclaw.osm.poi import CATEGORIES  # noqa: E402
from rcn_wroclaw.rcn.derive import add_price_per_m2, add_transaction_counts  # noqa: E402
from rcn_wroclaw.reports.eda_rcn import (  # noqa: E402
    SEQ_BLUE,
    SURFACE,
    TEXT_SECONDARY,
    _counts,
    _md_table,
    _style,
)

TRAM = "#2a78d6"
BUS = "#eb6834"
OUTLINE = "#a3a29c"


def duplicate_points(poi: gpd.GeoDataFrame, max_dist_m: float = 100.0) -> pd.Series:
    """Per category: number of point features having another feature with the same name
    within ``max_dist_m`` (typical OSM node + area double mapping)."""
    pts = poi[(poi["kind"] == "point") & poi["name"].notna()].reset_index(drop=True)
    left = pts[["category", "name", "geometry"]].copy()
    left["geometry"] = left.buffer(max_dist_m)
    j = gpd.sjoin(left, pts[["category", "name", "geometry"]], predicate="intersects")
    j = j[(j.index != j["index_right"]) & (j["category_left"] == j["category_right"])]
    j = j[j["name_left"].str.lower() == j["name_right"].str.lower()]
    return j.groupby("category_left").apply(lambda g: g.index.nunique(), include_groups=False)


def _fig_stops(boundary, osiedla, stops, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 7))
    osiedla.boundary.plot(ax=ax, color=OUTLINE, linewidth=0.4)
    boundary.boundary.plot(ax=ax, color=TEXT_SECONDARY, linewidth=1)
    s = stops[stops["dep_day"] > 0]
    tram = s["n_lines_tram"] > 0
    for mask, color, label in [(~tram, BUS, "tylko autobus"), (tram, TRAM, "z tramwajem")]:
        sub = s[mask]
        ax.scatter(
            sub.geometry.x,
            sub.geometry.y,
            s=sub["dep_day"] / 25,
            color=color,
            alpha=0.75,
            edgecolors=SURFACE,
            linewidths=0.4,
            label=f"{label} (n={len(sub):,})",
        )
    e0, n0, e1, n1 = boundary.total_bounds
    ax.set_xlim(e0 - 1000, e1 + 1000)
    ax.set_ylim(n0 - 1000, n1 + 1000)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title("Przystanki GTFS – wielkość = odjazdy w dzień powszedni")
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _fig_osiedla_price(osiedla_stats: gpd.GeoDataFrame, boundary, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 7))
    osiedla_stats.plot(
        ax=ax,
        column="median_price_m2",
        cmap=SEQ_BLUE,
        edgecolor=SURFACE,
        linewidth=0.8,
        legend=True,
        legend_kwds={"shrink": 0.6, "label": "mediana PLN/m²"},
        missing_kwds={"color": "#e4e3df"},
    )
    boundary.boundary.plot(ax=ax, color=TEXT_SECONDARY, linewidth=1)
    ax.set_aspect("equal")
    ax.set_axis_off()
    ax.set_title("Mediana ceny/m² wg osiedla (wolny rynek, 2023+)")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def build_report(out_md: Path, fig_dir: Path) -> Path:
    """Write ``02_context_data.md`` and figures."""
    _style()
    fig_dir.mkdir(parents=True, exist_ok=True)
    boundary = gpd.read_parquet(config.BOUNDARY_CITY)
    city = boundary.geometry.iloc[0]
    osiedla = gpd.read_parquet(config.OSIEDLA)
    stops = gpd.read_parquet(config.GTFS_STOPS)
    poi = gpd.read_parquet(config.POI_ALL)
    rcn = add_price_per_m2(add_transaction_counts(gpd.read_parquet(config.RCN_LOKALE_INTERIM)))
    flats = rcn[rcn["lok_funkcja"] == "mieszkalna"].copy()

    L: list[str] = []
    add = L.append
    add("# Etap 2 – dane kontekstowe: granice, komunikacja, POI\n")
    add(f"Wygenerowano: {dt.datetime.now():%Y-%m-%d %H:%M}.\n")

    add("## 1. Granice\n")
    add(f"- Granica miasta (PRG, warstwa `ms:powiaty` usługi RCN): **{city.area / 1e6:.1f} km²**")
    diff = osiedla.union_all().symmetric_difference(city).area / 1e6
    add(
        f"- Osiedla: **{len(osiedla)}** (uchwała XX/419/16); różnica sumy osiedli i granicy "
        f"miasta: {diff:.3f} km²"
    )
    inside = flats.within(city)
    add(
        f"- Lokale mieszkalne RCN wewnątrz granicy miasta: **{100 * inside.mean():.2f}%** "
        f"({(~inside).sum()} poza)\n"
    )

    add("## 2. Komunikacja – GTFS\n")
    fs, fe, sd = (stops[c].iloc[0] for c in ("feed_start", "feed_end", "service_date"))
    add(
        f"- Okres ważności rozkładu: {fs:%Y-%m-%d} – {fe:%Y-%m-%d}; dzień reprezentatywny: "
        f"{sd:%Y-%m-%d} ({sd:%A})"
    )
    in_city = stops.within(city)
    add(
        f"- Słupki przystankowe: {len(stops):,} (w granicach miasta: {in_city.sum():,}); "
        f"bez odjazdów w tym dniu: {(stops['dep_day'] == 0).sum()}"
    )
    add(
        f"- Słupki z tramwajem: {(stops['n_lines_tram'] > 0).sum():,}; "
        f"unikalne nazwy przystanków: {stops['stop_name'].nunique():,}\n"
    )
    desc = (
        stops.loc[
            in_city, ["n_lines", "n_lines_tram", "n_lines_bus_day", "dep_day", "dep_peak_7_9"]
        ]
        .describe(percentiles=[0.25, 0.5, 0.75, 0.95])
        .T.round(1)
    )
    add("Rozkład wskaźników na słupek (w granicach miasta):\n")
    add(_md_table(desc) + "\n")
    f_stops = fig_dir / "ctx_gtfs_stops.png"
    _fig_stops(boundary, osiedla, stops, f_stops)
    add(f"![Przystanki]({fig_dir.name}/{f_stops.name})\n")

    add("## 3. POI z OpenStreetMap\n")
    add(
        f"Snapshot Overpass: {poi['osm_snapshot'].iloc[0]}; obszar = granica miasta + "
        f"{config.OSM_BUFFER_M / 1000:.0f} km bufora.\n"
    )
    poi_in = poi.intersects(city)
    dup = duplicate_points(poi)
    desc_map = {c.name: c.description for c in CATEGORIES}
    tbl = (
        poi.assign(in_city=poi_in)
        .groupby("category")
        .agg(typ=("kind", "first"), razem=("osm_id", "size"), w_miescie=("in_city", "sum"))
    )
    tbl["możliwe duplikaty"] = dup.reindex(tbl.index).fillna(0).astype(int)
    tbl["definicja"] = [desc_map.get(c, "") for c in tbl.index]
    add(_md_table(tbl.sort_values("razem", ascending=False)) + "\n")
    add(
        "*Możliwe duplikaty* = obiekty punktowe, które mają obiekt tej samej kategorii i "
        "nazwy w promieniu 100 m (np. węzeł + obrys budynku). Do rozstrzygnięcia przy "
        "liczeniu cech.\n"
    )

    add("## 4. RCN wg osiedli\n")
    f = flats[
        (flats["tran_rodzaj_trans"] == "wolnyRynek")
        & (flats["dok_data"] >= "2023-01-01")
        & flats["price_m2"].between(3000, 50000)
    ]
    j = gpd.sjoin(f[["price_m2", "geometry"]], osiedla, predicate="within")
    stats = j.groupby("osiedle").agg(n=("price_m2", "size"), median_price_m2=("price_m2", "median"))
    os_stats = osiedla.merge(stats, left_on="osiedle", right_index=True, how="left")
    add(
        "Filtr roboczy (tylko na potrzeby opisu): wolny rynek, akt od 2023, "
        "cena/m² w przedziale 3–50 tys. zł.\n"
    )
    top = stats.sort_values("median_price_m2", ascending=False)
    add("Najdroższe i najtańsze osiedla (mediana PLN/m²):\n")
    add(_md_table(pd.concat([top.head(5), top.tail(5)]), floatfmt=",.0f") + "\n")
    add(
        f"- Osiedla z < 30 transakcjami: {(stats['n'] < 30).sum()}; bez transakcji: "
        f"{os_stats['n'].isna().sum()}\n"
    )
    f_os = fig_dir / "ctx_osiedla_price.png"
    _fig_osiedla_price(os_stats, boundary, f_os)
    add(f"![Mediana wg osiedli]({fig_dir.name}/{f_os.name})\n")

    add("## 5. Kompletność RCN vs GUS BDL\n")
    gus_path = config.INTERIM_DIR / "gus_bdl_wroclaw.parquet"
    if gus_path.exists():
        gus = pd.read_parquet(gus_path)
        add(
            "Dane GUS (P3783 – lokale sprzedane w transakcjach rynkowych, P3787 – mediana "
            "ceny/m²):\n"
        )
        add(
            _md_table(
                gus.pivot_table(index=["measure", "dimension"], columns="year", values="value")
            )
            + "\n"
        )
        rcn_year = (
            flats[flats["tran_rodzaj_trans"] == "wolnyRynek"]
            .assign(rok=lambda x: x["dok_data"].dt.year)
            .groupby(["rok", "tran_rodzaj_rynku"])
            .size()
            .unstack()
        )
        add("RCN – lokale mieszkalne, wolny rynek, wg roku aktu:\n")
        add(_md_table(rcn_year[rcn_year.index >= 2021]) + "\n")
    else:
        add(
            "**Brak danych GUS** – API BDL zwróciło limit zapytań. Uruchom "
            "`uv run rcn fetch-gus` (opcjonalnie z `BDL_CLIENT_ID`) i wygeneruj raport ponownie.\n"
        )
    add("### RCN – rynek wg roku (dla porównania)\n")
    add(_md_table(_counts(flats["tran_rodzaj_rynku"])) + "\n")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(L), encoding="utf-8")
    return out_md
