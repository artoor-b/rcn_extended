# RCN Wrocław – location accessibility vs. transaction price prediction

## Goal
Research + application project. Question: does enriching Rejestr Cen Nieruchomości (RCN)
transaction data with location features (public transport and amenity accessibility)
significantly improve prediction of apartment transaction prices in Wrocław?
Compare **"RCN only"** vs **"RCN + location"** models (Ridge baseline and LightGBM), with
bootstrap confidence intervals and SHAP feature importance.

## Scope
- Area: Wrocław city (powiat grodzki, TERYT `0264`).
- Objects: residential units only (`lok_funkcja == "mieszkalna"`), transaction records
  (no valuations, no land plots, no buildings).
- Target: price per m² (`price_m2`); auxiliary: total price.

## Working agreement
- Communicate with the project owner **in Polish**; code, comments, docstrings **in English**.
- Work in stages; after each stage summarise what was done, decisions, risks/doubts,
  and wait for approval before the next stage.
- If the meaning of an RCN field (or any data source field) is unclear – **ask, don't guess**.
- Verify that a data source is current/available before relying on it; if unavailable,
  propose a replacement and ask.

## Environment notes (macOS)
- LightGBM needs OpenMP: `brew install libomp` (installed 2026-11-13; Xcode license accepted).
- GUS BDL key: put `BDL_CLIENT_ID=...` in the git-ignored `.env` (never in code or chat).

## Conventions
- **One CRS for all geometry: EPSG:2180** (PUWG 1992). Geometry columns hold
  (easting, northing). Reproject inputs on load.
- RCN WFS 2.0 GML uses **(northing, easting)** axis order for urn EPSG::2180 – both in
  `<gml:pos>` and in the `BBOX` parameter. Handled in `rcn/parse.py::parse_pos` and
  `rcn/wfs.py::Tile.bbox_param`; covered by `tests/test_crs.py`.
- WFS paging must use `SORTBY=gid ASC` – unsorted paging silently drops records.
- The server-side `TERYT` attribute filter is unreliable – query by bbox tiles, filter
  `teryt == "0264"` client-side.
- Column names = original RCN attribute names lower-cased (`TRAN_CENA_BRUTTO` → `tran_cena_brutto`).
- **Privacy:** `DOK_TWORCA` (notary names/addresses) is dropped at parse time and never
  stored in interim/processed data or exposed in the app.
- Data layout (everything under `data/` is git-ignored and reproducible):
  - `data/raw/` – untouched downloads (e.g. `data/raw/rcn/wfs/<snapshot>/tile_ii_jj_pkkk.gml`)
  - `data/interim/` – parsed, typed, not yet cleaned (Parquet/GeoParquet)
  - `data/processed/` – model-ready tables (Parquet/GeoParquet)
- Code lives in `src/rcn_wroclaw/` as importable modules; notebooks in `notebooks/` are for
  exploration only and must not contain pipeline logic.
- Tests (pytest) are required for cleaning functions and feature computation. Fixtures are
  small synthetic files in `tests/fixtures/` (no real personal data).
- Location features are computed per unique point (`location_id` = coordinates rounded to
  0.1 m, see `features/euclidean.py::location_id`) and joined to transactions; the same
  functions must work for arbitrary points (web app predictions).
- GTFS line counts use only (line, stop) pairs with regular service (≥4 departures and ≥20%
  of the line's median per-stop departures) – removes depot runs.
- Cleaning rules (agreed 2026-11-12): flats only, `wolnyRynek` only, share 1/1, exactly one
  flat per transaction, deeds from 2023-01, area 15–300 m², price/m² 3–50k PLN and 0.5% tails
  trimmed per year; price = `lok_cena_brutto` (fallback `tran_cena_brutto` only for one-unit
  transactions); parking remarks kept as flag `has_parking_note`.
- Baseline "RCN only" includes osiedle; extra variants: without osiedle, + location,
  + location + x/y. Target log(price/m²); metrics on PLN/m² and total price.
- Uncertainty: never i.i.d. bootstrap over transactions – resample buildings (temporal test)
  or 1 km blocks (spatial CV). Report "new locations" (no training transactions) separately:
  location features can act as building identifiers for repeat-selling buildings.
- Validation must avoid leakage: temporal split (train older / test newer) + spatial CV.
- Ruff for lint/format (line length 100).

## Layout
```
src/rcn_wroclaw/
  config.py            paths, CRS, TERYT, bbox, service URLs
  cli.py               typer CLI (`rcn ...`)
  rcn/wfs.py           tiled + paged WFS download with on-disk cache
  rcn/parse.py         GML -> typed GeoDataFrame (dedup by gid, TERYT filter)
  rcn/derive.py        derived columns (units per transaction, price per m²)
  reports/eda_rcn.py   exploratory Markdown report + figures
  http.py              cached file download helper
  geo/boundaries.py    city boundary (PRG via RCN WFS `ms:powiaty`) + osiedla (geoportal.wroclaw.pl)
  transit/gtfs.py      GTFS -> per-stop lines/departures for a representative Wednesday
  osm/poi.py           one Overpass query (OSMnx) -> POI categories (CATEGORIES list)
  stats/gus_bdl.py     GUS BDL reference stats (units sold P3783, median price/m² P3787)
  reports/context.py   stage-2 report (02_context_data.md)
  features/euclidean.py  location ids, POI dedup, nearest distances, buffer counts, green shares
  features/transit.py    stops/lines/departures around a point (GTFS)
  features/network.py    OSM walk graph (scipy sparse) + multi-source Dijkstra walking distances
  features/build.py      assembles data/processed/location_features.parquet (1 row / location)
  reports/features.py    stage-3 report (03_features.md)
  clean/rcn.py           cleaning rules (CleaningConfig) + per-step log
  model/dataset.py       model dataset, feature variants, train-only monthly price index
  model/models.py        Ridge pipeline (log1p + scaling + one-hot) and LightGBM wrapper
  model/evaluate.py      temporal hold-out (test 2026) + spatial GroupKFold (1 km blocks)
  reports/models.py      stage-4 report (04_models.md)
  model/bootstrap.py     paired cluster bootstrap (clusters = buildings / 1 km blocks)
  model/explain.py       LightGBM TreeSHAP (pred_contrib), feature groups; saves models/*.txt
  reports/evaluation.py  stage-5 report (05_evaluation.md)
tests/                 pytest; fixtures/ with synthetic GML
reports/               generated reports (01_rcn_eda.md, figures/)
notebooks/             exploration only
```

## Commands
```bash
uv sync                                   # install env (Python 3.12)
uv run rcn fetch-rcn --sample 4           # quick sample: 4 tiles around Rynek
uv run rcn fetch-rcn                      # full Wrocław bbox (cached, re-runnable)
uv run rcn parse-rcn                      # latest snapshot -> data/interim/rcn_lokale_raw.parquet
uv run rcn eda-rcn                        # -> reports/01_rcn_eda.md
uv run rcn fetch-boundaries               # city boundary + osiedla -> data/interim/
uv run rcn fetch-gtfs [--source mirror|official|path.zip]   # -> data/interim/gtfs_stops.parquet
uv run rcn fetch-osm                      # Overpass -> data/interim/poi_osm.parquet
uv run rcn fetch-gus                      # GUS BDL (set BDL_CLIENT_ID to avoid rate limits)
uv run rcn context-report                 # -> reports/02_context_data.md
uv run rcn build-features [--osm-snapshot D] [--no-network]  # -> data/processed/location_features.parquet
uv run rcn features-report                # -> reports/03_features.md
uv run rcn build-dataset                  # cleaning -> data/processed/model_dataset.parquet
uv run rcn train-eval [--splits temporal,spatial]  # -> predictions / metrics parquet
uv run rcn models-report                  # -> reports/04_models.md
uv run rcn evaluation-report              # bootstrap + SHAP + maps -> reports/05_evaluation.md
uv run pytest                             # tests
uv run ruff check src tests && uv run ruff format src tests
```
Make shortcuts: `make setup | fetch-rcn | fetch-rcn-sample | parse-rcn | eda | rcn-all | boundaries | gtfs | osm | gus | context | context-all | test | lint | fmt`.

## Data sources
| Source | Use | Status |
|---|---|---|
| RCN WFS GUGiK `https://mapy.geoportal.gov.pl/wss/service/rcn`, layer `ms:lokale` | transactions | verified 2026-11-12 (WFS 2.0.0, GML only) |
| Same WFS, layer `ms:powiaty` | official city boundary (PRG) | verified 2026-11-12 (292.5 km²) |
| `geoportal.wroclaw.pl/www/pliki/osiedla/granice-osiedli.zip` | 48 osiedla (resolution XX/419/16, file 2017, CS2000/18) | verified 2026-11-12 |
| GTFS MPK Wrocław – official `wroclaw.pl/open-data/...` | stops, lines, departures | HTTP 403 for scripted clients (2026-11-12) |
| GTFS – MobilityData mirror (catalog id 980) | same | used; mirror file 2026-04-13 contains feed valid 2025-11-24..2025-12-12 |
| OpenStreetMap via Overpass (OSMnx), city + 2 km buffer | POI, parks, water, rail, major roads | snapshot 2026-11-12 |
| GUS BDL API (P3783, P3787, county level) | RCN completeness check | anonymous rate limit hit 2026-11-12 – needs retry / `BDL_CLIENT_ID` |
| RCN county GeoPackage (Geoportal "Pobierz dane") | alternative completeness check | no direct URL; manual download only |
| Optional: RSPO, Rejestr żłobków, noise maps, demography (`dem-rejurb-rejstat-shp.zip`), EMUiA addresses | extra features | later |

Known data caveats: OSM is a current snapshot applied to 2022–2026 transactions; OSM
under-represents nurseries (59) and hospitals (16); some point POI are double-mapped (node + area).

## Stages
1. ✅ Repo skeleton, RCN download, EDA report.
2. ✅ POI / transport / boundary data ingestion (GUS completeness check pending).
3. ✅ Feature engineering (distances, buffer counts, GTFS service levels, network distances).
4. ✅ Cleaning rules + modelling (Ridge, LightGBM × 4 feature variants).
5. ✅ Evaluation (MAE, RMSE, MAPE, R², cluster-bootstrap CIs, SHAP, error maps).
6. Web app (Next.js + TypeScript + MapLibre GL) – design data/API first.
