"""Mutual fund dashboard (Streamlit). Data is refreshed daily by GitHub Actions."""
from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mfdash import analysis as A
from mfdash import store
from mfdash.holdings import load_portfolio

st.set_page_config(page_title="My Mutual Funds", page_icon="📈", layout="wide")

# ---------------------------------------------------------------- theme & palette
try:
    DARK = st.context.theme.type != "light"     # None on first load -> dark steps (they also read on light)
except Exception:
    DARK = True

if DARK:
    SERIES = ["#338ced", "#e76624", "#00a888", "#d28e05", "#e24d8b", "#0aac23", "#9887ef", "#eb6263"]
    POS, NEG = "#338ced", "#eb6263"
    GRID = "rgba(255,255,255,0.08)"
else:
    SERIES = ["#257de0", "#f47230", "#00b894", "#f2a600", "#ee7fac", "#008a17", "#6a4ce2", "#ec4647"]
    POS, NEG = "#257de0", "#dc3c3d"
    GRID = "rgba(0,0,0,0.07)"
STATUS = {"good": "#0aac23", "watch": "#f18a59", "info": "#8c8c8c"}
MID = "#8f8f8f"   # neutral gray midpoint that reads on both light and dark pages
DIVERGING = [[0, "#b8292a"], [0.25, "#e0716f"], [0.5, MID], [0.75, "#519ced"], [1, "#1757ac"]]

st.markdown("""
<style>
.block-container {padding-top: 2.2rem; max-width: 1400px;}
h1 {font-size: 1.9rem !important; margin-bottom: 0 !important;}
.kpis {display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 14px 0 6px;}
.kpi {border: 1px solid rgba(128,128,128,.22); border-radius: 10px; padding: 12px 14px;
      background: rgba(128,128,128,.05);}
.kpi .l {font-size: .78rem; opacity: .7; margin-bottom: 4px;}
.kpi .v {font-size: 1.35rem; font-weight: 600; line-height: 1.2;}
.kpi .s {font-size: .78rem; margin-top: 3px; opacity: .85;}
.up {color: %(pos)s;} .down {color: %(neg)s;}
.chips {display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0 12px;}
.chip {font-size: .76rem; padding: 2px 9px; border-radius: 999px; border: 1px solid rgba(128,128,128,.3);
       opacity: .9;}
.fund-h {font-size: 1.25rem; font-weight: 600; margin: 4px 0 2px;}
.notes-h {font-size: .8rem; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; opacity: .65;
          margin: 14px 0 6px;}
.note {display: flex; gap: 10px; align-items: flex-start; padding: 8px 10px; margin-bottom: 6px;
       border-radius: 8px; background: rgba(128,128,128,.06); border-left: 3px solid var(--c); font-size: .9rem;}
.note .tag {font-size: .68rem; font-weight: 700; color: var(--c); min-width: 44px; padding-top: 2px;
            text-transform: uppercase;}
.muted {opacity: .65; font-size: .82rem;}
</style>
""".replace("%(pos)s", POS).replace("%(neg)s", NEG), unsafe_allow_html=True)


# ---------------------------------------------------------------- helpers
def inr(x, dec=0):
    if x is None or pd.isna(x):
        return "–"
    neg, x = x < 0, abs(x)
    whole, _, frac = f"{x:,.{dec}f}".partition(".")
    whole = whole.replace(",", "")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = head + "," + tail
    return ("-" if neg else "") + "₹" + whole + (("." + frac) if frac else "")


def pct(x, dec=1, sign=False):
    if x is None or pd.isna(x):
        return "–"
    return f"{x:+.{dec}f}%" if sign else f"{x:.{dec}f}%"


def short(name: str) -> str:
    s = re.sub(r"\(.*?\)", "", name)
    s = re.sub(r"[-–]\s*(regular|direct)(\s*plan)?\s*", " ", s, flags=re.I)
    s = re.sub(r"\b(regular plan|direct plan|regular|direct|growth option|growth|option|plan|scheme)\b", " ", s, flags=re.I)
    s = re.sub(r"\bfund\b", " ", s, flags=re.I)
    s = re.sub(r"[-–]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def kpis(items):
    """items: [(label, value, sub, direction)] direction in {None, 'up', 'down'}"""
    cells = []
    for lab, val, sub, d in items:
        cls = {"up": "up", "down": "down"}.get(d, "")
        arrow = {"up": "▲ ", "down": "▼ "}.get(d, "")
        cells.append(f"<div class='kpi'><div class='l'>{html.escape(lab)}</div><div class='v'>{html.escape(val)}</div>"
                     + (f"<div class='s {cls}'>{arrow}{html.escape(sub)}</div>" if sub else "") + "</div>")
    st.markdown(f"<div class='kpis'>{''.join(cells)}</div>", unsafe_allow_html=True)


def direction(x):
    if x is None or pd.isna(x) or abs(x) < 1e-9:
        return None
    return "up" if x > 0 else "down"


def style(fig, h=380, legend=False):
    fig.update_layout(height=h, margin=dict(l=0, r=10, t=10, b=0), showlegend=legend,
                      font=dict(size=12), hoverlabel=dict(font_size=12),
                      legend=dict(orientation="h", y=1.08, x=0, title=None))
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def bars(labels, values, fmt="{:+.1f}%", h=None, hover_name="Return"):
    """Horizontal ranked bar, blue for positive and red for negative, value labels at the end."""
    d = pd.DataFrame({"l": labels, "v": values}).dropna().sort_values("v")
    fig = go.Figure(go.Bar(
        x=d["v"], y=d["l"], orientation="h", marker=dict(color=[POS if v >= 0 else NEG for v in d["v"]],
                                                         cornerradius=4),
        text=[fmt.format(v) for v in d["v"]], textposition="outside", cliponaxis=False,
        hovertemplate="%{y}<br>" + hover_name + ": %{x:.2f}<extra></extra>"))
    style(fig, h or max(160, 30 * len(d) + 40))
    lo, hi = min(0.0, float(d["v"].min())), max(0.0, float(d["v"].max()))
    pad = (hi - lo) * 0.16 or 1
    fig.update_xaxes(range=[lo - (pad if lo < 0 else 0), hi + pad])
    fig.update_xaxes(showgrid=True, gridcolor=GRID, zeroline=True, zerolinecolor="rgba(128,128,128,.5)",
                     ticksuffix="%" if "%" in fmt else "")
    fig.update_yaxes(showgrid=False)
    fig.update_traces(width=0.62 if len(d) > 2 else 0.35)
    return fig


def styled(rows, fmt: dict):
    """(DataFrame, column_config): numbers pre-formatted as right-aligned text, blanks shown as '–'."""
    df = pd.DataFrame(rows)
    cfg = {}
    for c in df.columns:
        if c in fmt:
            df[c] = [("–" if v is None or (isinstance(v, float) and pd.isna(v)) else fmt[c].format(v))
                     for v in df[c]]
            cfg[c] = st.column_config.TextColumn(alignment="right")
        else:
            df[c] = ["–" if v is None or (isinstance(v, float) and pd.isna(v)) else v for v in df[c]]
    return df, cfg


def table(rows):
    """DataFrame with missing numbers shown blank instead of 'None'."""
    df = pd.DataFrame(rows)
    for c in df.columns:
        if df[c].map(lambda v: v is None or isinstance(v, (int, float))).all() and df[c].notna().any():
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def show(fig):
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})


# ---------------------------------------------------------------- data
@st.cache_data(ttl=600)
def load_all(stamp):
    hf = store.find_holdings_file()
    if not hf:
        return None
    pf = load_portfolio(hf)
    fmap = store.read_json(store.FUND_MAP, {})
    funds = []
    for h in pf.holdings:
        m = fmap.get(h.fund, {})
        code = m.get("code") or (f"isin-{m['isin']}" if m.get("nav_by_isin") and m.get("isin") else None)
        nav = store.load_nav(code) if code else []
        snaps = store.load_snapshots(code) if code else {}
        pos = A.position(h, nav, pf.transactions)
        latest = snaps[list(snaps)[-1]] if snaps else {}
        met = latest.get("metrics", {})
        funds.append({**pos, "h": h, "code": code, "map": m, "nav_hist": nav, "snaps": snaps,
                      "short": short(h.fund),
                      "tr": A.trailing_returns(nav), "mret": A.monthly_returns(nav, 12),
                      "metrics": met, "holdings": latest.get("holdings", []),
                      "portfolio_date": latest.get("portfolio_date"),
                      "category": met.get("sub_category") or met.get("category") or "Not fetched yet"})
    return pf, funds, store.read_json(store.STATUS, {})


_hf = store.find_holdings_file()
stamp = (max([p.stat().st_mtime for p in store.DATA.rglob("*.json")] + [0]),
         _hf.stat().st_mtime if _hf else 0, str(_hf))
loaded = load_all(stamp)
if loaded is None:
    st.error("Put your holdings Excel in the repo as `holdings.xlsx` and the dashboard will build itself.")
    st.stop()
pf, funds, status = loaded

# funds whose NAV stopped updating are shown but kept out of comparisons
last_dates = [f["nav_date"] for f in funds if f["nav_date"]]
latest_day = max(last_dates) if last_dates else None
for f in funds:
    f["stale"] = (not f["nav_date"]) or (latest_day and f["nav_date"] <
                                         (date.fromisoformat(latest_day) - timedelta(days=10)).isoformat())
live = [f for f in funds if not f["stale"]]

tot_val = sum(f["value"] or 0 for f in live)
tot_inv = sum(f["invested"] or 0 for f in live if f["value"])
tot_day = sum(f["day_change"] or 0 for f in live)
HAS_UNITS = tot_val > 0
for f in funds:
    f["weight_value"] = (f["value"] or 0) if HAS_UNITS else (100 / max(len(live), 1) if not f["stale"] else 0)


def avg(xs):
    xs = [x for x in xs if x is not None and not pd.isna(x)]
    return sum(xs) / len(xs) if xs else None


# ---------------------------------------------------------------- header
st.title("My Mutual Funds")
upd = status.get("updated_at")
cap = "Refreshes automatically every morning"
if upd:
    t = datetime.fromisoformat(upd)
    cap += f" · last run {t.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y, %I:%M %p')} IST"
if latest_day:
    cap += f" · NAVs as of {date.fromisoformat(latest_day).strftime('%d %b %Y')}"
st.markdown(f"<div class='muted'>{cap}</div>", unsafe_allow_html=True)

if HAS_UNITS:
    tot_x = A.xirr([fl for f in live for fl in f["flows"]])
    g = tot_val - tot_inv
    kpis([("Current value", inr(tot_val), None, None),
          ("Invested", inr(tot_inv), None, None),
          ("Gain", inr(g), pct(g / tot_inv * 100 if tot_inv else None, 1, True), direction(g)),
          ("XIRR", pct(tot_x * 100 if tot_x is not None else None), None, None),
          ("Last day", inr(tot_day), pct(tot_day / (tot_val - tot_day) * 100 if tot_val else None, 2, True),
           direction(tot_day))])
else:
    cur_m = max([m for f in live for m in f["mret"]], default=None)
    d1 = avg([f["tr"].get("1D") for f in live])
    dm = avg([f["mret"].get(cur_m) for f in live])
    y1 = avg([f["tr"].get("1Y") for f in live])
    beat = [f for f in live if f["metrics"].get("return_1y") is not None and f["metrics"].get("cat_return_1y") is not None]
    nb = sum(f["metrics"]["return_1y"] > f["metrics"]["cat_return_1y"] for f in beat)
    best = max(live, key=lambda f: f["tr"].get("1Y", -999), default=None)
    kpis([("Funds tracked", str(len(funds)), f"{len(funds) - len(live)} with old NAV" if len(live) < len(funds) else None, None),
          ("Average, last day", pct(d1, 2, True), None, direction(d1)),
          (f"Average, {datetime.strptime(cur_m, '%Y-%m').strftime('%b') if cur_m else 'this month'} so far",
           pct(dm, 2, True), None, direction(dm)),
          ("Average 1-year return", pct(y1), None, None),
          ("Beating category (1Y)", f"{nb} of {len(beat)}" if beat else "–", None, None),
          ("Best 1Y", best["short"] if best else "–", pct(best["tr"].get("1Y"), 1, True) if best else None,
           direction(best["tr"].get("1Y")) if best else None)])
    st.markdown("<div class='muted'>Add <b>Units</b> and <b>Invested</b> columns to holdings.xlsx to see your own ₹ value, gain and XIRR.</div>",
                unsafe_allow_html=True)

tabs = st.tabs(["Overview", "Factsheet", "Across my funds"])

# ---------------------------------------------------------------- Overview
with tabs[0]:
    if HAS_UNITS:
        mv = []
        for f in live:
            d = A.monthly_values(f["h"], f["nav_hist"], pf.transactions)
            if len(d):
                mv.append(d)
        if mv:
            tot = pd.concat(mv).groupby("month")[["value", "invested"]].sum().reset_index()
            tot = tot[tot["value"] > 0]
            st.subheader("Portfolio value, month-end")
            fig = go.Figure()
            fig.add_scatter(x=tot["month"], y=tot["value"], name="Value", mode="lines", line=dict(width=2, color=SERIES[0]))
            fig.add_scatter(x=tot["month"], y=tot["invested"], name="Invested", mode="lines",
                            line=dict(width=2, color=SERIES[1], dash="dot"))
            show(style(fig, 320, legend=True).update_yaxes(tickprefix="₹"))

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.subheader("Returns")
        per = st.segmented_control("Period", ["1M", "3M", "6M", "1Y", "3Y", "5Y"], default="1Y",
                                   key="perrank", label_visibility="collapsed") or "1Y"
        vals = [(f["short"], f["tr"].get(per)) for f in live if f["tr"].get(per) is not None]
        if vals:
            show(bars([v[0] for v in vals], [v[1] for v in vals]))
            if per in ("3Y", "5Y"):
                st.markdown("<div class='muted'>Annualised. Funds younger than the period are left out.</div>",
                            unsafe_allow_html=True)
        missing = [f["short"] for f in funds if f["stale"]]
        if missing:
            st.markdown(f"<div class='muted'>No current NAV for: {', '.join(missing)}.</div>",
                        unsafe_allow_html=True)
    with right:
        st.subheader("Compare funds")
        names = [f["short"] for f in live]
        ranked = sorted(live, key=lambda f: f["tr"].get("1Y", -999), reverse=True)
        default = [ranked[0]["short"], ranked[-1]["short"]] if len(ranked) > 1 else names[:1]
        c1, c2 = st.columns([3, 1])
        pick = c1.multiselect("Funds (up to 4)", names, default=default, max_selections=4, key="cmp")
        span = c2.selectbox("Since", ["6M", "1Y", "3Y", "5Y"], index=1, key="cmpspan")
        if pick and latest_day:
            days = {"6M": 182, "1Y": 365, "3Y": 1095, "5Y": 1826}[span]
            start = (date.fromisoformat(latest_day) - timedelta(days=days)).isoformat()
            fig = go.Figure()
            for i, n in enumerate(pick):
                f = next(x for x in live if x["short"] == n)
                d = pd.DataFrame(f["nav_hist"], columns=["Date", "NAV"])
                d = d[d["Date"] >= start]
                if len(d) < 2:
                    continue
                d["v"] = d["NAV"] / d["NAV"].iloc[0] * 100
                fig.add_scatter(x=d["Date"], y=d["v"], name=n, mode="lines", line=dict(width=2, color=SERIES[i]),
                                hovertemplate=n + "<br>%{x}<br>₹100 → ₹%{y:.1f}<extra></extra>")
                fig.add_annotation(x=d["Date"].iloc[-1], y=d["v"].iloc[-1], text=f"{d['v'].iloc[-1] - 100:+.1f}%",
                                   showarrow=False, xanchor="left", xshift=6, font=dict(size=11))
            fig.add_hline(y=100, line_width=1, line_dash="dot", line_color="rgba(128,128,128,.6)")
            style(fig, 380, legend=True).update_layout(hovermode="x unified", margin=dict(r=50))
            fig.update_yaxes(title=None, tickprefix="₹")
            show(fig)
            st.markdown("<div class='muted'>What ₹100 put in at the start would be worth now.</div>",
                        unsafe_allow_html=True)

    st.subheader("Month by month")
    withnav = [f for f in funds if f["mret"]]
    grid = pd.DataFrame({f["short"]: f["mret"] for f in withnav}).T
    if len(grid.columns):
        cols = sorted(grid.columns)[-12:]
        grid = grid[cols]
        order = sorted(withnav, key=lambda f: (f["stale"], -(f["tr"].get("1Y") or -999)))
        grid = grid.loc[[f["short"] for f in order if f["short"] in grid.index]]
        lim = max(5, min(15, float(grid.abs().max().max() or 5)))
        xl = [datetime.strptime(c, "%Y-%m").strftime("%b %y") for c in cols]
        text = [["" if pd.isna(v) else f"{v:+.1f}" for v in row] for row in grid.values]
        fig = go.Figure(go.Heatmap(z=grid.values, x=xl, y=list(grid.index), text=text, texttemplate="%{text}",
                                   textfont=dict(size=11), colorscale=DIVERGING, zmin=-lim, zmax=lim, xgap=2, ygap=2,
                                   showscale=False, hovertemplate="%{y}<br>%{x}: %{z:+.2f}%<extra></extra>"))
        style(fig, 60 + 30 * len(grid)).update_xaxes(side="top", showgrid=False).update_yaxes(
            autorange="reversed", showgrid=False)
        show(fig)
        st.markdown("<div class='muted'>NAV change in each calendar month, %. Blue = up, red = down. "
                    "The last column is month-to-date. Sorted by 1-year return.</div>", unsafe_allow_html=True)
    nonav = [f["short"] for f in funds if not f["mret"]]
    if nonav:
        st.markdown(f"<div class='muted'>No NAV yet for: {html.escape(', '.join(nonav))}.</div>",
                    unsafe_allow_html=True)

    st.subheader("By category")
    cat = pd.DataFrame([{"Category": f["category"], "w": f["weight_value"]} for f in funds if f["weight_value"]])
    if len(cat):
        if HAS_UNITS:
            cs = cat.groupby("Category")["w"].sum() / tot_val * 100
            fig = bars(cs.index, cs.values, fmt="{:.1f}%", hover_name="Share")
        else:
            cs = cat.groupby("Category").size()
            fig = bars(cs.index, cs.values.astype(float), fmt="{:.0f} funds", hover_name="Funds")
            fig.update_xaxes(ticksuffix="", dtick=1)
        fig.update_traces(marker_color=SERIES[0])
        show(fig)

# ---------------------------------------------------------------- Factsheet
with tabs[1]:
    rows = []
    for f in funds:
        m = f["metrics"]
        sm = A.summary(f["holdings"])
        notes = A.study(f["snaps"], f["fund"])
        f["notes"] = notes
        r1, c1 = m.get("return_1y"), m.get("cat_return_1y")
        rows.append({"Fund": f["short"], "Category": f["category"], "AUM ₹Cr": m.get("aum_cr"),
                     "Expense %": m.get("expense_ratio"), "P/E": m.get("pe"), "Stocks": sm.get("n_holdings"),
                     "Top 10 %": sm.get("top10"), "Cash & other %": sm.get("cash"),
                     "1Y vs category": (r1 - c1) if (r1 is not None and c1 is not None) else None,
                     "To watch": sum(n["level"] == "watch" for n in notes),
                     "Portfolio as of": f["portfolio_date"]})
    nc = st.column_config.NumberColumn
    st.markdown("<div class='muted'>Latest factsheet numbers for every fund. Pick one below for the full read.</div>",
                unsafe_allow_html=True)
    df, cfg = styled(rows, {"AUM ₹Cr": "{:,.0f}", "Expense %": "{:.2f}", "P/E": "{:.1f}", "Stocks": "{:.0f}",
                            "Top 10 %": "{:.1f}", "Cash & other %": "{:.1f}", "1Y vs category": "{:+.1f} pp",
                            "To watch": "{:.0f}"})
    st.dataframe(df, column_config=cfg, hide_index=True, width="stretch", height=38 + 35 * len(rows))

    order = sorted(funds, key=lambda f: -sum(n["level"] == "watch" for n in f["notes"]))
    pick = st.selectbox("Fund", [f["short"] for f in order], key="fspick")
    f = next(x for x in funds if x["short"] == pick)
    m, notes = f["metrics"], f["notes"]
    st.markdown(f"<div class='fund-h'>{html.escape(f['fund'])}</div>", unsafe_allow_html=True)
    chips = [f["category"], m.get("benchmark"), f"Manager: {m['fund_manager']}" if m.get("fund_manager") else None,
             f"Exit load: {m['exit_load']}" if m.get("exit_load") else None,
             f"Portfolio as of {f['portfolio_date']}" if f["portfolio_date"] else None]
    st.markdown("<div class='chips'>" + "".join(f"<span class='chip'>{html.escape(str(c))}</span>" for c in chips if c)
                + "</div>", unsafe_allow_html=True)
    about = [n for n in notes if n["level"] == "about"]
    if about:
        st.markdown(f"<div style='margin:-4px 0 4px'>{html.escape(about[0]['text'])}</div>", unsafe_allow_html=True)

    def vs(r, c):
        if r is None or c is None:
            return None, None
        return f"{r - c:+.1f} pp vs category", direction(r - c)

    s1, d1 = vs(m.get("return_1y"), m.get("cat_return_1y"))
    s3, d3 = vs(m.get("return_3y"), m.get("cat_return_3y"))
    kpis([("Fund size", f"₹{m['aum_cr']:,.0f} Cr" if m.get("aum_cr") else "–", None, None),
          ("Expense ratio (Direct)", pct(m.get("expense_ratio"), 2), None, None),
          ("P/E · P/B", f"{m['pe']:.1f} · {m['pb']:.1f}" if m.get("pe") and m.get("pb") else
           (f"{m['pe']:.1f}" if m.get("pe") else "–"), None, None),
          ("1Y return", pct(m.get("return_1y")), s1, d1),
          ("3Y return (pa)", pct(m.get("return_3y")), s3, d3),
          ("Your plan, 1Y", pct(f["tr"].get("1Y")), "from your NAV", None)])

    # ---- top 10 holdings with month-on-month change
    months_h = [k for k, v in f["snaps"].items() if v.get("holdings")]
    cur_h = f["snaps"][months_h[-1]]["holdings"] if months_h else []
    prev_h = f["snaps"][months_h[-2]]["holdings"] if len(months_h) > 1 else None
    st.markdown("<div class='notes-h'>Top 10 holdings and how they changed from last month</div>",
                unsafe_allow_html=True)
    if not cur_h:
        st.info("Stock-level holdings aren't available for this fund yet.")
    else:
        prev_w = {A.key_of(x): x["pct"] for x in (prev_h or [])}
        eq = sorted([x for x in cur_h if not A.is_cash(x)], key=lambda x: -x["pct"])
        top10 = eq[:10]
        rows = []
        for i, x in enumerate(top10, 1):
            k = A.key_of(x)
            was = prev_w.get(k) if prev_h is not None else None
            if prev_h is None:
                move, chg = "–", None
            elif k not in prev_w:
                move, chg = "New this month", None
            else:
                chg = x["pct"] - was
                move = "▲ Added" if chg >= A.MOVE_PP else ("▼ Trimmed" if chg <= -A.MOVE_PP else "No real change")
            rows.append({"Stock": x["name"], "Sector": x.get("sector"), "% of fund": x["pct"],
                         "Last month %": f"{was:.2f}" if was is not None else "–",
                         "Change (pp)": f"{chg:+.2f}" if chg is not None else "–", "Move": move})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=38 + 35 * len(rows),
                     column_config={
            "% of fund": st.column_config.ProgressColumn(format="%.2f%%", min_value=0,
                                                         max_value=max(10.0, float(top10[0]["pct"]))),
            "Last month %": st.column_config.TextColumn(), "Change (pp)": st.column_config.TextColumn()})
        if prev_h is None:
            st.markdown(f"<div class='muted'>Only one month of holdings on record ({months_h[-1]}). The change columns "
                        "fill in automatically once next month's portfolio is published, usually by the 10th–15th.</div>",
                        unsafe_allow_html=True)
        else:
            prev_top = sorted([x for x in prev_h if not A.is_cash(x)], key=lambda x: -x["pct"])[:10]
            now_keys = {A.key_of(x) for x in top10}
            cur_w = {A.key_of(x): x["pct"] for x in cur_h}
            out = [f"{x['name']} ({x['pct']:.1f}% → {cur_w.get(A.key_of(x), 0):.1f}%)"
                   for x in prev_top if A.key_of(x) not in now_keys]
            st.markdown(f"<div class='muted'>Compared {months_h[-2]} → {months_h[-1]}."
                        + (f" Dropped out of the top 10: {html.escape(', '.join(out))}." if out else "") + "</div>",
                        unsafe_allow_html=True)
        if prev_h is not None:
            with st.expander("All holdings with monthly change"):
                d = A.diff_holdings(prev_h, cur_h)
                d = d[~d["cash"]].drop(columns=["cash"])
                st.dataframe(d, hide_index=True, width="stretch")

    left, right = st.columns([1.1, 1], gap="large")
    with left:
        groups = [("Worth a look", "watch"), ("What changed", "info"), ("Positives", "good")]
        tags = {"watch": "Watch", "info": "Info", "good": "Good"}
        for title, lvl in groups:
            g = [n for n in notes if n["level"] == lvl]
            if not g:
                continue
            st.markdown(f"<div class='notes-h'>{title}</div>", unsafe_allow_html=True)
            st.markdown("".join(f"<div class='note' style='--c:{STATUS[lvl]}'><span class='tag'>{tags[lvl]}</span>"
                                f"<span>{html.escape(n['text'])}</span></div>" for n in g), unsafe_allow_html=True)
    with right:
        if cur_h:
            sw = A.sector_weights(cur_h)
            top_s = dict(list(sw.items())[:8])
            st.markdown("<div class='notes-h'>Sectors, % of fund</div>", unsafe_allow_html=True)
            show(bars(list(top_s), list(top_s.values()), fmt="{:.1f}%", hover_name="% of fund")
                 .update_traces(marker_color=SERIES[0]))

    hist = []
    for mo, s in f["snaps"].items():
        sm, mm = A.summary(s.get("holdings", [])), s.get("metrics", {})
        hist.append({"Month": mo, "AUM ₹Cr": mm.get("aum_cr"), "Expense %": mm.get("expense_ratio"),
                     "P/E": mm.get("pe"), "Stocks": sm.get("n_holdings"), "Top 10 %": sm.get("top10"),
                     "Cash & other %": sm.get("cash"), "Top sector": sm.get("top_sector"), "Manager": mm.get("fund_manager")})
    if hist:
        with st.expander(f"Factsheet history ({len(hist)} month{'s' if len(hist) != 1 else ''})"):
            df, cfg = styled(hist[::-1], {"AUM ₹Cr": "{:,.0f}", "Expense %": "{:.2f}", "P/E": "{:.1f}",
                                          "Stocks": "{:.0f}", "Top 10 %": "{:.1f}", "Cash & other %": "{:.1f}"})
            st.dataframe(df, column_config=cfg, hide_index=True, width="stretch")

# ---------------------------------------------------------------- Across my funds
with tabs[2]:
    lt = A.look_through([{**f, "value": f["weight_value"]} for f in funds])
    denom = tot_val if HAS_UNITS else 100.0
    if not len(lt):
        st.info("Appears once holdings data has been fetched.")
    else:
        st.markdown("<div class='muted'>" + ("Your money in each stock, adding up every fund that holds it."
                    if HAS_UNITS else "Your exposure to each stock across all your funds, counting each fund equally "
                    "(add units to the Excel to weight by your ₹).") + "</div>", unsafe_allow_html=True)
        a, b = st.columns([3, 2], gap="large")
        top = lt[lt["Sector"] != "Cash"].head(30).copy()
        top["% of portfolio"] = top["₹"] / denom * 100
        top["Funds"] = top["Funds"].map(lambda s: ", ".join(short(x) for x in s.split(", ")))
        cols = ["Stock", "Sector"] + (["₹"] if HAS_UNITS else []) + ["% of portfolio", "In # funds", "Funds"]
        a.dataframe(top[cols], hide_index=True, width="stretch", height=38 + 35 * 15, column_config={
            "₹": st.column_config.NumberColumn(format="₹%,.0f"),
            "% of portfolio": st.column_config.ProgressColumn(format="%.2f%%", min_value=0,
                                                              max_value=float(top["% of portfolio"].max()))})
        with b:
            sec = (lt.groupby("Sector")["₹"].sum() / denom * 100).sort_values(ascending=False).head(12)
            st.markdown("<div class='notes-h'>Sectors, % of portfolio</div>", unsafe_allow_html=True)
            show(bars(list(sec.index), list(sec.values), fmt="{:.1f}%", hover_name="%")
                 .update_traces(marker_color=SERIES[0]))
        multi = lt[(lt["In # funds"] >= 3) & (lt["Sector"] != "Cash")]
        if len(multi):
            st.markdown(f"<div class='note' style='--c:{STATUS['watch']}'><span class='tag'>Watch</span><span>"
                        f"{len(multi)} stocks sit in 3 or more of your funds: {html.escape(', '.join(multi['Stock'].head(10)))}"
                        f"{' …' if len(multi) > 10 else ''}.</span></div>", unsafe_allow_html=True)
        hf = [f for f in funds if f["holdings"]]
        if len(hf) >= 2:
            st.subheader("Overlap between your funds")
            names = [f["short"] for f in hf]
            mat = [[None if i == j else A.overlap(hf[i]["holdings"], hf[j]["holdings"]) for j in range(len(hf))]
                   for i in range(len(hf))]
            txt = [["" if v is None else f"{v:.0f}" for v in row] for row in mat]
            fig = go.Figure(go.Heatmap(z=mat, x=names, y=names, text=txt, texttemplate="%{text}", textfont=dict(size=10),
                                       colorscale=[[0, MID], [1, "#1757ac"]], zmin=0, zmax=80, xgap=2, ygap=2,
                                       showscale=False, hovertemplate="%{y} & %{x}: %{z:.0f}% overlap<extra></extra>"))
            show(style(fig, 120 + 32 * len(hf)).update_xaxes(tickangle=-40, showgrid=False)
                 .update_yaxes(autorange="reversed", showgrid=False))
            pairs = sorted([(names[i], names[j], mat[i][j]) for i in range(len(hf)) for j in range(i + 1, len(hf))
                            if mat[i][j] and mat[i][j] >= 40], key=lambda p: -p[2])
            for x, y, v in pairs[:6]:
                st.markdown(f"<div class='note' style='--c:{STATUS['watch']}'><span class='tag'>Watch</span>"
                            f"<span>{html.escape(x)} and {html.escape(y)} share {v:.0f}% of their portfolios.</span></div>",
                            unsafe_allow_html=True)
