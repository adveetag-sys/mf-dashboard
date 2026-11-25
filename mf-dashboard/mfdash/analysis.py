"""Returns, month-on-month portfolio changes, and the rule-based 'factsheet study'."""
from __future__ import annotations

import re
from bisect import bisect_right
from datetime import date

import pandas as pd

CASH_PAT = re.compile(r"treps|cash|net receivable|net current asset|reverse repo|tri-party|tri party|"
                      r"clearing corporation|call money|margin money", re.I)
MOVE_PP = 0.25  # weight change (percentage points) that counts as a real add/trim


# ------------------------------------------------------------------ returns
def xirr(flows: list[tuple[date, float]]) -> float | None:
    """flows: (date, amount) with investments negative and current value positive."""
    flows = [(d, a) for d, a in flows if a]
    if len(flows) < 2 or not any(a > 0 for _, a in flows) or not any(a < 0 for _, a in flows):
        return None
    d0 = min(d for d, _ in flows)

    def npv(r):
        return sum(a / (1 + r) ** ((d - d0).days / 365.0) for d, a in flows)

    lo, hi = -0.99, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f = npv(mid)
        if abs(f) < 1e-6:
            break
        if f_lo * f < 0:
            hi = mid
        else:
            lo, f_lo = mid, f
    return mid


def nav_on(nav: list, d: str) -> float | None:
    """NAV on date d (or the last one before it)."""
    if not nav:
        return None
    i = bisect_right([r[0] for r in nav], d)
    return nav[i - 1][1] if i else None


def position(h, nav: list, txns: pd.DataFrame) -> dict:
    """Value, gain, XIRR for one fund."""
    t = txns[txns["fund"] == h.fund] if len(txns) else txns
    latest = nav[-1] if nav else None
    prev = nav[-2] if len(nav) > 1 else None
    units = h.units
    if units is None and len(t) and t["units"].notna().any():
        units = float(t["units"].sum())
    # if only amounts are given in transactions, buy units at that day's NAV
    if units is None and len(t) and nav:
        units = sum(a / nav_on(nav, str(d)) for d, a in zip(t["date"], t["amount"]) if a and nav_on(nav, str(d)))
    value = units * latest[1] if (units is not None and latest) else None
    invested = h.invested
    if invested is None and len(t) and t["amount"].notna().any():
        invested = float(t["amount"].sum())
    flows = []
    if len(t) and t["amount"].notna().any():
        flows = [(pd.Timestamp(d).date(), -float(a)) for d, a in zip(t["date"], t["amount"]) if pd.notna(a)]
    elif invested and h.date:
        flows = [(h.date, -invested)]
    if flows and value and latest:
        flows.append((date.fromisoformat(latest[0]), value))
    x = xirr(flows) if flows else None
    return {
        "fund": h.fund, "units": units, "nav": latest[1] if latest else None,
        "nav_date": latest[0] if latest else None, "value": value, "invested": invested,
        "gain": (value - invested) if (value is not None and invested) else None,
        "gain_pct": ((value / invested - 1) * 100) if (value and invested) else None,
        "xirr": x * 100 if x is not None else None,
        "day_change": (units * (latest[1] - prev[1])) if (units and latest and prev) else None,
        "since": str(h.date) if h.date else (str(t["date"].min()) if len(t) else None),
        "flows": flows,
    }


def monthly_values(h, nav: list, txns: pd.DataFrame) -> pd.DataFrame:
    """Month-end value of one fund. Uses transactions if given, else current units from the start date."""
    if not nav:
        return pd.DataFrame(columns=["month", "value", "invested"])
    s = pd.Series({pd.Timestamp(d): v for d, v in nav})
    me = s.resample("ME").last().dropna()
    t = txns[txns["fund"] == h.fund] if len(txns) else txns
    rows = []
    start = pd.Timestamp(h.date) if h.date else None
    if len(t):
        start = pd.Timestamp(t["date"].min())
    if start is None:
        start = me.index[-1] - pd.DateOffset(months=12)  # no date: show last 12 months at current units
    for m, v in me.items():
        if m < start.normalize() - pd.offsets.MonthEnd(0):
            continue
        if len(t) and t["units"].notna().any():
            u = float(t[pd.to_datetime(t["date"]) <= m]["units"].sum())
            inv = float(t[pd.to_datetime(t["date"]) <= m]["amount"].fillna(0).sum())
        else:
            u, inv = (h.units or 0), (h.invested or 0)
        rows.append({"month": m.strftime("%Y-%m"), "value": u * v, "invested": inv})
    return pd.DataFrame(rows)


def trailing_returns(nav: list) -> dict:
    """Point-to-point returns from your plan's own NAV. 1Y and under absolute %, 3Y/5Y annualised."""
    if len(nav) < 2:
        return {}
    last_d, last_v = date.fromisoformat(nav[-1][0]), nav[-1][1]
    out = {}
    for lab, days, ann in [("1D", 1, False), ("1M", 30, False), ("3M", 91, False), ("6M", 182, False),
                           ("1Y", 365, False), ("3Y", 365 * 3, True), ("5Y", 365 * 5, True)]:
        d0 = (last_d.toordinal() - days)
        if lab == "1D":
            v0 = nav[-2][1]
        else:
            if date.fromisoformat(nav[0][0]).toordinal() > d0 + 5:
                continue
            v0 = nav_on(nav, date.fromordinal(d0).isoformat())
        if not v0:
            continue
        r = last_v / v0
        out[lab] = ((r ** (365 / days) - 1) if ann else (r - 1)) * 100
    return out


def monthly_returns(nav: list, months: int = 12) -> dict:
    """{"YYYY-MM": % change in NAV over that calendar month}; current month is month-to-date."""
    if not nav:
        return {}
    s = pd.Series({pd.Timestamp(d): v for d, v in nav})
    me = s.resample("ME").last().dropna()
    ch = me.pct_change().dropna() * 100
    return {k.strftime("%Y-%m"): round(v, 2) for k, v in ch.tail(months).items()}


# ------------------------------------------------------------------ portfolio composition
def _key(x: dict) -> str:
    return (x.get("isin") or re.sub(r"[^a-z0-9]", "", x["name"].lower())[:40])


def is_cash(x: dict) -> bool:
    return bool(CASH_PAT.search(x.get("name", "")) or CASH_PAT.search(str(x.get("instrument", ""))))


def equity_like(x: dict) -> bool:
    ins = str(x.get("instrument", "")).lower()
    return not is_cash(x) and not any(w in ins for w in ("debt", "bond", "debenture", "g-sec", "gsec",
                                                         "treasury", "certificate of deposit", "commercial paper",
                                                         "t-bill", "sovereign"))


def summary(holdings: list[dict]) -> dict:
    if not holdings:
        return {}
    eq = sorted([h for h in holdings if not is_cash(h)], key=lambda h: -h["pct"])
    sec = sector_weights(holdings)
    return {
        "n_holdings": len(eq),
        "top10": round(sum(h["pct"] for h in eq[:10]), 2),
        "top_stock": eq[0]["name"] if eq else None,
        "top_stock_pct": eq[0]["pct"] if eq else None,
        "cash": round(sum(h["pct"] for h in holdings if is_cash(h)), 2),
        "top_sector": max(sec, key=sec.get) if sec else None,
        "top_sector_pct": round(max(sec.values()), 2) if sec else None,
    }


def sector_weights(holdings: list[dict]) -> dict:
    out = {}
    for h in holdings:
        if is_cash(h):
            continue
        s = h.get("sector") or "Others"
        out[s] = out.get(s, 0) + h["pct"]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def diff_holdings(prev: list[dict], cur: list[dict]) -> pd.DataFrame:
    p = {_key(x): x for x in prev}
    c = {_key(x): x for x in cur}
    rows = []
    for k in set(p) | set(c):
        a, b = p.get(k), c.get(k)
        w0, w1 = (a["pct"] if a else 0.0), (b["pct"] if b else 0.0)
        ref = b or a
        if a is None:
            action = "New entry"
        elif b is None:
            action = "Exited"
        elif w1 - w0 >= MOVE_PP:
            action = "Increased"
        elif w0 - w1 >= MOVE_PP:
            action = "Reduced"
        else:
            action = "Unchanged"
        rows.append({"Stock": ref["name"], "Sector": ref.get("sector"), "Action": action,
                     "Prev %": round(w0, 2), "Now %": round(w1, 2), "Change (pp)": round(w1 - w0, 2),
                     "cash": is_cash(ref)})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    order = {"New entry": 0, "Exited": 1, "Increased": 2, "Reduced": 3, "Unchanged": 4}
    df["o"] = df["Action"].map(order)
    df["a"] = df["Change (pp)"].abs()
    return df.sort_values(["o", "a"], ascending=[True, False]).drop(columns=["o", "a"]).reset_index(drop=True)


# ------------------------------------------------------------------ factsheet study
def _fmt_list(names, n=3):
    names = list(names)
    s = ", ".join(names[:n])
    return s + (f" and {len(names) - n} more" if len(names) > n else "")


def study(snaps: dict, fund_name: str) -> list[dict]:
    """Plain-English notes from the latest factsheet vs the month before.
    Each note: {"level": "info|watch|good", "text": ...}"""
    notes = []
    if not snaps:
        return [{"level": "watch", "text": "No factsheet data yet. It will appear after the first update run."}]
    months = list(snaps)
    cur = snaps[months[-1]]
    prev = snaps[months[-2]] if len(months) > 1 else None
    m, pm = cur.get("metrics", {}), (prev or {}).get("metrics", {})
    cs, ps = summary(cur.get("holdings", [])), summary((prev or {}).get("holdings", []))

    def add(level, text):
        notes.append({"level": level, "text": text})

    if cs:
        add("about", f"Holds {cs['n_holdings']} positions; top 10 make up {cs['top10']:.1f}% of the fund. "
                    f"Largest: {cs['top_stock']} ({cs['top_stock_pct']:.1f}%). "
                    f"Biggest sector: {cs['top_sector']} ({cs['top_sector_pct']:.1f}%). Cash and equivalents: {cs['cash']:.1f}%.")
    if prev and cur.get("holdings") and prev.get("holdings"):
        d = diff_holdings(prev["holdings"], cur["holdings"])
        d = d[~d["cash"]]
        new, ex = d[d.Action == "New entry"], d[d.Action == "Exited"]
        inc, red = d[d.Action == "Increased"], d[d.Action == "Reduced"]
        if len(new):
            add("info", f"New buys this month: {_fmt_list(f"{r['Stock']} ({r['Now %']:.2f}%)" for _, r in new.iterrows())}.")
        if len(ex):
            add("info", f"Fully sold: {_fmt_list(f"{r['Stock']} (was {r['Prev %']:.2f}%)" for _, r in ex.iterrows())}.")
        if len(inc):
            top = inc.sort_values("Change (pp)", ascending=False)
            add("info", f"Added most to: {_fmt_list(f"{r['Stock']} (+{r['Change (pp)']:.2f} pp)" for _, r in top.iterrows())}.")
        if len(red):
            top = red.sort_values("Change (pp)")
            add("info", f"Trimmed most: {_fmt_list(f"{r['Stock']} ({r['Change (pp)']:.2f} pp)" for _, r in top.iterrows())}.")
        if not (len(new) or len(ex) or len(inc) or len(red)):
            add("good", "Portfolio barely changed from last month (low churn).")
        churn = d["Change (pp)"].abs().sum() / 2
        if churn >= 15:
            add("watch", f"High activity: about {churn:.0f}% of the portfolio was reshuffled in one month.")
        if ps:
            if abs(cs["top10"] - ps["top10"]) >= 2:
                add("watch" if cs["top10"] > ps["top10"] else "info",
                    f"Top-10 concentration {'rose' if cs['top10'] > ps['top10'] else 'fell'} from "
                    f"{ps['top10']:.1f}% to {cs['top10']:.1f}%.")
            if abs(cs["cash"] - ps["cash"]) >= 1:
                add("info", f"Cash moved from {ps['cash']:.1f}% to {cs['cash']:.1f}%"
                            f"{' (fund manager may be cautious)' if cs['cash'] > ps['cash'] + 2 else ''}.")
        sw0, sw1 = sector_weights(prev["holdings"]), sector_weights(cur["holdings"])
        shifts = {s: sw1.get(s, 0) - sw0.get(s, 0) for s in set(sw0) | set(sw1)}
        big = sorted([kv for kv in shifts.items() if abs(kv[1]) >= 1.5], key=lambda kv: -abs(kv[1]))
        if big:
            add("info", "Sector shifts: " + ", ".join(f"{s} {v:+.1f} pp" for s, v in big[:4]) + ".")
    elif not prev:
        add("info", "This is the first month on record; month-on-month changes start from next month's disclosure.")

    if cs and cs["top_stock_pct"] and cs["top_stock_pct"] >= 9:
        over = cs["top_stock_pct"] >= 10
        add("watch", f"{cs['top_stock']} is {cs['top_stock_pct']:.1f}% of the fund, "
                     + ("above the usual 10% single-stock cap (possible after a price rise, or in index/sector funds)."
                        if over else "close to the usual 10% single-stock cap."))

    er, per = m.get("expense_ratio"), pm.get("expense_ratio")
    if er is not None:
        if per is not None and abs(er - per) >= 0.02:
            add("good" if er < per else "watch", f"Expense ratio changed from {per:.2f}% to {er:.2f}%.")
        elif er >= 1.5:
            add("watch", f"Expense ratio is {er:.2f}%, which is high (check you're in the Direct plan).")
    aum, paum = m.get("aum_cr"), pm.get("aum_cr")
    if aum and paum and abs(aum / paum - 1) >= 0.10:
        add("info", f"Fund size {'grew' if aum > paum else 'shrank'} {abs(aum / paum - 1) * 100:.0f}% "
                    f"to ₹{aum:,.0f} Cr in a month.")
    sub = str(m.get("sub_category") or m.get("category") or "").lower()
    if aum and any(w in sub for w in ("small", "micro")) and aum > 30000:
        add("watch", f"Small-cap fund with ₹{aum:,.0f} Cr AUM; very large size can make it harder to buy and sell small stocks.")
    fm, pfm = m.get("fund_manager"), pm.get("fund_manager")
    if fm and pfm and set(map(str.strip, str(fm).split(";"))) != set(map(str.strip, str(pfm).split(";"))):
        add("watch", f"Fund manager change: was {pfm}, now {fm}.")
    pe, ppe = m.get("pe"), pm.get("pe")
    if pe and ppe and abs(pe / ppe - 1) >= 0.10:
        add("info", f"Portfolio P/E moved from {ppe:.1f} to {pe:.1f}.")
    for yrs in ("1y", "3y", "5y"):
        r, c = m.get(f"return_{yrs}"), m.get(f"cat_return_{yrs}")
        if r is not None and c is not None:
            diff = r - c
            if abs(diff) >= 1:
                add("good" if diff > 0 else "watch",
                    f"{yrs.upper()} return {r:.1f}% vs category {c:.1f}% ({'ahead' if diff > 0 else 'behind'} by {abs(diff):.1f} pp).")
    if cur.get("source") == "kuvera" and not cur.get("holdings"):
        add("watch", "Stock-level holdings unavailable this month (Groww data didn't load); only factsheet numbers shown.")
    return notes


# ------------------------------------------------------------------ across your funds
def look_through(funds: list[dict]) -> pd.DataFrame:
    """funds: [{"fund", "value", "holdings"}] -> your rupee exposure to each stock across all funds."""
    rows = []
    for f in funds:
        if not f.get("value"):
            continue
        for h in f.get("holdings", []):
            rows.append({"Stock": h["name"], "Sector": "Cash" if is_cash(h) else (h.get("sector") or "Others"),
                         "Fund": f["fund"], "₹": f["value"] * h["pct"] / 100, "key": _key(h)})
    if not rows:
        return pd.DataFrame(columns=["Stock", "Sector", "₹", "Funds", "In # funds"])
    df = pd.DataFrame(rows)
    g = df.groupby("key").agg(Stock=("Stock", "first"), Sector=("Sector", "first"), **{"₹": ("₹", "sum")},
                              Funds=("Fund", lambda s: ", ".join(sorted(set(s)))),
                              **{"In # funds": ("Fund", "nunique")})
    return g.sort_values("₹", ascending=False).reset_index(drop=True)


def overlap(a: list[dict], b: list[dict]) -> float:
    wa = {_key(x): x["pct"] for x in a if not is_cash(x)}
    wb = {_key(x): x["pct"] for x in b if not is_cash(x)}
    return round(sum(min(wa[k], wb[k]) for k in set(wa) & set(wb)), 1)
