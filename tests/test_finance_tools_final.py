"""
Day-6 tests: the finalised deterministic finance tools.

Covers what makes the four agent tools production-ready, on top of the per-algorithm tests:
zero-setup data loading (fresh clone), caching + invalidation, business_id safety, one error
hierarchy, input normalisation, determinism, JSON safety and thread safety.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from config.settings import settings
from finance_engine import data as data_module
from finance_engine import tools
from finance_engine.data import clear_cache, load_business, validate_business_id
from finance_engine.errors import BusinessNotFoundError, FinanceToolError, InvalidInputError, PeriodError
from finance_engine.etl import run_business, write_run_outputs

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
TODAY = date(2026, 11, 5)


@pytest.fixture(autouse=True)
def fresh_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def no_processed(tmp_path, monkeypatch):
    """Simulates a fresh clone: data/processed is empty (it is git-ignored)."""
    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(tmp_path / "empty"))
    return tmp_path


@pytest.fixture
def processed(tmp_path, monkeypatch):
    root = tmp_path / "processed"
    write_run_outputs(run_business(SAMPLES, today=TODAY), root / "biz_001")
    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(root))
    return root


# ------------------------------------------------------------------------------ #
# data loading
# ------------------------------------------------------------------------------ #

def test_fresh_clone_auto_builds_from_samples(no_processed):
    s = tools.get_financial_summary("biz_001", "last_month", today=TODAY)
    assert s.cash_balance == Decimal("39838.50")
    assert not (no_processed / "empty").exists()          # built in memory, nothing written


def test_auto_build_matches_processed_data(no_processed, monkeypatch, tmp_path):
    built = tools.get_financial_summary("biz_001", "last_month", today=TODAY)
    root = tmp_path / "p"
    write_run_outputs(run_business(SAMPLES, today=TODAY), root / "biz_001")
    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(root))
    from_files = tools.get_financial_summary("biz_001", "last_month", today=TODAY)
    assert built == from_files


def test_auto_build_can_be_disabled(no_processed):
    with pytest.raises(BusinessNotFoundError):
        load_business("biz_001", auto_build=False)


def test_cache_reuses_and_invalidates_on_file_change(processed):
    first = load_business("biz_001")
    assert load_business("biz_001") is first                 # cached
    csv = processed / "biz_001" / "transactions.csv"
    later = time.time() + 5
    os.utime(csv, (later, later))                            # file changed
    assert load_business("biz_001") is not first


def test_cache_is_per_folder(processed, monkeypatch, tmp_path):
    a = load_business("biz_001")
    other = tmp_path / "other"
    write_run_outputs(run_business(SAMPLES, today=TODAY), other / "biz_001")
    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(other))
    assert load_business("biz_001") is not a


# ------------------------------------------------------------------------------ #
# business_id safety and errors
# ------------------------------------------------------------------------------ #

@pytest.mark.parametrize("bad", ["", "   ", "../biz_001", "..\\x", "biz/001", "biz 001", "a" * 65, "-biz", None])
def test_unsafe_business_ids_rejected(bad, processed):
    with pytest.raises(InvalidInputError):
        validate_business_id(bad)
    with pytest.raises(InvalidInputError):
        tools.get_financial_summary(bad, today=TODAY)


def test_business_id_is_trimmed(processed):
    assert tools.get_overdue_invoices("  biz_001 ", today=TODAY).business_id == "biz_001"


def test_error_hierarchy_is_backward_compatible(processed):
    with pytest.raises(BusinessNotFoundError) as e1:
        tools.get_financial_summary("biz_404", today=TODAY)
    assert isinstance(e1.value, (FinanceToolError, LookupError)) and e1.value.code == "business_not_found"
    with pytest.raises(PeriodError) as e2:
        tools.get_financial_summary("biz_001", "next_decade", today=TODAY)
    assert isinstance(e2.value, (InvalidInputError, ValueError, FinanceToolError)) and e2.value.code == "invalid_period"
    from finance_engine.data import BusinessNotFoundError as OldImport
    from finance_engine.periods import PeriodError as OldPeriodImport
    assert OldImport is BusinessNotFoundError and OldPeriodImport is PeriodError


# ------------------------------------------------------------------------------ #
# input normalisation
# ------------------------------------------------------------------------------ #

@pytest.mark.parametrize("horizon, expected", [("30", 30), (60, 60), ("90.0", 90), (None, 30), ("", 30), (365, 365)])
def test_horizon_accepts_numeric_strings(processed, horizon, expected):
    assert tools.get_cash_flow_forecast("biz_001", horizon, today=TODAY).horizon_days == expected


@pytest.mark.parametrize("horizon", [0, -5, 366, "abc", 30.5, True])
def test_horizon_rejects_bad_values(processed, horizon):
    with pytest.raises(InvalidInputError):
        tools.get_cash_flow_forecast("biz_001", horizon, today=TODAY)


@pytest.mark.parametrize("today", [TODAY, "2026-11-05", datetime(2026, 11, 5, 18, 30), "2026-11-05T09:00:00"])
def test_today_accepts_dates_strings_and_datetimes(processed, today):
    r = tools.get_overdue_invoices("biz_001", today=today)
    assert r.as_of == TODAY and r.total_amount == Decimal("50300.00")


@pytest.mark.parametrize("call", [
    lambda: tools.get_overdue_invoices("biz_001", "05/11/2026"),
    lambda: tools.get_overdue_invoices("biz_001", "1999-12-31"),
    lambda: tools.get_expense_anomalies("biz_001", "2026-11-05", "2026-11-01"),
    lambda: tools.get_expense_anomalies("biz_001", "2024-01-01", "2026-11-01"),     # > 366 days
    lambda: tools.get_net_cash_flow("biz_001", "last_month", granularity="hour", today=TODAY),
    lambda: tools.get_overdue_report("biz_001", "2026-11-05", grace_days=-1),
])
def test_bad_inputs_raise_invalid_input(processed, call):
    with pytest.raises(InvalidInputError):
        call()


@pytest.mark.parametrize("period, start, end", [
    ("October 2026", "2026-10-01", "2026-10-31"),
    ("oct-2026", "2026-10-01", "2026-10-31"),
    ("october", "2026-10-01", "2026-10-31"),
    ("last 30 days", "2026-10-07", "2026-11-05"),
    ("Q3", "2026-10-01", "2026-11-05"),
])
def test_natural_period_labels(processed, period, start, end):
    p = tools.get_financial_summary("biz_001", period, today=TODAY).period
    assert (p.start.isoformat(), p.end.isoformat()) == (start, end)


def test_month_name_labels_agree_with_iso(processed):
    a = tools.get_financial_summary("biz_001", "October 2026", today=TODAY)
    b = tools.get_financial_summary("biz_001", "2026-10", today=TODAY)
    assert a.model_dump(exclude={"period"}) == b.model_dump(exclude={"period"})


# ------------------------------------------------------------------------------ #
# the four agent tools: contract, determinism, JSON, threads
# ------------------------------------------------------------------------------ #

def _agent_calls():
    from agents.cashflow import finance_tools as cf
    from agents.expense import finance_tools as ex
    from agents.invoice import finance_tools as inv
    return {
        "get_financial_summary": lambda: cf.get_financial_summary("biz_001", "last_month", today=TODAY),
        "forecast_cash_flow": lambda: cf.forecast_cash_flow("biz_001", 30, today=TODAY),
        "get_overdue_invoices": lambda: inv.get_overdue_invoices("biz_001", "2026-11-05", today=TODAY),
        "get_expense_anomalies": lambda: ex.get_expense_anomalies("biz_001", "2026-10-01", "2026-10-31", today=TODAY),
    }


@pytest.mark.parametrize("name", list(_agent_calls()))
def test_agent_tools_are_deterministic_and_json_safe(processed, name):
    call = _agent_calls()[name]
    first, second = call(), call()
    assert first == second
    payload = json.loads(json.dumps(first.model_dump(mode="json")))
    assert payload["currency"] == "INR" and isinstance(payload["warnings"], list)
    # CashFlowForecast (Member 4's schema) has no calculation_version field; the other three do
    assert payload.get("calculation_version", "finance-engine-1.1.0") == "finance-engine-1.1.0"


def test_agent_tool_floats_equal_engine_decimals(processed):
    from agents.cashflow import finance_tools as cf
    engine = tools.get_financial_summary("biz_001", "last_month", today=TODAY)
    agent = cf.get_financial_summary("biz_001", "last_month", today=TODAY)
    for f in ("inflows", "outflows", "net_cash_flow", "cash_balance", "receivables"):
        assert getattr(agent, f) == float(getattr(engine, f))
        assert getattr(engine, f) == getattr(engine, f).quantize(Decimal("0.01"))   # always 2 dp


def test_agent_tools_work_on_fresh_clone(no_processed):
    for name, call in _agent_calls().items():
        assert call().business_id == "biz_001", name


def test_agents_end_to_end_on_fresh_clone(no_processed, monkeypatch):
    """Real agents + real tools with no setup at all (no ETL run, no API key)."""
    from agents.cashflow.agent import cash_flow_agent
    from agents.invoice.agent import invoice_agent
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", True)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    cf = cash_flow_agent({"business_id": "biz_001", "period": "last_month", "today": TODAY})
    assert cf.metrics["cash_balance"] == 39838.5 and cf.metrics["currency"] == "INR"
    inv = invoice_agent({"business_id": "biz_001", "as_of_date": "2026-11-05", "today": TODAY})
    assert inv.metrics["total_amount"] == 50300.0


def test_tools_are_thread_safe(processed):
    calls = list(_agent_calls().values())
    expected = [c() for c in calls]
    clear_cache()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda i: calls[i % 4](), range(32)))
    for i, r in enumerate(results):
        assert r == expected[i % 4]


def test_unknown_business_through_every_agent_tool(processed):
    from agents.cashflow import finance_tools as cf
    from agents.expense import finance_tools as ex
    from agents.invoice import finance_tools as inv
    for call in (lambda: cf.get_financial_summary("biz_404"), lambda: cf.forecast_cash_flow("biz_404"),
                 lambda: inv.get_overdue_invoices("biz_404"), lambda: ex.get_expense_anomalies("biz_404")):
        with pytest.raises(BusinessNotFoundError):
            call()
