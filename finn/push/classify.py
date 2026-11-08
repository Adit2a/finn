"""Rules-first classification + materiality scoring of exchange filings.

Philosophy: a regex costs nothing and is right ~90% of the time on NSE's own
subject lines. The model is only consulted (see enrich.py) for filings whose
subject is opaque AND whose category could plausibly move the stock.
"""
from __future__ import annotations

import re

CATEGORIES = ["results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
              "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
              "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified"]

LABELS = {"results": "Results", "M&A": "M&A", "fund_raise": "Fund raise",
          "management_change": "Management", "litigation": "Legal / regulatory",
          "insider_trading": "Insider trade", "earnings_call": "Earnings call",
          "investor_meetings": "Investor meet", "credit_rating": "Credit rating",
          "dividend": "Dividend", "bonus_split": "Bonus / split", "capex": "Capex",
          "press_release": "Press release", "postal_ballot": "Postal ballot", "agm_egm": "AGM / EGM",
          "pledging": "Pledge", "unclassified": "Routine"}

# NSE subject line -> category (exact match, lower-cased)
SUBJECT = {
    "outcome of board meeting": "results", "financial result updates": "results",
    "integrated filing- financials": "results", "investor presentation": "results",
    "acquisition": "M&A", "sale or disposal": "M&A", "other restructuring": "M&A", "amalgamation/ merger": "M&A",
    "scheme of arrangement": "M&A", "joint venture": "M&A",
    "issue of securities": "fund_raise", "buyback": "fund_raise", "qualified institutional placement": "fund_raise",
    "preferential issue": "fund_raise", "rights issue": "fund_raise",
    "change in management": "management_change", "resignation of director/kmp/smp": "management_change",
    "appointment": "management_change", "change in director(s)": "management_change",
    "cessation": "management_change", "resignation": "management_change", "retirement": "management_change",
    "change in directorate": "management_change", "re-appointment": "management_change",
    "corporate insolvency resolution process": "litigation", "action(s) taken or orders passed": "litigation",
    "litigation/dispute": "litigation", "action(s) initiated or orders passed": "litigation",
    "fraud/default/arrest": "litigation", "penalty": "litigation",
    "disclosure under sebi takeover regulations": "insider_trading", "insider trading": "insider_trading",
    "analysts/institutional investor meet/con. call updates": "investor_meetings",
    "credit rating": "credit_rating", "credit rating- new": "credit_rating", "credit rating- others": "credit_rating",
    "credit rating- revision": "credit_rating",
    "dividend": "dividend", "record date": "dividend", "bonus": "bonus_split", "stock split": "bonus_split",
    "sub-division": "bonus_split",
    "capacity addition": "capex", "commencement of commercial production": "capex", "capex": "capex",
    "press release": "press_release", "bagging/receiving of orders/contracts": "press_release",
    "news verification": "press_release", "rumour verification - regulation 30(11)": "press_release",
    "postal ballot": "postal_ballot", "shareholders meeting": "agm_egm", "agm/egm": "agm_egm",
    "pledge": "pledging", "creation of pledge": "pledging", "release of pledge": "pledging",
    "copy of newspaper publication": "unclassified", "trading window": "unclassified",
    "esop/esos/esps": "unclassified", "allotment of securities": "fund_raise",
    "change in auditors": "unclassified", "monitoring agency report": "unclassified",
    "committee meeting updates": "unclassified", "amendment to aoa/moa": "unclassified",
    "loss of share certificates": "unclassified", "duplicate share certificate": "unclassified",
    "certificate under sebi (depositories and participants) regulations, 2018": "unclassified",
    "compliances-certificate under reg. 74 (5) of sebi (dp) regulations, 2018": "unclassified",
    "general updates": None, "updates": None,
}

R = lambda p: re.compile(p, re.I)
TEXT_RULES = [  # applied to subject+text when subject is generic, first match wins
    (R(r"newspaper|clipping|advertisement|publication"), "unclassified"),
    (R(r"trading window|loss of share cert|duplicate share|reg(ulation)?\.? ?74|auditor|"
       r"secretarial|scrutini[sz]er"), "unclassified"),
    (R(r"transcript|audio recording|link of recording|webcast"), "earnings_call"),
    (R(r"financial results|unaudited|audited results"), "results"),
    (R(r"\bipo\b|initial public offer|demerger|merger|amalgamat|acqui|stake|joint venture|divest"), "M&A"),
    (R(r"managing director|\bceo\b|\bcfo\b|chief executive|chief financial|whole[- ]time director|"
       r"\bkmp\b|key managerial|resign|appoint|cessation|retire"), "management_change"),
    (R(r"nclt|nclat|insolvency|penalty|show cause|order passed|court|tribunal|\bgst\b demand|"
       r"tax demand|search|raid|fraud"), "litigation"),
    (R(r"credit rating|rating (agency|action)|reaffirm|crisil|icra|care ratings|india ratings"), "credit_rating"),
    (R(r"qip|qualified institution|preferential|rights issue|buyback|fund rais"), "fund_raise"),
    (R(r"dividend|record date"), "dividend"),
    (R(r"bonus|split|sub-division"), "bonus_split"),
    (R(r"capacity|new plant|greenfield|brownfield|capex|commercial production|commission"), "capex"),
    (R(r"pledge"), "pledging"),
    (R(r"analyst|investor meet|investors'? meet|conference|roadshow"), "investor_meetings"),
    (R(r"press release|media (release|statement)|order|contract|launch|approval|partnership|clarification"),
     "press_release"),
]

TOP_ROLE = R(r"managing director|\bmd\b|\bceo\b|\bcfo\b|chief executive|chief financial|chairman|chairperson|"
             r"whole[- ]time director|executive director|\bcoo\b|chief operating")


def classify(item: dict) -> str:
    subj = item["subject"].lower().strip()
    blob = f"{item['subject']} {item['text']}"
    cat = SUBJECT.get(subj, "?")
    if subj == "analysts/institutional investor meet/con. call updates":
        if re.search(r"transcript|recording|audio|webcast", blob, re.I):
            return "earnings_call"
        if re.search(r"board.*(financial results|results)", blob, re.I):
            return "earnings_call"  # results date intimation: call schedule, low priority
        return "investor_meetings"
    if subj == "outcome of board meeting" and not re.search(r"result", blob, re.I):
        cat = None  # board outcome about something else: fall through to text rules
    if subj == "allotment of securities" and re.search(r"esop|esps|esos|non convertible|ncd|commercial paper", blob, re.I):
        return "unclassified"
    if cat == "management_change" and re.search(r"auditor|secretarial|scrutini", blob, re.I):
        return "unclassified"
    if cat not in (None, "?"):
        return cat
    for rx, c in TEXT_RULES:
        if rx.search(blob):
            return c
    return "unclassified"


def _amount_cr(text: str) -> float | None:
    """Largest ₹ amount mentioned, in crore."""
    best = None
    # a unit is required: a bare "₹1,119" is usually a per-share price, not a deal size
    for m in re.finditer(r"(?:rs\.?|inr|₹)\s*(\d[\d,]*(?:\.\d+)?)\s*(crores?|cr\b|lakhs?|million|mn\b|billion|bn\b)",
                         text, re.I):
        v = float(m.group(1).replace(",", ""))
        unit = m.group(2).lower().rstrip("s")
        v = {"lakh": v / 100, "million": v / 10, "mn": v / 10, "billion": v * 100, "bn": v * 100}.get(unit, v)
        best = max(best or 0, v)
    return best


BASE = {"results": 88, "M&A": 72, "fund_raise": 70, "management_change": 55, "litigation": 66,
        "insider_trading": 45, "earnings_call": 30, "investor_meetings": 8, "credit_rating": 30,
        "dividend": 34, "bonus_split": 55, "capex": 58, "press_release": 32, "postal_ballot": 18,
        "agm_egm": 14, "pledging": 65, "unclassified": 4}


def score(item: dict, cat: str, mcap_cr: float | None) -> tuple[int, list[str]]:
    """Materiality 0-100 plus the human-readable reasons behind it."""
    blob = f"{item['subject']} {item['text']} {item.get('summary') or ''}"
    s, why = BASE[cat], []
    if cat == "results":
        if re.search(r"intimation|scheduled|will be held|to consider", blob, re.I) and \
                not re.search(r"approved|submitted|attached", blob, re.I):
            s, why = 20, ["results date notice"]
        elif re.search(r"newspaper|presentation|press release|media release", blob, re.I):
            s, why = 40, ["supporting results document"]
    elif cat == "management_change":
        if TOP_ROLE.search(blob):
            s += 30; why.append("top-level role")
        elif re.search(r"independent director|non[- ]executive", blob, re.I):
            s -= 15; why.append("board seat, not management")
        if re.search(r"resign|cessation|step(s|ped)? down", blob, re.I):
            s += 8; why.append("departure")
    elif cat == "credit_rating":
        if re.search(r"downgrad|negative|watch with negative", blob, re.I):
            s = 80; why.append("downgrade / negative outlook")
        elif re.search(r"upgrad|positive", blob, re.I):
            s = 55; why.append("upgrade")
        else:
            s = 20; why.append("reaffirmation")
    elif cat == "M&A" and re.search(r"incorporat\w* .{0,60}subsidiary|subsidiary .{0,40}incorporat", blob, re.I) \
            and not re.search(r"acqui|stake|merger", item["text"], re.I):
        s = 22; why.append("new subsidiary set up (housekeeping)")
    elif cat == "pledging" and re.search(r"release|revoc", blob, re.I):
        s = 30; why.append("pledge released")
    elif cat == "fund_raise" and re.search(r"non convertible|ncd|commercial paper|debenture", blob, re.I):
        s = 15; why.append("routine debt issuance")
    elif cat == "earnings_call" and re.search(r"transcript", blob, re.I):
        s = 40; why.append("transcript: feeds research")
    elif cat == "litigation" and re.search(r"set(ting)? aside|dismiss|in favou?r|stay granted", blob, re.I):
        s -= 8; why.append("ruling in company's favour")

    # if the model read the filing, trust its amount (or its judgement that there is none)
    amt = item.get("amount_cr") if item.get("read_by") else _amount_cr(blob)
    if amt and mcap_cr and cat in ("press_release", "litigation", "M&A", "capex", "fund_raise"):
        pct = amt / mcap_cr * 100
        ptxt = "<0.01%" if pct < 0.01 else f"{pct:.2f}%" if pct < 0.5 else f"{pct:.1f}%"
        if pct >= 2:
            s += 25; why.append(f"₹{amt:,.0f} cr ≈ {ptxt} of market cap")
        elif pct >= 0.5:
            s += 12; why.append(f"₹{amt:,.0f} cr ≈ {ptxt} of market cap")
        else:
            s -= 5; why.append(f"₹{amt:,.0f} cr, small vs size ({ptxt} of m-cap)")
    if cat == "press_release" and re.search(r"order|contract", blob, re.I) and not amt:
        why.append("order win, value undisclosed")
    if item.get("hint") is not None:  # model's read of an opaque PDF nudges, never decides
        s = round(0.6 * s + 0.4 * item["hint"])
    return max(0, min(100, s)), why


def tier(s: int) -> str:
    return "act" if s >= 68 else "glance" if s >= 33 else "routine"
