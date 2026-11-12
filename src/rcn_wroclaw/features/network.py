"""Walking (network) distances on the OSM pedestrian graph.

The OSMnx ``walk`` graph is projected to EPSG:2180 and turned into an undirected sparse
matrix. For each target category one multi-source Dijkstra run (virtual super-source
connected to the target nodes with their snapping offsets) gives the walking distance from
every graph node to the nearest target; a location then gets
``snap(location) + dist(node(location))``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from rcn_wroclaw import config

log = logging.getLogger(__name__)

WALK_CAP_M = 5000.0  # distances beyond this are capped (Dijkstra limit)
AREA_SNAP_M = 30.0  # graph nodes within this distance of a park/forest/water count as access


@dataclass
class WalkGraph:
    """Undirected walking graph: node coordinates (EPSG:2180) + symmetric CSR of lengths."""

    xy: np.ndarray
    adj: csr_matrix

    @property
    def n(self) -> int:
        return self.xy.shape[0]

    def snap(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Nearest graph node index and straight-line offset for each coordinate."""
        d, i = cKDTree(self.xy).query(xy)
        return i, d

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        coo = self.adj.tocoo()
        np.savez_compressed(path, xy=self.xy, row=coo.row, col=coo.col, w=coo.data)

    @classmethod
    def load(cls, path: Path) -> WalkGraph:
        z = np.load(path)
        n = z["xy"].shape[0]
        adj = coo_matrix((z["w"], (z["row"], z["col"])), shape=(n, n)).tocsr()
        return cls(z["xy"], adj)

    @classmethod
    def from_edges(cls, xy: np.ndarray, u: np.ndarray, v: np.ndarray, w: np.ndarray) -> WalkGraph:
        """Build from an edge list; parallel edges keep the shortest length."""
        n = xy.shape[0]
        mask = u != v
        u, v, w = u[mask], v[mask], w[mask]
        rows, cols, data = np.r_[u, v], np.r_[v, u], np.r_[w, w]
        df = (
            pd.DataFrame({"r": rows, "c": cols, "w": data})
            .groupby(["r", "c"], as_index=False)
            .min()
        )
        adj = coo_matrix((df["w"], (df["r"], df["c"])), shape=(n, n)).tocsr()
        return cls(xy, adj)


def download_walk_graph(area_2180, out: Path) -> WalkGraph:
    """Download the OSM walk network for ``area_2180`` (cached as .npz)."""
    if out.exists():
        return WalkGraph.load(out)
    import osmnx as ox

    from rcn_wroclaw.osm.poi import configure_osmnx

    configure_osmnx(out.parent / "http_cache")
    simple = area_2180.simplify(200, preserve_topology=True)
    poly = gpd.GeoSeries([simple], crs=config.CRS).to_crs("EPSG:4326").iloc[0]
    g = ox.graph_from_polygon(poly, network_type="walk", simplify=True, retain_all=False)
    g = ox.project_graph(g, to_crs=config.CRS)
    nodes, edges = ox.graph_to_gdfs(g, nodes=True, edges=True)
    node_index = pd.Series(np.arange(len(nodes)), index=nodes.index)
    xy = np.column_stack([nodes["x"].to_numpy(), nodes["y"].to_numpy()])
    e = edges.reset_index()
    wg = WalkGraph.from_edges(
        xy,
        node_index.loc[e["u"]].to_numpy(),
        node_index.loc[e["v"]].to_numpy(),
        e["length"].to_numpy(dtype=float),
    )
    wg.save(out)
    log.info("walk graph: %d nodes, %d edges -> %s", wg.n, wg.adj.nnz // 2, out)
    return wg


def distance_to_sources(
    graph: WalkGraph, src_nodes: np.ndarray, src_offsets: np.ndarray, cap: float = WALK_CAP_M
) -> np.ndarray:
    """Walking distance from every node to the nearest source (offset added at the source)."""
    if len(src_nodes) == 0:
        return np.full(graph.n, np.inf)
    n = graph.n
    # Virtual node n -> source nodes (directed), offsets as weights.
    coo = graph.adj.tocoo()
    rows = np.r_[coo.row, np.full(len(src_nodes), n)]
    cols = np.r_[coo.col, src_nodes]
    data = np.r_[coo.data, np.maximum(src_offsets, 1e-6)]
    aug = coo_matrix((data, (rows, cols)), shape=(n + 1, n + 1)).tocsr()
    d = dijkstra(aug, directed=True, indices=n, limit=cap)
    return d[:n]


def area_access_nodes(graph: WalkGraph, polygons: gpd.GeoDataFrame, snap_m: float = AREA_SNAP_M):
    """Graph nodes inside or within ``snap_m`` of any polygon (offset 0)."""
    if len(polygons) == 0:
        return np.array([], dtype=int), np.array([])
    nodes = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(graph.xy[:, 0], graph.xy[:, 1]), crs=config.CRS
    )
    zone = gpd.GeoDataFrame(geometry=polygons.geometry.buffer(snap_m).to_numpy(), crs=config.CRS)
    hit = gpd.sjoin(nodes, zone, predicate="within").index.unique().to_numpy()
    return hit, np.zeros(len(hit))


def walk_features(
    points: gpd.GeoDataFrame,
    graph: WalkGraph,
    targets: dict[str, gpd.GeoDataFrame],
    cap: float = WALK_CAP_M,
    caps: dict[str, float] | None = None,
) -> pd.DataFrame:
    """``walk_<name>_m`` for every target layer (points snapped; polygons via access nodes).

    ``caps`` overrides the distance cap per target (e.g. a city-wide one for the centre).
    """
    caps = caps or {}
    pxy = np.column_stack([points.geometry.x, points.geometry.y])
    p_node, p_off = graph.snap(pxy)
    out: dict[str, np.ndarray] = {"walk_snap_m": p_off}
    for name, tgt in targets.items():
        if len(tgt) and tgt.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            s_node, s_off = area_access_nodes(graph, tgt)
        else:
            txy = np.column_stack([tgt.geometry.x, tgt.geometry.y])
            s_node, s_off = graph.snap(txy) if len(tgt) else (np.array([], int), np.array([]))
        c = caps.get(name, cap)
        d_nodes = distance_to_sources(graph, s_node, s_off, c)
        d = p_off + d_nodes[p_node]
        out[f"walk_{name}_m"] = np.minimum(d, c)
        log.info("walk %s: median %.0f m", name, np.median(out[f"walk_{name}_m"]))
    return pd.DataFrame(out, index=points.index)
