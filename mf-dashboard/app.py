"""Mutual fund dashboard (Streamlit). Data is refreshed daily by GitHub Actions."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from mfdash import analysis as A
from mfdash import store
from mfdash.holdings import load_portfolio

st.set_page_config(page_title="My Mutual Funds", page_icon="📈", layout="wide")


def inr(x, dec=0):
    if x is None or pd.isna(x):
        return "–"
    neg, x = x < 0, abs(x)
    s = f"{x:,.{dec}f}"
    # Indian grouping: 12,34,567
    whole, _, frac = s.partition(".")
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
        code = m.get("code")
        nav = store.load_nav(code) if code else []
        snaps = store.load_snapshots(code) if code else {}
        pos = A.position(h, nav, pf.transactions)
        latest = snaps[list(snaps)[-1]] if snaps else {}
        funds.append({**pos, "h": h, "code": code, "map": m, "nav_hist": nav, "snaps": snaps,
                      "tr": A.trailing_returns(nav), "mret": A.monthly_returns(nav, 12),
                      "metrics": latest.get("metrics", {}), "holdings": latest.get("holdings", []),
                      "portfolio_date": latest.get("portfolio_date"),
                      "category": (latest.get("metrics", {}).get("sub_category")
                                   or latest.get("metrics", {}).get("category") or "Unknown")})
    return pf, funds, store.read_json(store.STATUS, {})


_hf = store.find_holdings_file()
stamp = (max([p.stat().st_mtime for p in store.DATA.rglob("*.json")] + [0]),
         _hf.stat().st_mtime if _hf else 0, str(_hf))
loaded = load_all(stamp)
if loaded is None:
    st.error("Put your holdings Excel in the repo as `holdings.xlsx` and the dashboard will build itself.")
    st.stop()
pf, funds, status = loaded

# ---------------------------------------------------------------- header
st.title("My Mutual Funds")
upd = status.get("updated_at")
if upd:
    t = datetime.fromisoformat(upd)
    hrs = (datetime.now(timezone.utc) - t).total_seconds() / 3600
    st.caption(f"Data refreshed automatically · last run {t.astimezone().strftime('%d %b %Y, %H:%M')} "
               f"({hrs:.0f} h ago) · NAV from AMFI via mfapi.in · holdings and factsheet numbers from Groww / Kuvera")
else:
    st.caption("Waiting for the first automatic data run (GitHub Actions).")

tot_val = sum(f["value"] or 0 for f in funds)
tot_inv = sum(f["invested"] or 0 for f in funds if f["value"])
tot_day = sum(f["day_change"] or 0 for f in funds)
all_flows = [fl for f in funds for fl in f["flows"]]
tot_x = A.xirr(all_flows)

HAS_UNITS = tot_val > 0
# with no units in the Excel, cross-fund views treat every fund equally
for f in funds:
    f["weight_value"] = f["value"] if HAS_UNITS else (100 / len(funds))


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


c = st.columns(5)
if HAS_UNITS:
    c[0].metric("Current value", inr(tot_val))
    c[1].metric("Invested", inr(tot_inv))
    c[2].metric("Gain", inr(tot_val - tot_inv), pct((tot_val / tot_inv - 1) * 100 if tot_inv else None, sign=True))
    c[3].metric("XIRR", pct(tot_x * 100 if tot_x is not None else None))
    c[4].metric("Last day", inr(tot_day), pct(tot_day / (tot_val - tot_day) * 100 if tot_val else None, 2, True))
else:
    cur_m = max([m for f in funds for m in f["mret"]], default=None)
    c[0].metric("Funds", len(funds))
    c[1].metric("Avg last day", pct(avg([f["tr"].get("1D") for f in funds]), 2, True))
    c[2].metric(f"Avg this month ({cur_m or '–'})", pct(avg([f["mret"].get(cur_m) for f in funds]), 2, True))
    c[3].metric("Avg 1Y return", pct(avg([f["tr"].get("1Y") for f in funds])))
    beat = [f for f in funds if f["metrics"].get("return_1y") is not None and f["metrics"].get("cat_return_1y") is not None]
    c[4].metric("Beating category (1Y)",
                f"{sum(f['metrics']['return_1y'] > f['metrics']['cat_return_1y'] for f in beat)} of {len(beat)}" if beat else "–")
    st.caption("Add **Units** and **Invested** columns to holdings.xlsx to see your own ₹ value, gain and XIRR here.")

tabs = st.tabs(["Overview", "My funds", "Monthly changes", "Factsheet study", "Across my funds", "Data status"])

# ---------------------------------------------------------------- Overview
with tabs[0]:
    grid = pd.DataFrame({f["fund"]: f["mret"] for f in funds}).T
    if len(grid.columns):
        grid = grid[sorted(grid.columns)[-12:]]
        st.subheader("Monthly NAV change, % (latest month is month-to-date)")
        fig = px.imshow(grid.values, x=list(grid.columns), y=[n[:40] for n in grid.index], text_auto=".1f",
                        color_continuous_scale="RdYlGn", color_continuous_midpoint=0, aspect="auto")
        st.plotly_chart(fig.update_layout(height=140 + 28 * len(grid), margin=dict(l=0, r=0, t=10, b=0),
                                          coloraxis_showscale=False), width="stretch")
        rb = []
        for f in funds:
            if f["nav_hist"]:
                d = pd.DataFrame(f["nav_hist"], columns=["Date", "NAV"])
                d = d[d["Date"] >= (pd.Timestamp(d["Date"].iloc[-1]) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")]
                d["Growth of 100"] = d["NAV"] / d["NAV"].iloc[0] * 100
                d["Fund"] = f["fund"][:40]
                rb.append(d)
        if rb:
            st.plotly_chart(px.line(pd.concat(rb), x="Date", y="Growth of 100", color="Fund",
                                    title="₹100 invested a year ago in each fund")
                            .update_layout(height=460, margin=dict(l=0, r=0, t=40, b=0),
                                           legend=dict(font=dict(size=10))), width="stretch")
    mv = []
    for f in funds:
        d = A.monthly_values(f["h"], f["nav_hist"], pf.transactions)
        if len(d):
            d["fund"] = f["fund"]
            mv.append(d)
    if mv and HAS_UNITS:
        mv = pd.concat(mv)
        tot = mv.groupby("month")[["value", "invested"]].sum().reset_index()
        tot = tot[tot["value"] > 0]
        fig = go.Figure()
        fig.add_scatter(x=tot["month"], y=tot["value"], name="Value", mode="lines+markers", line=dict(width=3))
        fig.add_scatter(x=tot["month"], y=tot["invested"], name="Invested", mode="lines", line=dict(dash="dot"))
        fig.update_layout(title="Portfolio value at each month-end", height=360, margin=dict(l=0, r=0, t=40, b=0),
                          yaxis_tickprefix="₹", legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, width="stretch")
        tot["Change ₹"] = tot["value"].diff()
        tot["Change %"] = tot["value"].pct_change() * 100
        tot["Gain ₹"] = tot["value"] - tot["invested"]
        show = tot.iloc[::-1].head(24)
        st.subheader("Month on month")
        st.dataframe(pd.DataFrame({
            "Month": show["month"], "Value": show["value"].map(inr), "Invested": show["invested"].map(inr),
            "Change ₹": show["Change ₹"].map(inr), "Change %": show["Change %"].map(lambda x: pct(x, 2, True)),
            "Gain ₹": show["Gain ₹"].map(inr)}), hide_index=True, width="stretch")
        if not len(pf.transactions):
            st.caption("Without a Transactions sheet, past months assume you held today's units from your start date.")
    a, b = st.columns(2)
    alloc = pd.DataFrame([{"Fund": f["fund"], "Category": f["category"], "Value": f["weight_value"]} for f in funds])
    if not HAS_UNITS:
        a.caption("Shares are equal per fund until units are added.")
    if len(alloc):
        a.plotly_chart(px.pie(alloc, names="Fund", values="Value", hole=0.5, title="By fund")
                       .update_layout(height=380, margin=dict(l=0, r=0, t=40, b=0), showlegend=False)
                       .update_traces(textinfo="percent+label", textposition="inside"), width="stretch")
        b.plotly_chart(px.pie(alloc, names="Category", values="Value", hole=0.5, title="By category")
                       .update_layout(height=380, margin=dict(l=0, r=0, t=40, b=0))
                       .update_traces(textinfo="percent"), width="stretch")

# ---------------------------------------------------------------- My funds
with tabs[1]:
    rows = []
    for f in sorted(funds, key=lambda f: -(f["value"] or 0)):
        m, tr = f["metrics"], f["tr"]
        r = {"Fund": f["fund"], "Category": f["category"], "NAV": f["nav"], "NAV date": f["nav_date"]}
        if HAS_UNITS:
            r.update({"Units": f["units"], "Value": f["value"], "Invested": f["invested"], "Gain": f["gain"],
                      "Gain %": f["gain_pct"], "XIRR %": f["xirr"],
                      "Weight %": (f["value"] / tot_val * 100) if (f["value"] and tot_val) else None})
        r.update({"1D %": tr.get("1D"), "1M %": tr.get("1M"), "3M %": tr.get("3M"), "6M %": tr.get("6M"),
                  "1Y %": tr.get("1Y"), "3Y % pa": tr.get("3Y"), "5Y % pa": tr.get("5Y"),
                  "Cat 1Y %": m.get("cat_return_1y"), "Expense % (Direct)": m.get("expense_ratio"),
                  "AUM ₹Cr": m.get("aum_cr")})
        rows.append(r)
    df = pd.DataFrame(rows)
    st.caption("Returns are from your own plan's NAV. Category return and expense ratio come from Groww, "
               "which shows Direct plans; a Regular plan's expense ratio is higher.")
    st.dataframe(df, hide_index=True, width="stretch", column_config={
        "Units": st.column_config.NumberColumn(format="%.3f"), "NAV": st.column_config.NumberColumn(format="%.2f"),
        "Value": st.column_config.NumberColumn(format="₹%,.0f"), "Invested": st.column_config.NumberColumn(format="₹%,.0f"),
        "Gain": st.column_config.NumberColumn(format="₹%,.0f"), "Gain %": st.column_config.NumberColumn(format="%.1f"),
        "XIRR %": st.column_config.NumberColumn(format="%.1f"), "Weight %": st.column_config.NumberColumn(format="%.1f"),
        **{k: st.column_config.NumberColumn(format="%.1f") for k in
           ["1M %", "3M %", "6M %", "1Y %", "3Y % pa", "5Y % pa", "Cat 1Y %"]},
        "1D %": st.column_config.NumberColumn(format="%.2f"),
        "Expense % (Direct)": st.column_config.NumberColumn(format="%.2f"),
        "AUM ₹Cr": st.column_config.NumberColumn(format="%,.0f")})
    pick = st.selectbox("NAV chart", [f["fund"] for f in funds], key="navpick")
    f = next(x for x in funds if x["fund"] == pick)
    if f["nav_hist"]:
        nd = pd.DataFrame(f["nav_hist"], columns=["Date", "NAV"])
        fig = px.line(nd, x="Date", y="NAV", title=f"{pick} – NAV")
        if f["since"]:
            fig.add_vline(x=f["since"], line_dash="dot", annotation_text="you started")
        st.plotly_chart(fig.update_layout(height=340, margin=dict(l=0, r=0, t=40, b=0)), width="stretch")

# ---------------------------------------------------------------- Monthly changes
with tabs[2]:
    st.write("What each fund bought and sold, from its monthly portfolio disclosure.")
    summ = []
    for f in funds:
        ks = list(f["snaps"])
        if len(ks) >= 2 and f["snaps"][ks[-1]].get("holdings") and f["snaps"][ks[-2]].get("holdings"):
            d = A.diff_holdings(f["snaps"][ks[-2]]["holdings"], f["snaps"][ks[-1]]["holdings"])
            d = d[~d["cash"]]
            summ.append({"Fund": f["fund"], "Compared": f"{ks[-2]} → {ks[-1]}",
                         "New": int((d.Action == "New entry").sum()), "Exited": int((d.Action == "Exited").sum()),
                         "Increased": int((d.Action == "Increased").sum()), "Reduced": int((d.Action == "Reduced").sum()),
                         "Biggest buy": (d.sort_values("Change (pp)", ascending=False).iloc[0]["Stock"] if len(d) else "")})
        else:
            summ.append({"Fund": f["fund"], "Compared": f"needs 2 months (have {len(ks)})"})
    st.dataframe(pd.DataFrame(summ), hide_index=True, width="stretch")

    pick = st.selectbox("Fund", [f["fund"] for f in funds], key="chgpick")
    f = next(x for x in funds if x["fund"] == pick)
    months = [k for k, v in f["snaps"].items() if v.get("holdings")]
    if len(months) < 2:
        st.info(f"Holdings on record for: {', '.join(months) or 'none yet'}. "
                "Month-on-month changes appear once the next monthly disclosure is captured (around the 10th–15th).")
        if months:
            h = pd.DataFrame(f["snaps"][months[-1]]["holdings"])
            st.dataframe(h[["name", "sector", "instrument", "pct"]].rename(columns={"name": "Stock", "pct": "% of fund"}),
                         hide_index=True, width="stretch")
    else:
        c1, c2 = st.columns(2)
        m1 = c2.selectbox("Month", months[::-1], key="m1")
        older = [m for m in months if m < m1] or months[:1]
        m0 = c1.selectbox("Compared with", older[::-1], key="m0")
        d = A.diff_holdings(f["snaps"][m0]["holdings"], f["snaps"][m1]["holdings"])
        d = d[~d["cash"]].drop(columns=["cash"])
        k = st.columns(4)
        for col, lab in zip(k, ["New entry", "Exited", "Increased", "Reduced"]):
            col.metric(lab, int((d.Action == lab).sum()))
        for lab in ["New entry", "Exited", "Increased", "Reduced"]:
            sub = d[d.Action == lab]
            if len(sub):
                st.markdown(f"**{lab}**")
                st.dataframe(sub.drop(columns=["Action"]), hide_index=True, width="stretch")
        with st.expander("Full portfolio with changes"):
            st.dataframe(d, hide_index=True, width="stretch")
        s0 = A.sector_weights(f["snaps"][m0]["holdings"])
        s1 = A.sector_weights(f["snaps"][m1]["holdings"])
        sd = pd.DataFrame([{"Sector": s, "Change (pp)": s1.get(s, 0) - s0.get(s, 0), "Now %": s1.get(s, 0)}
                           for s in set(s0) | set(s1)]).sort_values("Change (pp)")
        sd = sd[sd["Change (pp)"].abs() >= 0.05]
        if len(sd):
            fig = px.bar(sd, x="Change (pp)", y="Sector", orientation="h", title=f"Sector weight change {m0} → {m1}",
                         color=sd["Change (pp)"] > 0, color_discrete_map={True: "#2e7d32", False: "#c62828"})
            st.plotly_chart(fig.update_layout(showlegend=False, height=max(300, 24 * len(sd)),
                                              margin=dict(l=0, r=0, t=40, b=0)), width="stretch")

# ---------------------------------------------------------------- Factsheet study
with tabs[3]:
    st.write("Each fund's latest factsheet numbers read against the month before. Updated automatically.")
    icon = {"watch": "🟠", "good": "🟢", "info": "🔹"}
    for f in sorted(funds, key=lambda f: -(f["value"] or 0)):
        notes = A.study(f["snaps"], f["fund"])
        n_watch = sum(n["level"] == "watch" for n in notes)
        with st.expander(f"{f['fund']}  ·  {f['category']}" + (f"  ·  {n_watch} to watch" if n_watch else ""),
                         expanded=n_watch > 0):
            m = f["metrics"]
            k = st.columns(6)
            k[0].metric("AUM", f"₹{m['aum_cr']:,.0f} Cr" if m.get("aum_cr") else "–")
            k[1].metric("Expense ratio (Direct)", pct(m.get("expense_ratio"), 2))
            k[2].metric("P/E", f"{m['pe']:.1f}" if m.get("pe") else "–")
            k[3].metric("1Y", pct(m.get("return_1y")), pct(m["return_1y"] - m["cat_return_1y"], 1, True) + " vs cat"
                        if m.get("return_1y") is not None and m.get("cat_return_1y") is not None else None)
            k[4].metric("3Y", pct(m.get("return_3y")), pct(m["return_3y"] - m["cat_return_3y"], 1, True) + " vs cat"
                        if m.get("return_3y") is not None and m.get("cat_return_3y") is not None else None)
            k[5].metric("Portfolio as of", f.get("portfolio_date") or "–")
            st.caption(f"Manager: {m.get('fund_manager') or '–'} · Benchmark: {m.get('benchmark') or '–'} · "
                       f"Exit load: {m.get('exit_load') or '–'} · Risk: {m.get('risk') or '–'}")
            for n in notes:
                st.markdown(f"{icon[n['level']]} {n['text']}")
            hist = []
            for mo, s in f["snaps"].items():
                sm = A.summary(s.get("holdings", []))
                mm = s.get("metrics", {})
                hist.append({"Month": mo, "AUM ₹Cr": mm.get("aum_cr"), "Expense %": mm.get("expense_ratio"),
                             "P/E": mm.get("pe"), "Stocks": sm.get("n_holdings"), "Top 10 %": sm.get("top10"),
                             "Cash %": sm.get("cash"), "Top sector": sm.get("top_sector"),
                             "Manager": mm.get("fund_manager")})
            if len(hist) > 1:
                st.dataframe(pd.DataFrame(hist[::-1]), hide_index=True, width="stretch")

# ---------------------------------------------------------------- Across my funds
with tabs[4]:
    lt = A.look_through([{**f, "value": f["weight_value"]} for f in funds])
    denom = tot_val if HAS_UNITS else 100.0
    if not len(lt):
        st.info("Appears once holdings data has been fetched.")
    else:
        st.write("Your money in each stock, adding up every fund that holds it." if HAS_UNITS else
                 "Your exposure to each stock across all your funds, counting each fund equally "
                 "(add units to the Excel to weight by your ₹).")
        a, b = st.columns([3, 2])
        top = lt[lt["Sector"] != "Cash"].head(30).copy()
        top["% of my MF money"] = top["₹"] / denom * 100
        top = top[["Stock", "Sector", "₹", "% of my MF money", "In # funds", "Funds"]]
        if not HAS_UNITS:
            top = top.drop(columns=["₹"])
        a.dataframe(top, hide_index=True, width="stretch", column_config={
            "₹": st.column_config.NumberColumn(format="₹%,.0f"),
            "% of my MF money": st.column_config.NumberColumn(format="%.2f")})
        sec = (lt.groupby("Sector")["₹"].sum() / denom * 100).sort_values(ascending=True)
        b.plotly_chart(px.bar(sec.tail(15), orientation="h", title="By sector (% of portfolio)",
                              labels={"value": "%", "Sector": ""})
                       .update_layout(showlegend=False, height=520, margin=dict(l=0, r=0, t=40, b=0)),
                       width="stretch")
        multi = lt[(lt["In # funds"] >= 3) & (lt["Sector"] != "Cash")]
        if len(multi):
            st.markdown(f"🟠 **{len(multi)} stocks sit in 3 or more of your funds**: "
                        + ", ".join(multi["Stock"].head(10)) + ".")
        hf = [f for f in funds if f["holdings"]]
        if len(hf) >= 2:
            names = [f["fund"] for f in hf]
            mat = [[100.0 if i == j else A.overlap(hf[i]["holdings"], hf[j]["holdings"]) for j in range(len(hf))]
                   for i in range(len(hf))]
            short = [n[:28] for n in names]
            fig = px.imshow(mat, x=short, y=short, text_auto=".0f", color_continuous_scale="Oranges",
                            title="Portfolio overlap between your funds (%)", zmin=0, zmax=100)
            st.plotly_chart(fig.update_layout(height=120 + 45 * len(hf), margin=dict(l=0, r=0, t=40, b=0)),
                            width="stretch")
            hi = [(names[i], names[j], mat[i][j]) for i in range(len(hf)) for j in range(i + 1, len(hf)) if mat[i][j] >= 40]
            for x, y, v in hi:
                st.markdown(f"🟠 {x} and {y} overlap {v:.0f}%; they behave much like the same fund.")

# ---------------------------------------------------------------- Data status
with tabs[5]:
    st.write("What the automatic run fetched. Nothing here needs you unless a row says *failed* or a match looks wrong.")
    rows = []
    for f in funds:
        stt = status.get("funds", {}).get(f["fund"], {})
        mp = f["map"]
        rows.append({"Your name": f["fund"], "Matched to": mp.get("scheme_name"), "AMFI code": mp.get("code"),
                     "Match": mp.get("method"), "Score": mp.get("score"), "Groww page": mp.get("groww_slug"),
                     "NAV": stt.get("nav"), "Factsheet": stt.get("factsheet")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for line in status.get("log", []):
        st.markdown(f"- {line}")
