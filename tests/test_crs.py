"""Axis-order sanity checks: RCN points must land in Wrocław after reprojection."""

from pyproj import Transformer

from rcn_wroclaw import config
from rcn_wroclaw.rcn.parse import parse_pos

TO_WGS84 = Transformer.from_crs(config.CRS, "EPSG:4326", always_xy=True)


def test_parse_pos_swaps_northing_easting():
    # Real-world example: RCN WFS 2.0 '<gml:pos>361084.632276 361411.462698</gml:pos>';
    # WFS 1.0.0 returns the same point as '361411.462698,361084.632276' (easting, northing).
    p = parse_pos("361084.632276 361411.462698")
    assert (p.x, p.y) == (361411.462698, 361084.632276)


def test_rynek_reprojects_to_wroclaw_market_square():
    lon, lat = TO_WGS84.transform(*config.RYNEK_2180)
    assert abs(lon - 17.032) < 0.002
    assert abs(lat - 51.110) < 0.002


def test_bbox_contains_wroclaw():
    e_min, n_min, e_max, n_max = config.WROCLAW_BBOX_2180
    lon0, lat0 = TO_WGS84.transform(e_min, n_min)
    lon1, lat1 = TO_WGS84.transform(e_max, n_max)
    # Wrocław limits approx. 16.80–17.18 E, 51.04–51.21 N
    assert lon0 < 16.80 and lon1 > 17.18
    assert lat0 < 51.04 and lat1 > 51.21
