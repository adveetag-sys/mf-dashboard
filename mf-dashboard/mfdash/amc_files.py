"""Read fund houses' official monthly portfolio files (Excel) dropped into
data/amc_portfolios/<YYYY-MM>/ and turn them into that month's holdings.

Every fund house lays its file out differently, so nothing here depends on a
fixed layout: for each of your funds it finds the block titled with the fund's
name, then the header row with an ISIN column and a "% to NAV" column, and reads
rows until the block ends.

Works with .xlsx, .xls and .xlsb. Zip files are opened too.
"""
from __future__ import annotations

import calendar
import re
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from .analysis import CASH_PAT
from .resolve import _core

ISIN_RE = re.compile(r"^IN[A-Z0-9]{10}$")
PCT_HEADS = ("% to nav", "% of nav", "%to nav", "% to net asset", "% of net asset", "%age to nav", "% to aum",
             "% of aum", "to net assets", "% to net", "percentage to nav", "% of total", "% net assets", "% to nav")
NAME_HEADS = ("name of the instrument", "name of instrument", "name of the security", "company", "issuer",
              "security", "instrument", "name")
IND_HEADS = ("industry", "sector", "rating / industry", "rating/industry")


def _txt(v) -> str:
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def _num(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _sheets(path: Path):
    """Yield (label, DataFrame) for every sheet in an Excel file or in the Excel files inside a zip."""
    suf = path.suffix.lower()
    if suf == ".zip":
        tmp = Path(tempfile.gettempdir()) / f"mfzip_{path.stem}"
        if not tmp.exists():
            with zipfile.ZipFile(path) as z:
                z.extractall(tmp)
        for p in sorted(tmp.rglob("*")):
            if p.suffix.lower() in (".xlsx", ".xls", ".xlsb", ".xlsm"):
                yield from _sheets(p)
        return
    engine = {".xls": "xlrd", ".xlsb": "pyxlsb"}.get(suf)
    try:
        book = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine=engine)
    except Exception as e:  # unreadable file: skip, logged by caller
        print(f"Couldn't read {path.name}: {e}")
        return
    for name, df in book.items():
        yield f"{path.name}:{name}", df


def _title_score(row_text: str, names: list[str]) -> float:
    t = re.sub(r"\(.*?\)", " ", row_text)
    tc = _core(t)
    if not tc or len(tc) > 140:
        return 0
    best = 0
    for n in names:
        nc = _core(n)
        if not nc:
            continue
        s = 0.7 * fuzz.token_set_ratio(nc, tc) + 0.3 * fuzz.token_sort_ratio(nc, tc)
        best = max(best, s)
    return best


def _find_cols(row: list[str]):
    low = [c.lower() for c in row]
    col = {}
    for i, c in enumerate(low):
        if "isin" in c and "isin" not in col:
            col["isin"] = i
        if "pct" not in col and any(h in c for h in PCT_HEADS):
            col["pct"] = i
        if "ind" not in col and any(c.startswith(h) or c == h for h in IND_HEADS):
            col["ind"] = i
    for i, c in enumerate(low):
        if "name" not in col and any(h in c for h in NAME_HEADS) and i != col.get("isin"):
            col["name"] = i
    return col if {"isin", "pct", "name"} <= set(col) else None


def _parse_block(rows: list[list[str]], start: int) -> list[dict]:
    """rows: sheet as text cells. start: index of the fund's title row."""
    hdr, col = None, None
    for r in range(start, min(start + 20, len(rows))):
        c = _find_cols(rows[r])
        if c:
            hdr, col = r, c
            break
    if hdr is None:
        return []
    out = []
    for r in range(hdr + 1, len(rows)):
        cells = rows[r]
        name = cells[col["name"]] if col["name"] < len(cells) else ""
        low = " ".join(cells).lower()
        if out and ("grand total" in low or _find_cols(cells)):
            break
        isin = (cells[col["isin"]] if col["isin"] < len(cells) else "").upper().replace(" ", "")
        pct = _num(cells[col["pct"]]) if col["pct"] < len(cells) else None
        if pct is None or not name:
            continue
        if ISIN_RE.match(isin) or CASH_PAT.search(name):
            out.append({"name": re.sub(r"\s+", " ", name), "isin": isin if ISIN_RE.match(isin) else None,
                        "sector": (cells[col["ind"]] if "ind" in col and col["ind"] < len(cells) else "") or "Others",
                        "instrument": "Equity" if isin.startswith("INE") else ("Cash" if CASH_PAT.search(name) else ""),
                        "pct": pct})
    if not out:
        return []
    tot = sum(h["pct"] for h in out)
    if 0 < tot <= 1.5:                       # file gives fractions (0.0523) instead of percent (5.23)
        for h in out:
            h["pct"] = h["pct"] * 100
    for h in out:
        h["pct"] = round(h["pct"], 4)
    # the same security can appear twice (e.g. split across rows): add up
    merged = {}
    for h in out:
        k = h["isin"] or h["name"].lower()
        if k in merged:
            merged[k]["pct"] = round(merged[k]["pct"] + h["pct"], 4)
        else:
            merged[k] = h
    return list(merged.values())


def find_fund(month_dir: Path, names: list[str]) -> tuple[list[dict], str | None]:
    """Search every file in month_dir for the block titled with one of `names`."""
    best = (0, None, None, None)                 # score, label, rows, row index
    for path in sorted(month_dir.iterdir()):
        if path.name.startswith(".") or path.suffix.lower() not in (".xlsx", ".xls", ".xlsb", ".xlsm", ".zip"):
            continue
        for label, df in _sheets(path):
            rows = [[_txt(v) for v in r] for r in df.itertuples(index=False)]
            sheet_score = _title_score(label.split(":", 1)[-1], names)
            for i, cells in enumerate(rows):
                joined = " ".join(c for c in cells if c)
                if not joined or any(ISIN_RE.match(c.upper()) for c in cells):
                    continue
                low = joined.lower()
                if "fund" not in low and "scheme" not in low and i > 6:
                    continue
                s = _title_score(joined, names)
                if s > best[0]:
                    best = (s, label, rows, i)
            if sheet_score >= 95 and best[1] != label:      # sheet named after the fund, title not found inside
                if sheet_score > best[0]:
                    best = (sheet_score, label, rows, 0)
    score, label, rows, idx = best
    if score < 88 or rows is None:
        return [], None
    return _parse_block(rows, idx), label


def month_end(month: str) -> str:
    y, m = map(int, month.split("-"))
    return f"{month}-{calendar.monthrange(y, m)[1]:02d}"
