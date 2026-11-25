"""Where the dashboard keeps its data (plain JSON files committed to the repo)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
NAV_DIR = DATA / "nav"
SNAP_DIR = DATA / "snapshots"
FUND_MAP = DATA / "fund_map.json"
STATUS = DATA / "status.json"


def find_holdings_file() -> Path | None:
    for name in ("holdings.xlsx", "holdings.xls", "holdings.csv"):
        p = ROOT / name
        if p.exists():
            return p
    xs = sorted(ROOT.glob("*.xlsx"))
    return xs[0] if xs else None


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, default=str, sort_keys=False))


def load_nav(code: str) -> list:
    return read_json(NAV_DIR / f"{code}.json", {}).get("nav", [])


def load_snapshots(code: str) -> dict:
    """-> {"YYYY-MM": snapshot}, oldest first"""
    m = read_json(SNAP_DIR / f"{code}.json", {}).get("months", {})
    return dict(sorted(m.items()))
