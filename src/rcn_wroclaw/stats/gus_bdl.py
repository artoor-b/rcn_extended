"""GUS Local Data Bank (BDL) reference statistics for the RCN completeness check.

Subjects (county level, annual):
- P3783 – number of residential units sold in market transactions,
- P3787 – median price per m² of residential units sold in market transactions.

Anonymous API access is rate-limited (100 req / 15 min, 1000 req / 12 h, possibly shared
per IP). Set ``BDL_CLIENT_ID`` (free registration at api.stat.gov.pl) for higher limits.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
import pandas as pd

from rcn_wroclaw import config
from rcn_wroclaw.http import USER_AGENT

log = logging.getLogger(__name__)

SUBJECTS = {"P3783": "units_sold_market", "P3787": "median_price_m2_market"}
COUNTY_NAME = "Powiat m. Wrocław"


def _client() -> httpx.Client:
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if cid := os.environ.get("BDL_CLIENT_ID"):
        headers["X-ClientId"] = cid
    return httpx.Client(base_url=config.BDL_API_URL, headers=headers, timeout=60)


def _get(client: httpx.Client, path: str, **params) -> dict:
    resp = client.get(path, params={"format": "json", "lang": "pl", **params})
    data = resp.json()
    if "errorResult" in data:
        raise RuntimeError(f"BDL API error: {data['errorResult']}")
    resp.raise_for_status()
    return data


def find_county_unit(client: httpx.Client, name: str = COUNTY_NAME) -> str:
    """Return the BDL unit id of the county-level unit for Wrocław."""
    data = _get(client, "/units/search", name="Wrocław", level=5, **{"page-size": 50})
    for unit in data.get("results", []):
        if unit.get("name") == name:
            return unit["id"]
    raise LookupError(f"{name!r} not found among {[u.get('name') for u in data['results']]}")


def fetch(out_dir: Path = config.BDL_RAW_DIR) -> pd.DataFrame:
    """Download variables of :data:`SUBJECTS` for Wrocław; cache raw JSON; return tidy table."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with _client() as client:
        unit_id = find_county_unit(client)
        frames = []
        for subject, label in SUBJECTS.items():
            raw = out_dir / f"{subject}_{unit_id}.json"
            if raw.exists():
                payload = json.loads(raw.read_text())
            else:
                variables = _get(client, "/variables", **{"subject-id": subject, "page-size": 100})
                data = _get(
                    client,
                    f"/data/by-unit/{unit_id}",
                    **{"var-id": [v["id"] for v in variables["results"]], "page-size": 100},
                )
                payload = {"variables": variables["results"], "data": data}
                raw.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
            frames.append(tidy(payload, subject, label))
    return pd.concat(frames, ignore_index=True)


def tidy(payload: dict, subject: str, label: str) -> pd.DataFrame:
    """Flatten a cached BDL payload into rows: subject, variable, dimension labels, year, value."""
    meta = {
        v["id"]: " / ".join(str(v[k]) for k in ("n1", "n2", "n3") if v.get(k))
        for v in payload["variables"]
    }
    rows = []
    for res in payload["data"].get("results", []):
        for val in res.get("values", []):
            rows.append(
                {
                    "subject": subject,
                    "measure": label,
                    "var_id": res["id"],
                    "dimension": meta.get(res["id"], ""),
                    "year": int(val["year"]),
                    "value": val.get("val"),
                }
            )
    return pd.DataFrame(rows)
