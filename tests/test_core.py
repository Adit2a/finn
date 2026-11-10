"""Offline tests for the parts of FINN that must never be wrong: the arithmetic and the rules.

No network and no model calls. Run:  python -m pytest -q
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finn import notify  # noqa: E402
from finn.pull import numbers as N  # noqa: E402
from finn.push import classify as C  # noqa: E402
from finn.sources import nse  # noqa: E402


def filing(subject, text="", **kw):
    return {"subject": subject, "text": text, **kw}


# ------------------------------------------------------------------ classification

def test_subject_line_decides_the_category():
    assert C.classify(filing("Acquisition", "Acquisition of 51% stake in XYZ Ltd")) == "M&A"
    assert C.classify(filing("Credit Rating", "ICRA reaffirmed AAA")) == "credit_rating"


def test_transcript_is_an_earnings_call_not_an_investor_meeting():
    subj = "Analysts/Institutional Investor Meet/Con. Call Updates"
    assert C.classify(filing(subj, "Transcript of the Q2 FY27 earnings call")) == "earnings_call"
    assert C.classify(filing(subj, "Meeting with ABC Mutual Fund on 12 Nov")) == "investor_meetings"


def test_auditor_appointment_is_not_a_management_change():
    assert C.classify(filing("Appointment", "Appointment of secretarial auditor for FY27")) == "unclassified"


def test_board_outcome_without_results_is_not_results():
    assert C.classify(filing("Outcome of Board Meeting", "Board approved the unaudited financial results")) == "results"
    assert C.classify(filing("Outcome of Board Meeting", "Board took note of general matters")) != "results"


# ------------------------------------------------------------------ amounts and materiality

def test_amount_needs_a_unit_so_share_prices_are_not_deal_sizes():
    assert C._amount_cr("issue price of Rs. 1,119 per share") is None
    assert C._amount_cr("order worth Rs 13,000 crore") == 13000
    assert C._amount_cr("consideration of INR 500 million") == 50
    assert C._amount_cr("investment of ₹2 billion and Rs 40 lakh") == 200


def test_results_date_notice_is_routine_but_results_are_not():
    notice = filing("Outcome of Board Meeting", "Board meeting scheduled to consider financial results")
    s, why = C.score(notice, "results", 100000)
    assert C.tier(s) == "routine" and why == ["results date notice"]
    s, _ = C.score(filing("Outcome of Board Meeting", "Board approved the financial results"), "results", 100000)
    assert C.tier(s) == "act"


def test_a_departing_md_outranks_a_new_independent_director():
    md, _ = C.score(filing("Resignation", "Resignation of the Managing Director"), "management_change", None)
    ind, _ = C.score(filing("Appointment", "Appointment of an Independent Director"), "management_change", None)
    assert C.tier(md) == "act" and md > ind


def test_deal_size_is_judged_against_market_cap():
    item = filing("Acquisition", "Acquisition for Rs 13,000 crore")
    big, why = C.score(item, "M&A", 400000)      # 3.25% of market cap
    small, _ = C.score(item, "M&A", 20000000)    # 0.065%
    assert big > small and "of market cap" in why[-1]


def test_debt_issuance_and_rating_reaffirmation_stay_quiet_but_a_downgrade_does_not():
    s, _ = C.score(filing("Issue of Securities", "Allotment of non convertible debentures"), "fund_raise", None)
    assert C.tier(s) == "routine"
    s, _ = C.score(filing("Credit Rating", "Rating reaffirmed at AA+"), "credit_rating", None)
    assert C.tier(s) == "routine"
    s, _ = C.score(filing("Credit Rating", "Rating downgraded to A"), "credit_rating", None)
    assert C.tier(s) == "act"


def test_score_stays_within_bounds():
    s, _ = C.score(filing("Resignation", "Resignation of CEO", hint=100), "management_change", None)
    assert 0 <= s <= 100


# ------------------------------------------------------------------ results from XBRL

def xbrl(ctx, **tags):
    return "".join(f'<in-bse-fin:{k} contextRef="{ctx}" unitRef="INR" decimals="-5">{v}</in-bse-fin:{k}>'
                   for k, v in tags.items())


def test_ebitda_is_pbt_plus_finance_costs_plus_depreciation_less_exceptionals():
    x = xbrl("Q", RevenueFromOperations=1000e7, ProfitBeforeTax=150e7, ProfitLossForPeriod=110e7,
             FinanceCosts=20e7, DepreciationDepletionAndAmortisationExpense=30e7, ExceptionalItemsBeforeTax=10e7)
    n = nse._numbers(x, "Q")
    assert n["kind"] == "corporate"
    assert (n["revenue"], n["ebitda"], n["pbt"], n["pat"]) == (1000, 190, 150, 110)


def test_lenders_get_no_ebitda_because_interest_is_their_cost_of_goods():
    x = xbrl("Q", RevenueFromOperations=1000e7, ProfitBeforeTax=200e7, ProfitLossForPeriod=150e7, FinanceCosts=450e7)
    n = nse._numbers(x, "Q")
    assert n["kind"] == "lender" and n["ebitda"] is None


def test_banks_report_pre_provision_operating_profit():
    x = xbrl("Q", InterestEarned=5000e7, OperatingProfitBeforeProvisionAndContingencies=1800e7,
             ProfitLossFromOrdinaryActivitiesBeforeTax=1500e7, ProfitLossForThePeriod=1100e7)
    n = nse._numbers(x, "Q")
    assert n["kind"] == "bank" and n["ebitda"] == 1800 and n["pat"] == 1100


def test_the_three_month_context_is_chosen_over_year_to_date_and_segments():
    def ctx(cid, start, end, extra=""):
        return (f'<xbrli:context id="{cid}"><xbrli:entity>{extra}</xbrli:entity><xbrli:period>'
                f'<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>'
                f'</xbrli:period></xbrli:context>')
    xml = (ctx("YTD", "2026-04-01", "2026-09-30") + ctx("Seg", "2026-07-01", "2026-09-30", "<xbrli:segment/>")
           + ctx("Quarter", "2026-07-01", "2026-09-30") + ctx("Prev", "2026-04-01", "2026-06-30"))
    assert nse._quarter_context(xml, dt.date(2026, 9, 30)) == "Quarter"


def test_indian_fiscal_quarters():
    assert nse.fy_quarter(dt.date(2026, 9, 30)) == "Q2 FY27"
    assert nse.fy_quarter(dt.date(2027, 3, 31)) == "Q4 FY27"
    assert nse._prev_quarter_end(dt.date(2026, 6, 30), 1) == dt.date(2026, 3, 31)
    assert nse._prev_quarter_end(dt.date(2026, 9, 30), 4) == dt.date(2025, 9, 30)
    assert N.call_quarter("Oct 2026") == "Q2 FY27"   # an October call discusses July-September
    assert N.call_quarter("May 2026") == "Q4 FY26"


def test_growth_maths_refuses_meaningless_inputs():
    assert N.cagr(100, 200, 3) == 26.0
    assert N.cagr(-50, 200, 3) is None and N.cagr(100, 200, 0) is None
    assert N._pct(100, 114) == 14.0 and N._pct(-100, -50) == 50.0 and N._pct(0, 5) is None
    assert N.months_ago("Oct 2026", dt.date(2026, 11, 10)) == 1
    assert N.months_ago("Aug 2023", dt.date(2026, 11, 10)) == 39   # far outside the 15-month window


# ------------------------------------------------------------------ alerts

def series(closes, vols=None):
    start = dt.date(2026, 10, 1)
    vols = vols or [100] * len(closes)
    return [{"d": (start + dt.timedelta(days=i)).isoformat(), "c": c, "v": v} for i, (c, v) in enumerate(zip(closes, vols))]


RULES = {"price_move_pct": 4.0, "volume_multiple": 2.0, "volume_lookback_days": 3, "categories": ["results"]}


def test_price_and_volume_thresholds():
    h = [{"symbol": "AAA", "series": series([100, 100, 100, 100, 105, 105], [100, 100, 100, 100, 100, 300])}]
    hits = notify.price_alerts(h, RULES, dt.datetime(2026, 9, 1))
    assert [(a["date"], round(a["ret"], 1), a["vol"]) for a in hits] == [("2026-10-05", 5.0, False), ("2026-10-06", 0.0, True)]


def test_the_boundary_close_belongs_to_the_previous_session():
    h = [{"symbol": "AAA", "series": series([100, 100, 100, 100, 105, 111])}]
    since = dt.datetime.fromisoformat("2026-10-05T15:30:00")
    assert [a["date"] for a in notify.price_alerts(h, RULES, since)] == ["2026-10-06"]


def test_each_alert_is_sent_once(monkeypatch):
    monkeypatch.setenv("FINN_PUBLIC_URL", "")
    feed = {"generated_at": "2026-10-06T17:00:00", "rules": RULES, "upcoming": [],
            "holdings": [{"symbol": "AAA", "series": series([100, 100, 100, 100, 100, 106])},
                         {"symbol": "BBB", "series": series([100, 100, 100, 100, 100, 110])}],
            "events": [{"id": "e1", "symbol": "AAA", "category": "results", "score": 90, "ts": "2026-10-06T16:10:00",
                        "headline": "Q2 FY27: revenue +10% YoY"},
                       {"id": "e2", "symbol": "AAA", "category": "dividend", "score": 90, "ts": "2026-10-06T16:10:00",
                        "headline": "not a category this person asked for"}]}
    text, ids = notify.digest(feed, ["AAA"], {}, set())
    assert "AAA" in text and "BBB" not in text and "revenue +10%" in text
    assert set(ids) == {"px-AAA-2026-10-06", "e1"}
    assert notify.digest(feed, ["AAA"], {}, set(ids)) == (None, [])
