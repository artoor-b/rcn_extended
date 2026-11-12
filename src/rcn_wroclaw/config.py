"""Project-wide constants: paths, CRS, area of interest, service URLs."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
INTERIM_DIR = DATA_DIR / "interim"
PROCESSED_DIR = DATA_DIR / "processed"
REPORTS_DIR = PROJECT_ROOT / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

# Single working CRS for all geometry (PUWG 1992).
CRS = "EPSG:2180"

# Wrocław = city with county rights, TERYT county code 0264.
TERYT_WROCLAW = "0264"

# Wrocław bounding box in EPSG:2180 as (easting_min, northing_min, easting_max, northing_max),
# padded ~1 km around the city limits. Records are filtered by TERYT afterwards.
WROCLAW_BBOX_2180 = (345_000.0, 354_000.0, 374_000.0, 375_000.0)

# Rynek (main market square) in EPSG:2180, easting/northing.
RYNEK_2180 = (362_280.0, 362_199.0)

# RCN WFS (GUGiK). WFS 2.0.0, GML 3.2 output only.
# NOTE: with WFS 2.0.0 + urn CRS the server uses EPSG axis order for EPSG:2180,
# i.e. (northing, easting) – both in GML <gml:pos> and in the BBOX parameter.
# Verified 2026-11-12 against WFS 1.0.0 output, which uses (easting, northing).
RCN_WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/rcn"
RCN_LAYER_LOKALE = "ms:lokale"
RCN_PAGE_SIZE = 1000

RCN_RAW_DIR = RAW_DIR / "rcn" / "wfs"
RCN_LOKALE_INTERIM = INTERIM_DIR / "rcn_lokale_raw.parquet"

# --- Stage 2: boundaries, transit, POI, reference statistics -----------------------------

# Official county boundary (PRG) served by the same RCN WFS, layer without attributes.
RCN_LAYER_POWIATY = "ms:powiaty"
BOUNDARY_RAW_DIR = RAW_DIR / "boundaries"
BOUNDARY_CITY = INTERIM_DIR / "boundary_wroclaw.parquet"

# Wrocław "osiedla" (48 auxiliary units, council resolution XX/419/16), SHP in CS2000/18.
OSIEDLA_URL = "https://geoportal.wroclaw.pl/www/pliki/osiedla/granice-osiedli.zip"
OSIEDLA_SHP_IN_ZIP = "GraniceOsiedli/GraniceOsiedli.shp"
OSIEDLA = INTERIM_DIR / "osiedla.parquet"

# GTFS MPK Wrocław. The official URL (MobilityData catalog id 980) rejects scripted clients
# (HTTP 403, checked 2026-11-12); the MobilityData mirror is used by default.
GTFS_OFFICIAL_URL = (
    "https://www.wroclaw.pl/open-data/87b09b32-f076-4475-8ec9-6020ed1f9ac0/"
    "OtwartyWroclaw_rozklad_jazdy_GTFS.zip"
)
GTFS_MIRROR_URL = (
    "https://storage.googleapis.com/storage/v1/b/mdb-latest/o/"
    "pl-dolnoslaskie-mpk-wroclaw-gtfs-980.zip?alt=media"
)
GTFS_RAW_DIR = RAW_DIR / "gtfs"
GTFS_STOPS = INTERIM_DIR / "gtfs_stops.parquet"

# OpenStreetMap via Overpass (OSMnx). POIs are fetched within the city boundary buffered
# by this distance so nearest-distance / buffer features are not truncated at the border.
OSM_RAW_DIR = RAW_DIR / "osm"
OSM_BUFFER_M = 2000.0
POI_DIR = INTERIM_DIR / "poi"
POI_ALL = INTERIM_DIR / "poi_osm.parquet"

# GUS BDL (Local Data Bank) – reference counts / medians for the RCN completeness check.
BDL_API_URL = "https://bdl.stat.gov.pl/api/v1"
BDL_RAW_DIR = RAW_DIR / "gus_bdl"
