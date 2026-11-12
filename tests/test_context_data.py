"""Tests for stage-2 loaders: GML polygons, GTFS service levels, OSM classification, BDL."""

import datetime as dt
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, box

from rcn_wroclaw.geo.boundaries import parse_gml_polygons
from rcn_wroclaw.osm.poi import CATEGORIES, classify
from rcn_wroclaw.stats.gus_bdl import client_id, tidy
from rcn_wroclaw.transit.gtfs import active_services, representative_weekday, stop_service_levels

POLY_GML = """<?xml version="1.0" encoding="UTF-8"?>
<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0"
  xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:ms="http://mapserver.gis.umn.edu/mapserver">
 <wfs:member><ms:powiaty><ms:msGeometry>
  <gml:Polygon gml:id="p1" srsName="urn:ogc:def:crs:EPSG::2180">
   <gml:exterior><gml:LinearRing><gml:posList srsDimension="2">
     100 200 100 300 150 300 150 200 100 200
   </gml:posList></gml:LinearRing></gml:exterior>
  </gml:Polygon>
 </ms:msGeometry></ms:powiaty></wfs:member>
</wfs:FeatureCollection>"""


def test_parse_gml_polygon_swaps_axes(tmp_path: Path):
    f = tmp_path / "p.gml"
    f.write_text(POLY_GML)
    geom = parse_gml_polygons(f)
    # posList is (northing, easting): easting 200..300, northing 100..150
    assert geom.bounds == (200.0, 100.0, 300.0, 150.0)


@pytest.fixture
def gtfs_dir(tmp_path: Path) -> Path:
    files = {
        "calendar.txt": "﻿service_id,monday,tuesday,wednesday,thursday,friday,saturday,"
        "sunday,start_date,end_date\n"
        "WD,1,1,1,1,0,0,0,20251124,20251212\nSA,0,0,0,0,0,1,0,20251124,20251212\n",
        "calendar_dates.txt": "service_id,date,exception_type\n",
        "stops.txt": "stop_id,stop_code,stop_name,stop_lat,stop_lon\n"
        "1,11,Rynek,51.11,17.03\n2,22,Dworzec,51.10,17.04\n3,33,Pusty,51.12,17.05\n",
        "routes.txt": "route_id,agency_id,route_short_name,route_long_name,route_desc,route_type,"
        "route_type2_id\n"
        "T1,3,1,,,0,31\nB1,2,100,,,3,30\nN1,2,240,,,3,40\n",
        "trips.txt": "route_id,service_id,trip_id\n"
        "T1,WD,t1\nT1,WD,t2\nB1,WD,b1\nN1,WD,n1\nB1,SA,b_sat\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type,"
        "drop_off_type\n"
        "t1,07:10:00,07:10:00,1,0,0,0\nt1,07:20:00,07:20:00,2,1,1,0\n"
        "t2,12:00:00,12:00:00,1,0,0,0\n"
        "b1,08:00:00,08:00:00,1,0,0,0\nb1,08:10:00,08:10:00,2,1,0,0\n"
        "n1,23:30:00,23:30:00,2,0,0,0\n"
        "b_sat,09:00:00,09:00:00,1,0,0,0\n",
    }
    for name, content in files.items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    return tmp_path


def test_gtfs_weekday_and_services(gtfs_dir: Path):
    day = representative_weekday(gtfs_dir)
    assert day == dt.date(2025, 11, 26) and day.weekday() == 2
    assert active_services(gtfs_dir, day) == ["WD"]


def test_gtfs_stop_service_levels(gtfs_dir: Path):
    g = stop_service_levels(gtfs_dir, min_departures=1).set_index("stop_id")
    assert g.crs.to_epsg() == 2180
    # Rynek: tram t1, t2 + bus b1 (saturday trip excluded)
    assert g.loc["1", "dep_day"] == 3
    assert g.loc["1", "n_lines"] == 2
    assert g.loc["1", "n_lines_tram"] == 1
    assert g.loc["1", "dep_peak_7_9"] == 2
    # Dworzec: t1 has pickup_type=1 (alighting only) -> not counted; b1 + night n1 counted
    assert g.loc["2", "dep_day"] == 2
    assert g.loc["2", "n_lines_bus_day"] == 1
    assert g.loc["2", "n_lines_night"] == 1
    assert g.loc["2", "dep_6_22"] == 1
    # stop without service
    assert g.loc["3", "dep_day"] == 0 and g.loc["3", "n_lines"] == 0


def test_osm_classify_categories_and_kinds():
    feats = gpd.GeoDataFrame(
        {
            "element": ["node", "node", "way", "way", "node"],
            "id": [1, 2, 3, 4, 5],
            "name": ["SP 1", "Żłobek Miś", "Park Szczytnicki", None, "Przedszkole 5"],
            "amenity": ["school", "kindergarten", None, None, "kindergarten"],
            "leisure": [None, None, "park", None, None],
            "railway": [None, None, None, "station", None],
        },
        geometry=[
            Point(17.03, 51.11),
            Point(17.04, 51.11),
            box(17.07, 51.11, 17.09, 51.12),
            box(17.03, 51.09, 17.04, 51.10),
            Point(17.05, 51.11),
        ],
        crs="EPSG:4326",
    )
    poi = classify(feats)
    assert poi.crs.to_epsg() == 2180
    cats = poi.groupby("category")["osm_id"].apply(set).to_dict()
    assert cats["school"] == {1}
    assert cats["nursery"] == {2}
    assert cats["kindergarten"] == {5}
    assert cats["park"] == {3}
    assert cats["rail_station"] == {4}
    # point categories become points, area categories keep polygons
    assert (poi.loc[poi.category == "rail_station"].geom_type == "Point").all()
    assert (poi.loc[poi.category == "park"].geom_type == "Polygon").all()


def test_osm_category_names_unique():
    names = [c.name for c in CATEGORIES]
    assert len(names) == len(set(names))


def test_bdl_tidy():
    payload = {
        "variables": [{"id": 1, "n1": "rynek pierwotny", "n2": None}],
        "data": {"results": [{"id": 1, "values": [{"year": "2024", "val": 123, "attrId": 1}]}]},
    }
    df = tidy(payload, "P3783", "units_sold_market")
    assert df.to_dict("records") == [
        {
            "subject": "P3783",
            "measure": "units_sold_market",
            "var_id": 1,
            "dimension": "rynek pierwotny",
            "year": 2024,
            "value": 123,
        }
    ]


def test_gtfs_route_lists(gtfs_dir: Path):
    g = stop_service_levels(gtfs_dir, min_departures=1).set_index("stop_id")
    assert g.loc["1", "routes"] == ["B1", "T1"]
    assert g.loc["1", "routes_tram"] == ["T1"]
    assert g.loc["3", "routes"] == []


def test_gtfs_regular_service_rule_drops_rare_pairs(gtfs_dir: Path):
    # Line T1 has 2 departures at Rynek and 0 (alighting only) at Dworzec: with a floor of
    # 2 departures T1 stays at Rynek, while B1 (1 departure per stop) is dropped everywhere.
    g = stop_service_levels(gtfs_dir, min_departures=2).set_index("stop_id")
    assert g.loc["1", "routes"] == ["T1"]
    assert g.loc["2", "dep_day"] == 0


def test_bdl_client_id_from_env_file(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BDL_CLIENT_ID", raising=False)
    env = tmp_path / ".env"
    assert client_id(env) is None
    env.write_text("# comment\nOTHER=1\nBDL_CLIENT_ID = 'abc-123'\n")
    assert client_id(env) == "abc-123"
    monkeypatch.setenv("BDL_CLIENT_ID", "from-env")
    assert client_id(env) == "from-env"
