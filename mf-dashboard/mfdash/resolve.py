"""Match the fund names in your Excel to AMFI scheme codes, ISINs and Groww pages.

Results are saved in data/fund_map.json. If a match is wrong, put the right
AMFI code (or Groww link) in your Excel and it will be used instead.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from rapidfuzz import fuzz, process

from . import sources

AMFI_NAV_ALL = ["https://portal.amfiindia.com/spages/NAVAll.txt",
                "https://www.amfiindia.com/spages/NAVAll.txt"]
MAP_VERSION = 2          # bump to re-check every saved match


def amfi_universe() -> list[dict]:
    """Active schemes from AMFI's daily NAV file: code, isin_growth, isin_reinv, name."""
    txt = ""
    for url in AMFI_NAV_ALL:
        try:
            r = sources._session.get(url, timeout=60)
            r.raise_for_status()
            if ";" in r.text[:5000]:
                txt = r.text
                break
        except Exception:
            continue
    if not txt:
        return [{"code": str(s["schemeCode"]), "name": s["schemeName"], "isin": None, "isin2": None}
                for s in sources.mfapi_all_schemes()]
    out = []
    for line in txt.splitlines():
        p = line.split(";")
        if len(p) >= 6 and p[0].strip().isdigit():
            out.append({"code": p[0].strip(), "isin": p[1].strip() if p[1].strip() not in ("-", "") else None,
                        "isin2": p[2].strip() if p[2].strip() not in ("-", "") else None,
                        "name": p[3].strip()})
    return out


def _clean(n: str) -> str:
    n = n.lower().replace("&", "and")
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _plan_bias(query: str, cand: str) -> float:
    q, c = _clean(query), _clean(cand)
    score = 0.0
    want_direct = "direct" in q or "regular" not in q          # most app investors hold Direct
    if want_direct and "direct" in c:
        score += 8
    if not want_direct and "regular" in c:
        score += 8
    want_idcw = any(w in q for w in ("idcw", "dividend", "payout"))
    if want_idcw == any(w in c for w in ("idcw", "dividend", "payout", "bonus")):
        score += 6
    if "growth" in c and not want_idcw:
        score += 3
    for bad in ("segregated", "unclaimed", "institutional", "bonus"):
        if bad in c and bad not in q:
            score -= 10
    return score


def match_name(name: str, universe: list[dict]) -> tuple[dict | None, float]:
    names = [_clean(u["name"]) for u in universe]
    top = process.extract(_clean(name), names, scorer=fuzz.token_set_ratio, limit=25)
    best, best_s = None, -1
    for _, s, idx in top:
        u = universe[idx]
        tot = s + _plan_bias(name, u["name"]) + 0.15 * fuzz.ratio(_clean(name), names[idx])
        if tot > best_s:
            best, best_s = u, tot
    return best, best_s


PLAN_WORDS = r"\b(direct|regular|plan|growth|option|idcw|dividend|payout|reinvestment|scheme|fund|g)\b"


def _core(n: str) -> str:
    """scheme name without plan/option words, so Regular and Direct plans compare equal"""
    return re.sub(r"\s+", " ", re.sub(PLAN_WORDS, " ", _clean(n))).strip()


def resolve_groww(scheme_name: str, isin: str | None, code: str | None, hint: str | None) -> str | None:
    """Groww lists Direct plans. Direct and Regular plans of a scheme hold the same portfolio,
    so for a Regular plan we use the Direct plan's page for holdings and factsheet numbers."""
    cands = []
    if hint:
        cands.append(sources.groww_slug_from_link(hint))
    core = _core(scheme_name)
    for r in sources.groww_search(core)[:6]:
        cands.append(r["search_id"])
    base = sources.slugify(core)
    for bb in dict.fromkeys([base, base.replace("-and-", "-"), base.replace("multi-cap", "multicap"),
                             base.replace("multicap", "multi-cap"), base.replace("smallcap", "small-cap"),
                             base.replace("midcap", "mid-cap")]):
        cands += [f"{bb}-fund-direct-growth", f"{bb}-direct-growth", f"{bb}-direct-plan-growth"]
    seen = set()
    for slug in cands:
        if not slug or slug in seen:
            continue
        seen.add(slug)
        j = sources.groww_scheme(slug)
        if not j:
            continue
        if hint and slug == cands[0]:
            return slug
        jisin = str(j.get("isin") or "")
        jcode = str(j.get("scheme_code") or "")
        if (isin and jisin == isin) or (code and jcode == code):
            return slug
        jn = _core(j.get("scheme_name", ""))
        if jn and (jn == core or fuzz.token_sort_ratio(jn, core) >= 90):
            return slug
    return None


def _isin_ok(code: str, isin: str) -> bool:
    """True if mfapi says this scheme code carries this ISIN and still has a recent NAV."""
    try:
        nav = sources.mfapi_nav(code)
    except RuntimeError:
        return False
    meta = nav["meta"]
    isins = {meta.get("isin_growth"), meta.get("isin_div_reinvestment"), meta.get("isin_div_payout")}
    if isin not in isins:
        return False
    return bool(nav["nav"]) and nav["nav"][-1][0] >= (date.today() - timedelta(days=15)).isoformat()


def _search_by_isin(isin: str, name: str) -> dict | None:
    """When AMFI's list isn't reachable: search mfapi by name and keep the code whose ISIN matches."""
    words = _core(name).split()
    tried = set()
    for q in (" ".join(words[:3]), " ".join(words[:2]), words[0] if words else ""):
        if not q or q in tried:
            continue
        tried.add(q)
        for s in sources.mfapi_search(q)[:40]:
            code = str(s.get("schemeCode"))
            if _isin_ok(code, isin):
                return {"code": code, "name": s.get("schemeName")}
    return None


def resolve_all(portfolio, map_path: Path, log) -> dict:
    fmap = json.loads(map_path.read_text()) if map_path.exists() else {}
    universe = None
    for h in portfolio.holdings:
        cur = fmap.get(h.fund, {})
        if cur.get("v") != MAP_VERSION or (h.isin and cur.get("isin_given") != h.isin):
            cur = {}                      # re-check old or changed matches
        cur["v"] = MAP_VERSION
        cur["isin_given"] = h.isin
        # user-supplied identifiers always win
        if h.code:
            cur.update(code=h.code, method="from your Excel (AMFI code)", score=100)
        if h.isin and not h.code:
            universe = universe or amfi_universe()
            u = next((x for x in universe if h.isin in (x["isin"], x["isin2"])), None)
            if u:
                cur.update(code=u["code"], scheme_name=u["name"], isin=h.isin, method="ISIN (AMFI list)", score=100)
            else:
                u = _search_by_isin(h.isin, h.fund)
                if u:
                    cur.update(code=u["code"], scheme_name=u["name"], isin=h.isin, method="ISIN (mfapi check)", score=100)
                else:
                    cur.update(code=None, isin=h.isin, scheme_name=None, method="ISIN not found; using ISIN NAV backup",
                               score=None, nav_by_isin=True)
                    log(f"'{h.fund}': ISIN {h.isin} not found in AMFI's list; NAV taken from the ISIN backup source.")
        if not cur.get("code") and not cur.get("nav_by_isin"):
            universe = universe or amfi_universe()
            u, s = match_name(h.fund, universe)
            if u:
                cur.update(code=u["code"], scheme_name=u["name"], isin=u["isin"], method="name match",
                           score=round(min(s, 120), 1))
                if s < 95:
                    log(f"Check the match for '{h.fund}' -> '{u['name']}' (score {s:.0f}). "
                        f"Add its AMFI code to your Excel if it's wrong.")
            else:
                log(f"Couldn't match '{h.fund}' to any AMFI scheme.")
                fmap[h.fund] = cur
                continue
        if cur.get("code") and (not cur.get("scheme_name") or not cur.get("isin")):
            try:
                meta = sources.mfapi_nav(cur["code"])["meta"]
                cur.setdefault("scheme_name", meta.get("scheme_name"))
                cur["isin"] = cur.get("isin") or meta.get("isin_growth")
            except RuntimeError:
                pass
        if h.groww and sources.groww_slug_from_link(h.groww) != cur.get("groww_slug"):
            cur["groww_slug"] = None
        if not cur.get("groww_slug"):
            cur["groww_slug"] = resolve_groww(cur.get("scheme_name") or h.fund, cur.get("isin"), cur.get("code"), h.groww)
            if not cur["groww_slug"] and cur.get("scheme_name") and _core(cur["scheme_name"]) != _core(h.fund):
                cur["groww_slug"] = resolve_groww(h.fund, cur.get("isin"), cur.get("code"), None)  # old name
            if not cur["groww_slug"]:
                log(f"No Groww page found for '{h.fund}'. Holdings for it will come from Kuvera/AMC only; "
                    f"paste its Groww link into your Excel to fix.")
        fmap[h.fund] = cur
    # drop funds you no longer hold from the map
    fmap = {k: v for k, v in fmap.items() if k in {h.fund for h in portfolio.holdings}}
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(json.dumps(fmap, indent=2, default=str))
    return fmap
