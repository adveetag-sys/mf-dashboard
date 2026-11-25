"""Read your holdings Excel, whatever layout it has.

Works with a hand-made sheet or with an export from Groww / Zerodha Coin /
Kuvera / INDmoney / CAMS. It looks for the header row and maps columns by
name, so the exact column titles don't matter much.

Recognised columns (any of the listed names, case-insensitive):
  fund      : fund, scheme, scheme name, fund name, name
  units     : units, unit balance, balance units, quantity, qty
  invested  : invested, invested value, invested amount, amount, cost, cost value, purchase value
  date      : date, purchase date, start date, invested on, first investment
  code      : amfi, amfi code, scheme code, code
  isin      : isin
  groww     : groww, groww link, groww url, groww slug

Optional second sheet "Transactions" (or "Txns"): date, fund, amount, units, type.
Use positive amount/units for purchases and negative (or type = sell/redeem) for redemptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

ALIASES = {
    "fund": ["fund", "scheme", "scheme name", "fund name", "name", "mutual fund", "scheme_name"],
    "units": ["units", "unit balance", "balance units", "quantity", "qty", "closing units", "units held"],
    "invested": ["invested", "invested value", "invested amount", "amount", "cost", "cost value",
                 "purchase value", "investment", "amount invested", "total invested", "invested amt"],
    "date": ["date", "purchase date", "start date", "invested on", "first investment", "buy date"],
    "code": ["amfi", "amfi code", "scheme code", "code", "amfi scheme code"],
    "isin": ["isin", "isin code"],
    "groww": ["groww", "groww link", "groww url", "groww slug"],
    "type": ["type", "transaction type", "txn type", "action"],
}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(s).lower()).strip()


def _match(col: str, key: str) -> bool:
    c = _norm(col)
    c = re.sub(r"\s+", " ", c)
    for a in ALIASES[key]:
        if c == a or c.startswith(a + " ") or c.endswith(" " + a):
            return True
    return False


def _find_header(raw: pd.DataFrame, need=("fund",)) -> int | None:
    for i in range(min(len(raw), 40)):
        row = [str(x) for x in raw.iloc[i].tolist() if pd.notna(x)]
        if all(any(_match(c, k) for c in row) for k in need):
            return i
    return None


def _map_columns(df: pd.DataFrame, keys) -> dict:
    out = {}
    for k in keys:
        for c in df.columns:
            if c in out.values():
                continue
            if _match(c, k):
                out[k] = c
                break
    return out


def _num(x):
    if pd.isna(x):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[^0-9.\-]", "", str(x))
    try:
        return float(s) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _date(x):
    if pd.isna(x) or x == "":
        return None
    try:
        return pd.to_datetime(x, dayfirst=True).date()
    except Exception:
        return None


@dataclass
class Holding:
    fund: str
    units: float | None = None
    invested: float | None = None
    date: object = None
    code: str | None = None
    isin: str | None = None
    groww: str | None = None


@dataclass
class Portfolio:
    holdings: list[Holding]
    transactions: pd.DataFrame = field(default_factory=pd.DataFrame)  # date, fund, amount, units


def _read_sheet(path: Path, sheet) -> pd.DataFrame | None:
    raw = pd.read_excel(path, sheet_name=sheet, header=None) if path.suffix.lower() != ".csv" \
        else pd.read_csv(path, header=None)
    h = _find_header(raw)
    if h is None:
        return None
    df = raw.iloc[h + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[h].tolist()]
    df = df.dropna(how="all")
    return df


def load_portfolio(path: str | Path) -> Portfolio:
    path = Path(path)
    sheets = [None] if path.suffix.lower() == ".csv" else pd.ExcelFile(path).sheet_names
    txn_sheet = next((s for s in sheets if s and _norm(s) in ("transactions", "txns", "transaction")), None)
    hold_sheet = next((s for s in sheets if s and _norm(s) in ("holdings", "holding", "portfolio")), None)
    if hold_sheet is None:
        hold_sheet = next((s for s in sheets if s != txn_sheet), sheets[0])

    df = _read_sheet(path, hold_sheet if hold_sheet is not None else 0)
    if df is None:
        raise ValueError(f"Couldn't find a header row with a fund/scheme name column in {path.name}")
    cols = _map_columns(df, ["fund", "units", "invested", "date", "code", "isin", "groww"])

    holdings: dict[str, Holding] = {}
    for _, r in df.iterrows():
        name = r.get(cols["fund"])
        if pd.isna(name) or not str(name).strip() or _norm(name) in ("total", "grand total"):
            continue
        name = str(name).strip()
        h = Holding(
            fund=name,
            units=_num(r[cols["units"]]) if "units" in cols else None,
            invested=_num(r[cols["invested"]]) if "invested" in cols else None,
            date=_date(r[cols["date"]]) if "date" in cols else None,
            code=(str(int(_num(r[cols["code"]]))) if "code" in cols and _num(r[cols["code"]]) else None),
            isin=(str(r[cols["isin"]]).strip().upper() if "isin" in cols and pd.notna(r[cols["isin"]]) else None),
            groww=(str(r[cols["groww"]]).strip() if "groww" in cols and pd.notna(r[cols["groww"]]) else None),
        )
        # same fund in several folios -> add up
        if name in holdings:
            old = holdings[name]
            old.units = (old.units or 0) + (h.units or 0)
            old.invested = (old.invested or 0) + (h.invested or 0)
            old.date = min([d for d in (old.date, h.date) if d], default=None)
        else:
            holdings[name] = h

    txns = pd.DataFrame(columns=["date", "fund", "amount", "units"])
    if txn_sheet:
        t = _read_sheet(path, txn_sheet)
        if t is not None:
            tc = _map_columns(t, ["date", "fund", "invested", "units", "type"])
            rows = []
            for _, r in t.iterrows():
                d, f = _date(r.get(tc.get("date"))), r.get(tc.get("fund"))
                if not d or pd.isna(f):
                    continue
                amt = _num(r[tc["invested"]]) if "invested" in tc else None
                u = _num(r[tc["units"]]) if "units" in tc else None
                sell = "type" in tc and re.search(r"sell|redeem|redemption|switch out|withdraw",
                                                  str(r[tc["type"]]).lower())
                if sell:
                    amt = -abs(amt) if amt else amt
                    u = -abs(u) if u else u
                rows.append({"date": d, "fund": str(f).strip(), "amount": amt, "units": u})
            txns = pd.DataFrame(rows, columns=["date", "fund", "amount", "units"])
            # funds only in transactions -> add holdings
            for f, g in txns.groupby("fund"):
                if f not in holdings:
                    holdings[f] = Holding(fund=f)
                h = holdings[f]
                if h.units is None and g["units"].notna().any():
                    h.units = float(g["units"].sum())
                if h.invested is None and g["amount"].notna().any():
                    h.invested = float(g["amount"].sum())
                if h.date is None:
                    h.date = g["date"].min()

    return Portfolio(holdings=list(holdings.values()), transactions=txns)
