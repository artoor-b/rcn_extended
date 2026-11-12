"""Tests for location feature computation (euclidean, transit, network)."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

from rcn_wroclaw.features.euclidean import (
    area_share,
    centre_features,
    count_within,
    dedup_points,
    location_id,
    nearest_geometry_distance,
    nearest_point_distance,
    unique_locations,
)
from rcn_wroclaw.features.network import WalkGraph, distance_to_sources, walk_features
from rcn_wroclaw.features.transit import transit_features

CRS = "EPSG:2180"


def pts(*xy):
    return gpd.GeoDataFrame(geometry=[Point(x, y) for x, y in xy], crs=CRS)


def test_location_id_and_unique_locations():
    assert location_id(np.array([1.04]), np.array([2.0]))[0] == "L10_20"
    g = pts((0, 0), (0, 0.01), (5, 5))
    loc = unique_locations(g)
    assert len(loc) == 2 and loc["location_id"].is_unique


def test_nearest_and_counts():
    p = pts((0, 0), (1000, 0))
    t = pts((0, 100), (0, 400), (0, 2000))
    np.testing.assert_allclose(nearest_point_distance(p, t), [100, np.hypot(1000, 100)])
    np.testing.assert_array_equal(count_within(p, t, 300), [1, 0])
    np.testing.assert_array_equal(count_within(p, t, 500), [2, 0])
    assert np.isnan(nearest_point_distance(p, pts())).all()


def test_nearest_geometry_inside_polygon_is_zero_and_line_distance():
    p = pts((5, 5), (20, 5))
    poly = gpd.GeoDataFrame(geometry=[box(0, 0, 10, 10)], crs=CRS)
    np.testing.assert_allclose(nearest_geometry_distance(p, poly), [0, 10])
    line = gpd.GeoDataFrame(geometry=[LineString([(0, 20), (30, 20)])], crs=CRS)
    np.testing.assert_allclose(nearest_geometry_distance(p, line), [15, 15])


def test_area_share_half_plane():
    p = pts((0, 0))
    half = gpd.GeoDataFrame(geometry=[box(0, -1000, 1000, 1000)], crs=CRS)
    assert area_share(p, half, 500)[0] == pytest.approx(0.5, abs=0.01)
    # overlapping polygons are not double counted
    two = gpd.GeoDataFrame(geometry=[box(0, -1000, 1000, 1000)] * 2, crs=CRS)
    assert area_share(p, two, 500)[0] == pytest.approx(0.5, abs=0.01)


def test_dedup_points_same_name_nearby():
    poi = gpd.GeoDataFrame(
        {
            "category": ["school", "school", "school", "school"],
            "kind": ["point"] * 4,
            "name": ["SP 1", "sp 1", "SP 1", None],
        },
        geometry=[Point(0, 0), Point(50, 0), Point(500, 0), Point(1, 0)],
        crs=CRS,
    )
    out = dedup_points(poi, 100)
    assert len(out) == 3  # second "SP 1" dropped; far one and unnamed kept


def test_centre_features_direction():
    f = centre_features(pts((0, 100), (100, 0)), centre=(0, 0))
    np.testing.assert_allclose(f["dist_rynek_m"], [100, 100])
    np.testing.assert_allclose(f["dir_rynek_cos"], [1, 0], atol=1e-9)  # north
    np.testing.assert_allclose(f["dir_rynek_sin"], [0, 1], atol=1e-9)  # east


def test_transit_features_union_of_lines():
    stops = gpd.GeoDataFrame(
        {
            "dep_day": [100, 50, 0],
            "dep_peak_7_9": [10, 5, 0],
            "n_lines_tram": [1, 0, 0],
            "routes": [["1", "2"], ["2", "100"], []],
            "routes_tram": [["1"], [], []],
        },
        geometry=[Point(0, 100), Point(0, 400), Point(0, 50)],
        crs=CRS,
    )
    f = transit_features(pts((0, 0)), stops, radii=(300, 500)).iloc[0]
    assert f["dist_stop_m"] == pytest.approx(100)  # unserved stop at 50 m ignored
    assert f["dist_tram_stop_m"] == pytest.approx(100)
    assert f["n_lines_300"] == 2 and f["n_lines_500"] == 3  # {1,2} ∪ {2,100}
    assert f["n_lines_tram_500"] == 1
    assert f["dep_day_500"] == 150 and f["dep_peak_300"] == 10


@pytest.fixture
def line_graph() -> WalkGraph:
    # 0 --100-- 1 --100-- 2 --100-- 3 along the x axis, plus a shortcut-free detour
    xy = np.array([[0, 0], [100, 0], [200, 0], [300, 0]], dtype=float)
    return WalkGraph.from_edges(
        xy, np.array([0, 1, 2, 1]), np.array([1, 2, 3, 0]), np.array([100.0, 100, 100, 150])
    )


def test_walk_graph_keeps_shortest_parallel_edge(line_graph: WalkGraph):
    assert line_graph.adj[0, 1] == 100 and line_graph.adj[1, 0] == 100


def test_distance_to_sources_multi_source_with_offsets(line_graph: WalkGraph):
    d = distance_to_sources(line_graph, np.array([0, 3]), np.array([10.0, 0.0]))
    np.testing.assert_allclose(d, [10, 110, 100, 0], atol=1e-3)  # zero offsets -> 1e-6 floor


def test_walk_features_snap_and_cap(line_graph: WalkGraph):
    p = pts((0, 5), (300, 0))
    targets = {"school": pts((300, 20))}
    f = walk_features(p, line_graph, targets, cap=250)
    # point 0: 5 m snap + 300 m path + 20 m target snap = 325 -> capped at 250
    np.testing.assert_allclose(f["walk_school_m"], [250, 20])
    np.testing.assert_allclose(f["walk_snap_m"], [5, 0])


def test_walk_features_polygon_access(line_graph: WalkGraph):
    park = gpd.GeoDataFrame(geometry=[box(190, -10, 400, 10)], crs=CRS)
    f = walk_features(pts((0, 0)), line_graph, {"park": park})
    assert f["walk_park_m"].iloc[0] == pytest.approx(200)  # node 2 is inside the park


def test_walk_round_trip_npz(tmp_path, line_graph: WalkGraph):
    path = tmp_path / "g.npz"
    line_graph.save(path)
    g2 = WalkGraph.load(path)
    assert (g2.adj != line_graph.adj).nnz == 0
    pd.testing.assert_frame_equal(pd.DataFrame(g2.xy), pd.DataFrame(line_graph.xy))


def test_walk_features_per_target_cap(line_graph: WalkGraph):
    f = walk_features(pts((0, 0)), line_graph, {"far": pts((300, 0))}, cap=100, caps={"far": 1000})
    assert f["walk_far_m"].iloc[0] == pytest.approx(300)
