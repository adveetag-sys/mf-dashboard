"""Offline end-to-end test: fakes the web sources, runs the updater for two months,
then renders every tab of the Streamlit app.

Run:  python tests/test_pipeline.py      (works on a temporary copy of the repo)
"""
import json
import random
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp()) / "repo"
shutil.copytree(SRC, tmp, ignore=shutil.ignore_patterns("__pycache__", "tests"))
sys.path.insert(0, str(tmp))

from mfdash import holdings, resolve, sources, store  # noqa: E402

pf = holdings.load_portfolio(store.find_holdings_file())
random.seed(7)
codes = {h.isin: str(100000 + i) for i, h in enumerate(pf.holdings)}
UNIVERSE = [{"code": c, "isin": i, "isin2": None, "name": f"Scheme {c} - Regular Plan - Growth"} for i, c in codes.items()]
STOCKS = [("HDFC Bank", "Financial"), ("ICICI Bank", "Financial"), ("Reliance Industries", "Energy"),
          ("Infosys", "Technology"), ("TCS", "Technology"), ("Bharti Airtel", "Communication"),
          ("Larsen & Toubro", "Construction"), ("Axis Bank", "Financial"), ("Sun Pharma", "Healthcare"),
          ("Maruti Suzuki", "Automobile"), ("Persistent Systems", "Technology"), ("Dixon Tech", "Capital Goods"),
          ("Coforge", "Technology"), ("Trent", "Consumer"), ("Cholamandalam", "Financial"), ("Max Healthcare", "Healthcare")]
MONTH = {"n": 0}


def fake_nav(code):
    v, d, rows = 50.0, date.today() - timedelta(days=365 * 6), []
    rnd = random.Random(int(code))
    while d <= date.today():
        if d.weekday() < 5:
            v *= 1 + rnd.gauss(0.0005, 0.01)
            rows.append((d.isoformat(), round(v, 4)))
        d += timedelta(days=1)
    return {"meta": {"scheme_name": f"Scheme {code}", "isin_growth": None}, "nav": rows}


def fake_groww(slug):
    rnd = random.Random(hash(slug) % 1000 + MONTH["n"])
    picks = rnd.sample(STOCKS, 12)
    w = [rnd.uniform(2, 9) for _ in picks]
    tot = sum(w)
    hold = [{"company_name": n, "sector_name": s, "nature_name": "EQ", "corpus_per": round(x / tot * 94, 2),
             "portfolio_date": ["2026-09-30", "2026-10-31"][MONTH["n"]]} for (n, s), x in zip(picks, w)]
    hold.append({"company_name": "TREPS", "sector_name": "Cash", "nature_name": "CASH", "corpus_per": 6.0,
                 "portfolio_date": ["2026-09-30", "2026-10-31"][MONTH["n"]]})
    return {"scheme_name": slug.replace("-", " ").title(), "category": "Equity", "sub_category": "Flexi Cap",
            "aum": 20000 + 3000 * MONTH["n"], "expense_ratio": 0.7 + 0.05 * MONTH["n"],
            "fund_manager_details": [{"person_name": "A Manager"}] + ([{"person_name": "New Person"}] if MONTH["n"] else []),
            "return_stats": [{"return1y": 14.2, "return3y": 18.1, "cat_return1y": 12.0, "cat_return3y": 19.0}],
            "benchmark_name": "NIFTY 500 TRI", "exit_load": "1% within 1 year", "holdings": hold}


resolve.amfi_universe = lambda: UNIVERSE
sources.mfapi_nav = fake_nav
sources.groww_search = lambda q: []
sources.groww_scheme = lambda slug: fake_groww(slug) if slug.endswith("-fund-direct-growth") else None
sources.groww_portfolio_stats = lambda code: {"pe": 30 + 4 * MONTH["n"], "pb": 5.0}
sources.kuvera = lambda isin: None

sys.path.insert(0, str(tmp / "scripts"))
import update  # noqa: E402

update.time.sleep = lambda s: None
update.main()                                   # month 1
for p in store.SNAP_DIR.glob("*.json"):         # pretend the last fetch was a while ago
    j = json.loads(p.read_text())
    for m in j["months"].values():
        m["fetched_on"] = "2026-10-01"
    p.write_text(json.dumps(j))
MONTH["n"] = 1
update.main()                                   # month 2

st_ = json.loads(store.STATUS.read_text())
ok = sum("ok" in v["factsheet"] for v in st_["funds"].values())
print(f"factsheet ok for {ok}/{len(st_['funds'])} funds; log lines: {len(st_['log'])}")
assert ok == len(pf.holdings), st_
snap = store.load_snapshots(codes[pf.holdings[0].isin])
assert list(snap) == ["2026-09", "2026-10"], list(snap)

from mfdash import analysis as A  # noqa: E402
notes = A.study(snap, "x")
print("\nSample factsheet study notes:")
for n in notes:
    print(" ", n["level"], "|", n["text"])

from streamlit.testing.v1 import AppTest  # noqa: E402
import os  # noqa: E402
os.chdir(tmp)
at = AppTest.from_file(str(tmp / "app.py"), default_timeout=120).run()
assert not at.exception, [e.value for e in at.exception]
print("\nApp rendered OK. Tabs:", [t.label for t in at.tabs])
print("Metrics:", [(m.label, m.value) for m in at.metric][:5])

# second scenario: add units + invested to check the ₹ view
import openpyxl  # noqa: E402
wb = openpyxl.load_workbook(tmp / "holdings.xlsx")
ws = wb.active
ws["D3"], ws["E3"], ws["F3"] = "Units", "Invested", "Purchase Date"
for r in range(4, ws.max_row + 1):
    ws.cell(r, 4, 100 + r * 10)
    ws.cell(r, 5, 5000 + r * 400)
    ws.cell(r, 6, "2023-04-10")
wb.save(tmp / "holdings.xlsx")
at = AppTest.from_file(str(tmp / "app.py"), default_timeout=120).run()
assert not at.exception, [e.value for e in at.exception]
print("With units:", [(m.label, m.value) for m in at.metric][:5])
print("\nALL TESTS PASSED  (temp repo:", tmp, ")")
