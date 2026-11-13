"""
Data fixtures and golden snapshots that back the Reporting Agent with real engine numbers.

1. Fixtures are valid, deterministic, and survive the CSV round trip unchanged.
2. Each scenario still produces the situation it was designed for (so the snapshots stay meaningful).
3. Golden snapshots: tools -> agents -> report output equals tests/snapshots/reporting/<id>.json.
4. Every figure in a report traces back to an engine number (nothing invented or altered).

After an INTENDED change to the engine or an agent, refresh the snapshots and review the diff:
    python -m scripts.report_snapshots --update        (or UPDATE_SNAPSHOTS=1 pytest ...)
"""

import json
import os
import re
from datetime import date

import pytest

from finance_engine.anomalies import detect_expense_anomalies
from finance_engine.calculations import calculate_overdue_invoices, financial_summary
from finance_engine.data import FinanceData, load_business
from finance_engine.fixtures import AS_OF, FIXTURE_IDS, SCENARIOS, build_scenario, write_fixtures
from finance_engine.forecasting import forecast_cash_flow
from finance_engine.periods import resolve_period
from scripts.report_snapshots import (
    CADENCES,
    build_snapshot,
    diff_paths,
    fixture_environment,
    snapshot_path,
    to_text,
)

ALL_IDS = list(SCENARIOS)
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"


@pytest.fixture(scope="module")
def processed_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixtures")
    write_fixtures(root)
    return root


@pytest.fixture(scope="module")
def snapshots(processed_root):
    """Freshly computed snapshots for every scenario (computed once per test run)."""
    with fixture_environment(processed_root):
        return {biz: build_snapshot(biz) for biz in ALL_IDS}


def _engine(data):
    """Raw engine results for a FinanceData, JSON-safe, for equality checks."""
    month = resolve_period("current_month", AS_OF)
    return {
        "summary": financial_summary(data, month).model_dump(mode="json"),
        "forecast": forecast_cash_flow(data, AS_OF, 30).model_dump(mode="json"),
        "overdue": calculate_overdue_invoices(data, AS_OF).model_dump(mode="json"),
        "anomalies": detect_expense_anomalies(data, date(2026, 11, 1), AS_OF).model_dump(mode="json"),
    }


# ============================================================================== #
# 1. fixtures: valid, deterministic, CSV round trip
# ============================================================================== #

@pytest.mark.parametrize("biz", FIXTURE_IDS)
def test_fixture_is_valid_single_tenant_data(biz):
    datasets = build_scenario(biz)
    data = FinanceData.from_datasets(datasets, biz)
    assert data.business_id == biz and data.base_currency == "INR"
    for entity, rows in datasets.items():
        assert rows or entity in ("expenses",), f"{biz}: no {entity}"
        assert all(r.business_id == biz for r in rows)
        key = {"accounts": "account_id", "counterparties": "counterparty_id", "transactions": "transaction_id",
               "invoices": "invoice_id", "expenses": "expense_id"}.get(entity)
        ids = [getattr(r, key) for r in rows] if key else []
        assert len(ids) == len(set(ids)), f"{biz}: duplicate ids in {entity}"
    # every invoice payment and paid bill points at a real record
    txn_ids = {t.transaction_id for t in data.transactions}
    inv_ids = {i.invoice_id for i in data.invoices}
    party_ids = {c.counterparty_id for c in data.counterparties}
    assert all(t.invoice_id in inv_ids for t in data.transactions if t.invoice_id)
    assert all(e.transaction_id in txn_ids for e in data.expenses if e.transaction_id)
    assert all(i.customer_id in party_ids for i in data.invoices)
    assert all(e.vendor_id in party_ids for e in data.expenses if e.vendor_id)
    # nothing after the demo date
    assert max(t.transaction_date for t in data.transactions) <= AS_OF


@pytest.mark.parametrize("biz", FIXTURE_IDS)
def test_fixture_builders_are_deterministic(biz, tmp_path):
    assert build_scenario(biz) == build_scenario(biz)
    write_fixtures(tmp_path / "a", [biz])
    write_fixtures(tmp_path / "b", [biz])
    files = sorted(p.name for p in (tmp_path / "a" / biz).iterdir())
    assert files and all((tmp_path / "a" / biz / f).read_bytes() == (tmp_path / "b" / biz / f).read_bytes()
                         for f in files)


@pytest.mark.parametrize("biz", FIXTURE_IDS)
def test_csv_round_trip_gives_identical_engine_results(biz, processed_root):
    in_memory = FinanceData.from_datasets(build_scenario(biz), biz)
    from_csv = load_business(biz, processed_dir=processed_root, auto_build=False)
    assert _engine(from_csv) == _engine(in_memory)


def test_unknown_fixture_is_rejected():
    with pytest.raises(KeyError):
        build_scenario("biz_001")          # the sample has no builder: it comes from data/samples
    with pytest.raises(KeyError):
        build_scenario("biz_nope")


# ============================================================================== #
# 2. scenario intent (if one of these fails, the fixture no longer tests what it claims)
# ============================================================================== #

def _tools(snapshots, biz, cadence="daily"):
    return snapshots[biz][cadence]["tools"]


def test_steady_is_healthy(snapshots):
    t = _tools(snapshots, "biz_fx_steady")
    assert t["forecast"]["risk_level"] == "low" and t["forecast"]["projected_balance"] > t["financial_summary"]["cash_balance"]
    assert t["overdue_invoices"]["count"] == 0 and t["expense_anomalies"]["anomalies"] == []
    assert t["financial_summary"]["warnings"] == [] and t["forecast"]["data_coverage"] == 1.0
    monthly = _tools(snapshots, "biz_fx_steady", "monthly")["financial_summary"]
    assert monthly["net_cash_flow"] > 0


def test_crunch_is_high_risk_with_every_overdue_band(snapshots):
    t = _tools(snapshots, "biz_fx_crunch")
    assert t["forecast"]["risk_level"] == "high"
    engine_fc = forecast_cash_flow(FinanceData.from_datasets(build_scenario("biz_fx_crunch"), "biz_fx_crunch"), AS_OF, 30)
    assert 0 < engine_fc.runway_days < 30                       # runs out of cash inside the horizon
    assert 0 < t["financial_summary"]["cash_balance"] < t["overdue_invoices"]["total_amount"]
    assert [b["count"] for b in t["overdue_invoices"]["ageing_bands"]] == [1, 1, 1]
    assert snapshots["biz_fx_crunch"]["daily"]["report"]["requires_approval"] is True


def test_anomaly_scenario_trips_the_intended_rules(snapshots):
    anomalies = _tools(snapshots, "biz_fx_anomaly")["expense_anomalies"]["anomalies"]
    rules = sorted(a["rule"] for a in anomalies)
    assert len(anomalies) == 4
    assert [a["severity"] for a in anomalies] == ["high", "high", "medium", "low"]
    assert {a["vendor"] for a in anomalies} >= {"Amazon Web Services", "Orange Realty LLP"}
    assert all(a["evidence_refs"] for a in anomalies) and rules == sorted(rules)
    # October had none: the monthly report must not inherit November's anomalies
    assert _tools(snapshots, "biz_fx_anomaly", "monthly")["expense_anomalies"]["anomalies"] == []


def test_thin_data_is_flagged(snapshots):
    fc = _tools(snapshots, "biz_fx_thin")["forecast"]
    assert fc["risk_level"] == "medium" and fc["data_coverage"] < 0.5
    caveats = snapshots["biz_fx_thin"]["daily"]["report"]["caveats"]
    assert any("Only 13 day(s) of bank history" in c for c in caveats)


def test_scenarios_cover_every_risk_level(snapshots):
    levels = {_tools(snapshots, b)["forecast"]["risk_level"] for b in ALL_IDS}
    assert levels == {"low", "medium", "high"}


# ============================================================================== #
# 3. golden snapshots
# ============================================================================== #

@pytest.mark.parametrize("biz", ALL_IDS)
def test_snapshot_matches_golden_file(biz, snapshots):
    path = snapshot_path(biz)
    actual = snapshots[biz]
    if UPDATE or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_text(actual), encoding="utf-8", newline="\n")
        if not UPDATE:
            pytest.fail(f"snapshot {path.name} was missing and has been written; review and commit it.")
        return
    expected = json.loads(path.read_text(encoding="utf-8"))
    changes = diff_paths(expected, actual)
    assert not changes, (f"{biz} report output changed:\n  " + "\n  ".join(changes) +
                         "\nIf intended: python -m scripts.report_snapshots --update")


def test_snapshots_are_reproducible(processed_root, snapshots):
    with fixture_environment(processed_root):
        again = build_snapshot("biz_fx_crunch")
    assert to_text(again) == to_text(snapshots["biz_fx_crunch"])


def test_snapshot_files_have_no_machine_specific_content():
    for biz in ALL_IDS:
        text = snapshot_path(biz).read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:\\\\|/tmp/|/home/|Users[\\/]", text), f"{biz}: local path in snapshot"
        assert "sk-" not in text


def test_fixture_environment_restores_settings(processed_root):
    from config.settings import settings
    before = (settings.USE_REAL_FINANCE_TOOLS, settings.OPENAI_API_KEY, settings.PROCESSED_DATA_DIR)
    with fixture_environment(processed_root):
        assert settings.USE_REAL_FINANCE_TOOLS is True and settings.OPENAI_API_KEY == ""
    assert (settings.USE_REAL_FINANCE_TOOLS, settings.OPENAI_API_KEY, settings.PROCESSED_DATA_DIR) == before


# ============================================================================== #
# 4. the report is backed by the engine: every figure traces back to a tool number
# ============================================================================== #

def _inr(x: float) -> str:
    return f"-₹{abs(x):,.2f}" if x < 0 else f"₹{x:,.2f}"


def _numbers(obj):
    """Every number anywhere in a JSON structure."""
    if isinstance(obj, bool):
        return set()
    if isinstance(obj, (int, float)):
        return {round(float(obj), 2)}
    if isinstance(obj, dict):
        return set().union(*map(_numbers, obj.values())) if obj else set()
    if isinstance(obj, list):
        return set().union(*map(_numbers, obj)) if obj else set()
    return set()


@pytest.mark.parametrize("cadence", list(CADENCES))
@pytest.mark.parametrize("biz", ALL_IDS)
def test_report_evidence_equals_engine_numbers(snapshots, biz, cadence):
    run = snapshots[biz][cadence]
    t, report = run["tools"], run["report"]
    evidence = {e["label"]: (e["value"], e["source"]) for e in report["evidence"]}
    s, fc, od = t["financial_summary"], t["forecast"], t["overdue_invoices"]
    assert evidence["Cash Balance"] == (_inr(s["cash_balance"]), "cashflow")
    assert evidence["Net Cash Flow"] == (_inr(s["net_cash_flow"]), "cashflow")
    assert evidence["Projected Cash Balance"] == (_inr(fc["projected_balance"]), "cashflow")
    assert evidence["Liquidity Risk Level"] == (fc["risk_level"].title(), "cashflow")
    assert evidence["Overdue Invoices Total"] == (_inr(od["total_amount"]), "invoice")
    assert evidence["Overdue Invoices Count"] == (str(od["count"]), "invoice")
    assert evidence["Expense Anomalies Count"] == (str(len(t["expense_anomalies"]["anomalies"])), "expense")
    # report metrics are the tool numbers, unchanged
    m = report["metrics"]
    assert (m["cash_balance"], m["inflows"], m["outflows"]) == (s["cash_balance"], s["inflows"], s["outflows"])
    assert (m["projected_balance"], m["count"], m["total_amount"]) == (fc["projected_balance"], od["count"],
                                                                       od["total_amount"])
    assert report["missing_parts"] == [] and report["currency"] == "INR"


@pytest.mark.parametrize("cadence", list(CADENCES))
@pytest.mark.parametrize("biz", ALL_IDS)
def test_report_summary_invents_no_numbers(snapshots, biz, cadence):
    run = snapshots[biz][cadence]
    tools = run["tools"]
    counts = {float(len(tools["expense_anomalies"]["anomalies"])), float(tools["overdue_invoices"]["count"])}
    allowed = _numbers(tools) | counts | {30.0}                 # 30 = the forecast horizon in days
    for raw in re.findall(r"-?\d[\d,]*(?:\.\d+)?", run["report"]["summary"]):
        value = round(float(raw.replace(",", "")), 2)
        assert value in allowed or -value in allowed, f"{biz}/{cadence}: {raw} is not an engine number"


@pytest.mark.parametrize("biz", ALL_IDS)
def test_report_caveats_are_engine_warnings_verbatim(snapshots, biz):
    for cadence in CADENCES:
        run = snapshots[biz][cadence]
        warnings = {w for tool in run["tools"].values() for w in tool.get("warnings", [])}
        assert set(run["report"]["caveats"]) <= warnings


def test_daily_and_monthly_use_different_periods(snapshots):
    for biz in ALL_IDS:
        d = snapshots[biz]["daily"]["tools"]["financial_summary"]
        m = snapshots[biz]["monthly"]["tools"]["financial_summary"]
        assert d["period"] == "current_month (2026-11-01 to 2026-11-05)"
        assert m["period"] == "last_month (2026-10-01 to 2026-10-31)"
        assert snapshots[biz]["monthly"]["report"]["report_type"] == "monthly"
