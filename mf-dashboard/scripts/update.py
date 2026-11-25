"""Daily updater. GitHub Actions runs this; you never need to.

1. reads holdings.xlsx
2. matches each fund to its AMFI code / Groww page (cached in data/fund_map.json)
3. saves full NAV history          -> data/nav/<code>.json
4. saves this month's portfolio +
   factsheet numbers               -> data/snapshots/<code>.json  (one entry per month)
5. writes data/status.json so the dashboard can show what worked
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mfdash import sources, store  # noqa: E402
from mfdash.holdings import load_portfolio  # noqa: E402
from mfdash.resolve import resolve_all  # noqa: E402

FACTSHEET_EVERY_DAYS = 3
NAV_YEARS_KEPT = 8


def main():
    log_lines = []

    def log(msg):
        print(msg)
        log_lines.append(msg)

    for d in (store.DATA, store.NAV_DIR, store.SNAP_DIR):
        d.mkdir(parents=True, exist_ok=True)
    hf = store.find_holdings_file()
    if not hf:
        log("No holdings.xlsx found in the repo root.")
        store.write_json(store.STATUS, {"updated_at": datetime.now(timezone.utc).isoformat(), "log": log_lines})
        return
    pf = load_portfolio(hf)
    log(f"Read {len(pf.holdings)} funds from {hf.name}")
    fmap = resolve_all(pf, store.FUND_MAP, log)

    status = {"updated_at": datetime.now(timezone.utc).isoformat(), "holdings_file": hf.name,
              "funds": {}, "log": log_lines}
    today = date.today()
    for h in pf.holdings:
        m = fmap.get(h.fund, {})
        code = m.get("code")
        st = {"code": code, "nav": "skipped", "factsheet": "skipped"}
        status["funds"][h.fund] = st
        if not code and m.get("nav_by_isin") and m.get("isin"):
            code = f"isin-{m['isin']}"          # files keyed by ISIN when there is no AMFI code
            st["code"] = code
        if not code:
            st["nav"] = st["factsheet"] = "no AMFI code"
            continue

        # ---- NAV
        try:
            nav = sources.nav_by_isin(m["isin"]) if code.startswith("isin-") else sources.mfapi_nav(code)
            stale = not nav["nav"] or nav["nav"][-1][0] < (today - timedelta(days=15)).isoformat()
            if stale and m.get("isin") and not code.startswith("isin-"):
                alt = sources.nav_by_isin(m["isin"])
                if alt["nav"] and (not nav["nav"] or alt["nav"][-1][0] > nav["nav"][-1][0]):
                    nav = alt
                    log(f"{h.fund}: mfapi NAV was out of date, used the ISIN backup source.")
            cutoff = min([(today - timedelta(days=365 * NAV_YEARS_KEPT)).isoformat()] +
                         ([str(h.date)] if h.date else []))
            rows = [r for r in nav["nav"] if r[0] >= cutoff]
            store.write_json(store.NAV_DIR / f"{code}.json", {"meta": nav["meta"], "nav": rows})
            st["nav"] = f"ok, latest {rows[-1][0]}" if rows else "empty"
        except Exception as e:
            st["nav"] = f"failed: {e}"
            log(f"NAV failed for {h.fund}: {e}")

        # ---- factsheet + portfolio
        snap_path = store.SNAP_DIR / f"{code}.json"
        snaps = store.read_json(snap_path, {"months": {}})
        last = max(snaps["months"].values(), key=lambda s: s.get("fetched_on", ""), default=None)
        if last and (today - date.fromisoformat(last["fetched_on"])).days < FACTSHEET_EVERY_DAYS:
            st["factsheet"] = f"fresh (fetched {last['fetched_on']})"
            continue
        snap = None
        if m.get("groww_slug"):
            j = sources.groww_scheme(m["groww_slug"])
            time.sleep(1.0)
            if j:
                stats = None if code.startswith("isin-") else sources.groww_portfolio_stats(code)
                snap = sources.normalise_groww(j, stats)
                time.sleep(1.0)
        k = None
        for isin in dict.fromkeys([m.get("isin"), (snap or {}).get("metrics", {}).get("isin")]):
            if isin and not k:
                k = sources.kuvera(isin)      # Kuvera lists Direct plans; Groww gives the Direct ISIN
        if True:
            if k:
                kv = sources.normalise_kuvera(k)
                if snap is None:
                    snap = {"source": "kuvera", "portfolio_date": None, "metrics": kv, "holdings": []}
                else:  # fill gaps from Kuvera
                    for key, val in kv.items():
                        if snap["metrics"].get(key) in (None, "") and val not in (None, ""):
                            snap["metrics"][key] = val
        if snap is None:
            st["factsheet"] = "failed: no source answered"
            log(f"Factsheet failed for {h.fund}")
            continue
        snap["fetched_on"] = today.isoformat()
        month = (snap.get("portfolio_date") or today.isoformat())[:7]
        prev = snaps["months"].get(month)
        if prev and not snap["holdings"] and prev.get("holdings"):
            snap["holdings"] = prev["holdings"]            # keep holdings if this fetch had none
            snap["portfolio_date"] = prev.get("portfolio_date")
        snaps["months"][month] = snap
        store.write_json(snap_path, snaps)
        st["factsheet"] = (f"ok ({snap['source']}), portfolio as of {snap.get('portfolio_date') or 'n/a'}, "
                           f"{len(snap['holdings'])} holdings")
        if snap["holdings"] == [] and snap["source"] == "groww":
            log(f"Groww answered for {h.fund} but no holdings were found. Keys seen: {snap.get('raw_keys')}")

    # ---- Nifty 50 price index, for the Overview comparison
    try:
        rows = sources.nifty50()
        cutoff = (today - timedelta(days=365 * NAV_YEARS_KEPT)).isoformat()
        rows = [r for r in rows if r[0] >= cutoff]
        store.write_json(store.NAV_DIR / "nifty50.json", {"meta": {"scheme_name": "Nifty 50 (price index)"},
                                                         "nav": rows})
        status["nifty50"] = f"ok, latest {rows[-1][0]}"
    except Exception as e:
        status["nifty50"] = f"failed: {e}"
        log(f"Nifty 50 failed: {e}")

    import_amc_files(pf, fmap, today, log)
    store.write_json(store.STATUS, status)
    print("done")


def import_amc_files(pf, fmap, today, log):
    """Official monthly portfolio files you dropped into data/amc_portfolios/<YYYY-MM>/ fill months
    that Groww no longer shows (e.g. last month, for the month-on-month comparison)."""
    from mfdash import amc_files
    root = store.DATA / "amc_portfolios"
    if not root.exists():
        return
    for month_dir in sorted(p for p in root.iterdir() if p.is_dir() and len(p.name) == 7 and p.name[4] == "-"):
        month = month_dir.name
        for h in pf.holdings:
            m = fmap.get(h.fund, {})
            code = m.get("code") or (f"isin-{m['isin']}" if m.get("nav_by_isin") and m.get("isin") else None)
            if not code:
                continue
            path = store.SNAP_DIR / f"{code}.json"
            snaps = store.read_json(path, {"months": {}})
            if snaps["months"].get(month, {}).get("holdings"):
                continue                                  # already have this month
            names = [x for x in (h.fund, m.get("scheme_name")) if x]
            hold, where = amc_files.find_fund(month_dir, names)
            if not hold:
                log(f"{month}: couldn't find '{h.fund}' in the files in amc_portfolios/{month}.")
                continue
            # use the sector names from the newest month so sectors line up month to month
            later = [v for k, v in sorted(snaps["months"].items()) if k > month and v.get("holdings")]
            if later:
                from mfdash.analysis import match_names
                pairs = match_names(hold, later[0]["holdings"])
                for i, j in pairs.items():
                    hold[i]["sector"] = later[0]["holdings"][j].get("sector") or hold[i]["sector"]
            snaps["months"][month] = {"source": "amc file", "portfolio_date": amc_files.month_end(month),
                                      "metrics": {}, "holdings": hold, "fetched_on": today.isoformat(),
                                      "file": where}
            snaps["months"] = dict(sorted(snaps["months"].items()))
            store.write_json(path, snaps)
            log(f"{month}: read {len(hold)} holdings for '{h.fund}' from {where}.")


if __name__ == "__main__":
    main()
