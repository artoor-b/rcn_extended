"""Command-line entry point: ``uv run rcn <command>``."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
import typer

from rcn_wroclaw import config

app = typer.Typer(help="RCN Wrocław data pipeline.", no_args_is_help=True)


@app.callback()
def _main(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _latest_snapshot() -> Path:
    snaps = sorted(p for p in config.RCN_RAW_DIR.glob("*") if p.is_dir())
    if not snaps:
        raise typer.BadParameter(f"No RCN snapshots in {config.RCN_RAW_DIR}; run fetch-rcn first.")
    return snaps[-1]


@app.command("fetch-rcn")
def fetch_rcn(
    sample: int | None = typer.Option(
        None, help="Only download the N tiles closest to Rynek (quick sample)."
    ),
    snapshot: str = typer.Option(
        dt.date.today().isoformat(), help="Snapshot folder name under data/raw/rcn/wfs/."
    ),
    tile_size: float = typer.Option(2000.0, help="Tile edge in metres."),
) -> None:
    """Download RCN apartment-unit records (ms:lokale) for the Wrocław bbox."""
    from rcn_wroclaw.rcn.wfs import central_tiles, fetch_lokale, make_tiles

    tiles = make_tiles(config.WROCLAW_BBOX_2180, tile_size)
    if sample:
        tiles = central_tiles(tiles, sample)
    out_dir = config.RCN_RAW_DIR / snapshot
    paths = fetch_lokale(out_dir, tiles)
    typer.echo(f"{len(paths)} GML page(s) from {len(tiles)} tile(s) in {out_dir}")


@app.command("parse-rcn")
def parse_rcn(
    snapshot: str | None = typer.Option(None, help="Snapshot folder (default: latest)."),
    out: Path = typer.Option(config.RCN_LOKALE_INTERIM, help="Output GeoParquet."),
) -> None:
    """Parse cached GML pages into a GeoParquet of Wrocław unit records."""
    from rcn_wroclaw.rcn.parse import parse_files

    snap_dir = config.RCN_RAW_DIR / snapshot if snapshot else _latest_snapshot()
    paths = sorted(snap_dir.glob("*.gml"))
    gdf = parse_files(paths)
    out.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(out)
    typer.echo(f"{len(gdf):,} records (TERYT {config.TERYT_WROCLAW}) -> {out}")


@app.command("eda-rcn")
def eda_rcn(
    src: Path = typer.Option(config.RCN_LOKALE_INTERIM, help="Input GeoParquet."),
    out: Path = typer.Option(config.REPORTS_DIR / "01_rcn_eda.md", help="Output Markdown."),
) -> None:
    """Generate the exploratory report on RCN unit records."""
    from rcn_wroclaw.reports.eda_rcn import build_report

    gdf = gpd.read_parquet(src)
    src = src.resolve()
    shown = src.relative_to(config.PROJECT_ROOT) if src.is_relative_to(config.PROJECT_ROOT) else src
    note = f"Wejście: `{shown}`."
    # Figures live next to the report so relative image links work.
    path = build_report(gdf, out, out.parent / "figures", source_note=note)
    typer.echo(f"Report -> {path}")


@app.command("fetch-boundaries")
def fetch_boundaries() -> None:
    """Download the city boundary (PRG via RCN WFS) and Wrocław osiedla."""
    from rcn_wroclaw.geo.boundaries import fetch_city_boundary, fetch_osiedla

    b = fetch_city_boundary()
    o = fetch_osiedla()
    typer.echo(f"city: {b.geometry.area.iloc[0] / 1e6:.1f} km²; osiedla: {len(o)}")


@app.command("fetch-gtfs")
def fetch_gtfs(
    source: str = typer.Option(
        "mirror", help="'mirror' (MobilityData), 'official' (wroclaw.pl) or a local .zip path."
    ),
    snapshot: str = typer.Option(dt.date.today().isoformat(), help="Folder under data/raw/gtfs/."),
) -> None:
    """Download/copy a GTFS feed and compute stop-level service indicators."""
    import shutil

    from rcn_wroclaw.http import download
    from rcn_wroclaw.transit.gtfs import extract, feed_period, stop_service_levels

    dest = config.GTFS_RAW_DIR / snapshot / "gtfs.zip"
    if source in ("mirror", "official"):
        url = config.GTFS_MIRROR_URL if source == "mirror" else config.GTFS_OFFICIAL_URL
        download(url, dest)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(Path(source), dest)
    gtfs_dir = extract(dest)
    start, end = feed_period(gtfs_dir)
    stops = stop_service_levels(gtfs_dir)
    stops["feed_start"], stops["feed_end"] = pd.Timestamp(start), pd.Timestamp(end)
    stops.to_parquet(config.GTFS_STOPS)
    typer.echo(f"feed {start}..{end}: {len(stops)} stops -> {config.GTFS_STOPS}")


@app.command("fetch-osm")
def fetch_osm(
    snapshot: str = typer.Option(dt.date.today().isoformat(), help="Folder under data/raw/osm/."),
) -> None:
    """Fetch OSM features (Overpass) around Wrocław and classify them into POI categories."""
    from rcn_wroclaw.geo.boundaries import load_city_boundary
    from rcn_wroclaw.osm.poi import classify, fetch_features

    area = load_city_boundary().buffer(config.OSM_BUFFER_M)
    feats = fetch_features(area, config.OSM_RAW_DIR / snapshot)
    poi = classify(feats)
    poi["osm_snapshot"] = snapshot
    poi.to_parquet(config.POI_ALL)
    typer.echo(
        f"{len(poi):,} POI rows in {poi['category'].nunique()} categories -> {config.POI_ALL}"
    )


@app.command("fetch-gus")
def fetch_gus() -> None:
    """Download GUS BDL reference statistics (set BDL_CLIENT_ID for higher API limits)."""
    from rcn_wroclaw.stats.gus_bdl import fetch

    df = fetch()
    out = config.INTERIM_DIR / "gus_bdl_wroclaw.parquet"
    df.to_parquet(out)
    typer.echo(f"{len(df)} rows -> {out}")


@app.command("context-report")
def context_report(
    out: Path = typer.Option(config.REPORTS_DIR / "02_context_data.md", help="Output Markdown."),
) -> None:
    """Report on boundaries, GTFS, OSM POI and RCN coverage (stage 2)."""
    from rcn_wroclaw.reports.context import build_report

    path = build_report(out, out.parent / "figures")
    typer.echo(f"Report -> {path}")


@app.command("build-features")
def build_features(
    osm_snapshot: str = typer.Option(
        dt.date.today().isoformat(), help="OSM snapshot folder (walk graph cache)."
    ),
    network: bool = typer.Option(True, help="Compute walking distances (downloads the graph)."),
) -> None:
    """Compute location features for all unique RCN locations -> data/processed/."""
    from rcn_wroclaw.features.build import (
        FEATURES_PATH,
        build_location_features,
        rcn_locations,
    )
    from rcn_wroclaw.features.network import download_walk_graph
    from rcn_wroclaw.geo.boundaries import load_city_boundary

    graph = None
    if network:
        area = load_city_boundary().buffer(config.OSM_BUFFER_M)
        graph = download_walk_graph(area, config.OSM_RAW_DIR / osm_snapshot / "walk_graph.npz")
    feats = build_location_features(
        rcn_locations(),
        gpd.read_parquet(config.POI_ALL),
        gpd.read_parquet(config.GTFS_STOPS),
        gpd.read_parquet(config.OSIEDLA),
        graph,
    )
    FEATURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(FEATURES_PATH)
    typer.echo(f"{len(feats):,} locations x {feats.shape[1] - 2} features -> {FEATURES_PATH}")


@app.command("features-report")
def features_report(
    out: Path = typer.Option(config.REPORTS_DIR / "03_features.md", help="Output Markdown."),
) -> None:
    """Report on location features (coverage, distributions, link with price/m²)."""
    from rcn_wroclaw.reports.features import build_report

    path = build_report(out, out.parent / "figures")
    typer.echo(f"Report -> {path}")


if __name__ == "__main__":
    app()
