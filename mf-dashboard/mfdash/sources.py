"""Free public data sources.

mfapi.in      -> daily NAV history (AMFI data), scheme search.
Groww         -> each fund's latest portfolio (stock-wise holdings) + factsheet numbers.
                 This is the JSON Groww's own website uses; it is unofficial, so every
                 field is read defensively and missing fields are reported, not fatal.
Kuvera (via mf.captnemo.in) -> backup for expense ratio, AUM, fund manager, returns.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime

import requests

UA = {"User-Agent": "Mozilla/5.0 (personal mf-dashboard; +github actions)",
      "Accept": "application/json"}
_session = requests.Session()
_session.headers.update(UA)


def get_json(url: str, tries: int = 3, pause: float = 2.0, params: dict | None = None):
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, timeout=30, params=params)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:  # network blips, 429s
            last = e
            time.sleep(pause * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


# ---------------------------------------------------------------- mfapi.in
MFAPI = "https://api.mfapi.in/mf"


def mfapi_all_schemes() -> list[dict]:
    return get_json(MFAPI) or []


def mfapi_search(q: str) -> list[dict]:
    return get_json(f"{MFAPI}/search", params={"q": q}) or []


def mfapi_nav(code: str) -> dict:
    """-> {"meta": {...}, "nav": [(date, float), ...] oldest first}"""
    j = get_json(f"{MFAPI}/{code}") or {}
    rows = []
    for d in j.get("data", []):
        try:
            rows.append((datetime.strptime(d["date"], "%d-%m-%Y").date().isoformat(), float(d["nav"])))
        except (KeyError, ValueError):
            continue
    rows.sort()
    return {"meta": j.get("meta", {}), "nav": rows}


def nav_by_isin(isin: str) -> dict:
    """Backup NAV source keyed by ISIN (mf.captnemo.in, built from AMFI data)."""
    j = get_json(f"https://mf.captnemo.in/nav/{isin}", tries=2) or {}
    rows = []
    for d, v in j.get("historical_nav", []):
        try:
            rows.append((str(d)[:10], float(v)))
        except (TypeError, ValueError):
            continue
    rows.sort()
    return {"meta": {"scheme_name": j.get("name"), "isin_growth": j.get("ISIN")}, "nav": rows}


# ---------------------------------------------------------------- Nifty 50 (price index)
def nifty50() -> list:
    """Daily Nifty 50 closes [(date, close)], oldest first. Yahoo Finance chart data; yfinance as backup."""
    for host in ("query1", "query2"):
        try:
            j = get_json(f"https://{host}.finance.yahoo.com/v8/finance/chart/%5ENSEI",
                         tries=2, params={"range": "10y", "interval": "1d"})
            r = j["chart"]["result"][0]
            ts, cl = r["timestamp"], r["indicators"]["quote"][0]["close"]
            rows = {}
            for t, c in zip(ts, cl):
                if c:
                    rows[datetime.utcfromtimestamp(t + 19800).date().isoformat()] = round(float(c), 2)  # IST date
            if rows:
                return sorted(rows.items())
        except Exception:
            continue
    try:
        import yfinance as yf
        h = yf.Ticker("^NSEI").history(period="10y", interval="1d")
        return [(d.date().isoformat(), round(float(c), 2)) for d, c in h["Close"].dropna().items()]
    except Exception as e:
        raise RuntimeError(f"Nifty 50 fetch failed: {e}")


# ---------------------------------------------------------------- Groww
GROWW = "https://groww.in/v1/api/data/mf/web"


def slugify(name: str) -> str:
    s = name.lower().replace("&", "and")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(option|plan|scheme)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return re.sub(r"-+", "-", s)


def groww_slug_from_link(link: str) -> str:
    return link.rstrip("/").split("/")[-1].split("?")[0]


def groww_search(q: str) -> list[dict]:
    """Returns [{"search_id", "title"}...]. Tries the search endpoints Groww's site uses."""
    urls = [
        ("https://groww.in/v1/api/search/v1/entity",
         {"app": "false", "entity_type": "scheme", "page": 0, "q": q, "size": 10}),
        ("https://groww.in/v1/api/search/v3/query/global/st_query",
         {"entity_type": "scheme", "page": 0, "query": q, "size": 10}),
    ]
    for u, p in urls:
        try:
            j = get_json(u, tries=2, params=p)
        except RuntimeError:
            continue
        if not j:
            continue
        items = j.get("content") or j.get("data", {}).get("content") or j.get("results") or []
        out = []
        for it in items:
            sid = it.get("search_id") or it.get("searchId")
            if sid and (it.get("entity_type", "scheme").lower() in ("scheme", "mf", "mutual_fund")):
                out.append({"search_id": sid, "title": it.get("title") or it.get("name") or sid})
        if out:
            return out
    return []


def groww_scheme(slug: str) -> dict | None:
    for v in ("v4", "v5", "v3"):
        try:
            j = get_json(f"{GROWW}/{v}/scheme/search/{slug}", tries=2)
        except RuntimeError:
            continue
        if j and isinstance(j, dict) and (j.get("scheme_name") or j.get("holdings")):
            return j
    return None


def groww_portfolio_stats(amfi_code: str) -> dict | None:
    try:
        return get_json(f"{GROWW}/v1/scheme/portfolio/{amfi_code}/stats", tries=2)
    except RuntimeError:
        return None


# ---------------------------------------------------------------- Kuvera (captnemo mirror)
def kuvera(isin: str) -> dict | None:
    try:
        j = get_json(f"https://mf.captnemo.in/kuvera/{isin}", tries=2)
    except RuntimeError:
        return None
    if isinstance(j, list):
        return j[0] if j else None
    return j


# ---------------------------------------------------------------- normalising
def _first(d: dict, keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d[k] not in (None, "", "NA", "-"):
            return d[k]
    return default


def _f(x):
    try:
        return float(str(x).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _iso(x):
    if not x:
        return None
    s = str(x)[:10]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    try:  # epoch millis
        return date.fromtimestamp(int(x) / 1000).isoformat()
    except Exception:
        return None


def _deep_find_number(d, names):
    """search nested dict/list for first key in names with a numeric value"""
    if isinstance(d, dict):
        for k, v in d.items():
            if k.lower() in names and _f(v) is not None:
                return _f(v)
        for v in d.values():
            r = _deep_find_number(v, names)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _deep_find_number(v, names)
            if r is not None:
                return r
    return None


def normalise_groww(j: dict, stats: dict | None = None) -> dict:
    holds_raw = _first(j, ["holdings", "portfolio_holdings", "portfolio", "holding"], []) or []
    holdings, pdates = [], []
    for h in holds_raw if isinstance(holds_raw, list) else []:
        name = _first(h, ["company_name", "name", "stock_name", "security_name", "instrument_name"])
        pct = _f(_first(h, ["corpus_per", "percentage", "weight", "holding_percentage", "per", "corpus_percentage"]))
        if not name or pct is None:
            continue
        pd_ = _iso(_first(h, ["portfolio_date", "as_on_date", "date"]))
        if pd_:
            pdates.append(pd_)
        holdings.append({
            "name": str(name).strip(),
            "isin": _first(h, ["isin", "stock_isin", "isin_code"]),
            "sector": _first(h, ["sector_name", "sector", "industry"], "Others"),
            "instrument": _first(h, ["instrument_name", "nature_name", "asset_type", "instrument"], ""),
            "rating": _first(h, ["rating", "credit_rating"]),
            "pct": round(pct, 4),
            "market_value": _f(_first(h, ["market_value", "value"])),
        })
    rs = _first(j, ["return_stats", "returns", "return_stat"], {})
    if isinstance(rs, list):
        rs = rs[0] if rs else {}
    mgr_details = _first(j, ["fund_manager_details", "fund_managers"], []) or []
    mgrs = [m.get("person_name") or m.get("name") for m in mgr_details if isinstance(m, dict)]
    mgrs = [m for m in mgrs if m]
    stats = stats or {}
    metrics = {
        "scheme_name": _first(j, ["scheme_name", "fund_name", "name"]),
        "fund_house": _first(j, ["fund_house", "amc", "amc_name"]),
        "category": _first(j, ["category", "fund_category"]),
        "sub_category": _first(j, ["sub_category", "sub_category_name", "fund_sub_category"]),
        "benchmark": _first(j, ["benchmark_name", "benchmark"]),
        "risk": _first(j, ["nfo_risk", "risk", "risk_rating"]),
        "aum_cr": _f(_first(j, ["aum", "fund_size", "aum_cr"])),
        "expense_ratio": _f(_first(j, ["expense_ratio", "ter"])),
        "exit_load": _first(j, ["exit_load"]),
        "fund_manager": "; ".join(mgrs) or _first(j, ["fund_manager", "fund_managers_name"]),
        "launch_date": _iso(_first(j, ["launch_date", "inception_date"])),
        "return_1y": _f(_first(rs, ["return1y", "return_1y", "1y"])),
        "return_3y": _f(_first(rs, ["return3y", "return_3y", "3y"])),
        "return_5y": _f(_first(rs, ["return5y", "return_5y", "5y"])),
        "cat_return_1y": _f(_first(rs, ["cat_return1y", "category_return_1y"])),
        "cat_return_3y": _f(_first(rs, ["cat_return3y", "category_return_3y"])),
        "cat_return_5y": _f(_first(rs, ["cat_return5y", "category_return_5y"])),
        "pe": _deep_find_number(stats, {"pe", "pe_ratio", "price_to_earnings", "p_e"}) or
              _deep_find_number(j.get("stats") or {}, {"pe", "pe_ratio"}),
        "pb": _deep_find_number(stats, {"pb", "pb_ratio", "price_to_book", "p_b"}) or
              _deep_find_number(j.get("stats") or {}, {"pb", "pb_ratio"}),
        "turnover": _deep_find_number(stats, {"turnover", "portfolio_turnover", "turnover_ratio"}),
        "isin": _first(j, ["isin", "direct_isin"]),
        "amfi_code": str(_first(j, ["scheme_code", "amfi_code"]) or "") or None,
    }
    portfolio_date = max(pdates) if pdates else _iso(_first(j, ["portfolio_date", "holdings_as_on"]))
    return {"source": "groww", "portfolio_date": portfolio_date, "metrics": metrics, "holdings": holdings,
            "raw_keys": sorted(j.keys())}


def normalise_kuvera(k: dict) -> dict:
    ret = k.get("returns") or {}
    return {
        "scheme_name": k.get("name"),
        "category": k.get("category"),
        "sub_category": k.get("fund_category"),
        "risk": k.get("crisil_rating"),
        "aum_cr": _f(k.get("aum")),
        "expense_ratio": _f(k.get("expense_ratio")),
        "fund_manager": k.get("fund_manager"),
        "launch_date": k.get("start_date"),
        "return_1y": _f(ret.get("year_1")),
        "return_3y": _f(ret.get("year_3")),
        "return_5y": _f(ret.get("year_5")),
        "turnover": _f(k.get("portfolio_turnover")),
        "volatility": _f(k.get("volatility")),
    }
