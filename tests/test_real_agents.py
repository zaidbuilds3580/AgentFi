"""
Tests for Day 6 integration: connecting Member 3's real finance tools to Cash Flow,
Invoice, and Expense agents via tool_source and config.settings.USE_REAL_FINANCE_TOOLS.

Verifies:
1. Every metric in each agent's result equals real tool output exactly (inflows, outflows,
   net_cash_flow, cash_balance, receivables, projected_balance, count, total_amount, anomalies).
2. Safe failure: unknown business_id gives a limitation message with no numbers.
3. Clean empty anomaly handling ("none found").
4. Tool warnings appear in caveats unchanged (word-for-word).
5. Switch works both ways (USE_REAL_FINANCE_TOOLS True and False).
"""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from config.settings import settings
from graph.state.models import AgentResult
from finance_engine.etl import run_business, write_run_outputs
from agents.cashflow.agent import cash_flow_agent
from agents.expense.agent import expense_agent
from agents.invoice.agent import invoice_agent
from agents.cashflow import finance_tools as cf_tools
from agents.expense import finance_tools as exp_tools
from agents.invoice import finance_tools as inv_tools
from agents.expense.schemas import ExpenseAnomaliesResult

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
TODAY = date(2026, 11, 5)


@pytest.fixture(scope="module")
def processed_dir(tmp_path_factory):
    """Builds processed data for biz_001 in a temporary folder (never edits data/)."""
    root = tmp_path_factory.mktemp("processed")
    write_run_outputs(run_business(SAMPLES, today=TODAY), root / "biz_001")
    return root


@pytest.fixture(autouse=True)
def setup_environment(processed_dir, monkeypatch):
    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(processed_dir))
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", True)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")


# --------------------------------------------------------------------------- #
# 1. Exact metric matching on real data (biz_001)
# --------------------------------------------------------------------------- #

def test_cashflow_agent_real_metrics_match_tool_output_exactly():
    """Verify Cash Flow Agent copies every metric exactly from Member 3's real tools."""
    state = {"business_id": "biz_001", "period": "last_month", "today": TODAY}
    result = cash_flow_agent(state)

    expected_summary = cf_tools.get_financial_summary("biz_001", "last_month", today=TODAY)
    expected_forecast = cf_tools.forecast_cash_flow("biz_001", 30, today=TODAY)

    assert isinstance(result, AgentResult)
    assert result.agent_name == "cashflow"
    assert result.requires_approval is False

    # Exact metrics check
    assert result.metrics["inflows"] == expected_summary.inflows
    assert result.metrics["outflows"] == expected_summary.outflows
    assert result.metrics["net_cash_flow"] == expected_summary.net_cash_flow
    assert result.metrics["cash_balance"] == expected_summary.cash_balance
    assert result.metrics["receivables"] == expected_summary.receivables
    assert result.metrics["calculation_version"] == expected_summary.calculation_version
    assert result.metrics["horizon_days"] == expected_forecast.horizon_days
    assert result.metrics["projected_balance"] == expected_forecast.projected_balance
    assert result.metrics["risk_level"] == expected_forecast.risk_level
    assert result.metrics["currency"] == "INR"

    # Currency symbol formatting: ₹ for INR, not $
    assert any("₹" in f for f in result.findings)
    assert not any("$" in f for f in result.findings)


def test_invoice_agent_real_metrics_match_tool_output_exactly():
    """Verify Invoice Agent copies count, total_amount, and invoice lines from real tools."""
    as_of = "2026-11-05"
    state = {"business_id": "biz_001", "as_of_date": as_of, "today": TODAY}
    result = invoice_agent(state)

    expected_overdue = inv_tools.get_overdue_invoices("biz_001", as_of_date=as_of, today=TODAY)

    assert isinstance(result, AgentResult)
    assert result.agent_name == "invoice"
    assert result.requires_approval is False

    # Exact metrics check
    assert result.metrics["count"] == expected_overdue.count
    assert result.metrics["total_amount"] == expected_overdue.total_amount
    assert result.metrics["as_of_date"] == as_of
    assert result.metrics["currency"] == "INR"
    assert len(result.metrics["invoices"]) == expected_overdue.count
    assert [inv["invoice_id"] for inv in result.metrics["invoices"]] == [
        i.invoice_id for i in expected_overdue.invoices
    ]

    # Currency symbol formatting: ₹ for INR, not $
    assert any("₹" in f for f in result.findings)
    assert not any("$" in f for f in result.findings)


def test_expense_agent_real_metrics_match_tool_output_exactly():
    """Verify Expense Agent copies anomalies list and count from real tools."""
    start = "2026-10-01"
    end = "2026-11-04"
    state = {"business_id": "biz_001", "start_date": start, "end_date": end, "today": TODAY}
    result = expense_agent(state)

    expected_anomalies = exp_tools.get_expense_anomalies("biz_001", start_date=start, end_date=end, today=TODAY)

    assert isinstance(result, AgentResult)
    assert result.agent_name == "expense"
    assert result.requires_approval is False

    # Exact metrics check
    assert result.metrics["anomalies_count"] == len(expected_anomalies.anomalies)
    assert result.metrics["anomalies"] == [a.model_dump() for a in expected_anomalies.anomalies]
    assert result.metrics["currency"] == "INR"

    # Currency symbol formatting: ₹ for INR, not $
    assert any("₹" in f for f in result.findings)
    assert not any("$" in f for f in result.findings)


# --------------------------------------------------------------------------- #
# 2. Safe failure on unknown business_id (no numbers, limitation message)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("agent_func,state", [
    (cash_flow_agent, {"business_id": "biz_nonexistent_999"}),
    (invoice_agent, {"business_id": "biz_nonexistent_999"}),
    (expense_agent, {"business_id": "biz_nonexistent_999"}),
])
def test_unknown_business_id_gives_limitation_with_no_numbers(agent_func, state):
    """Verify unknown tenant raises BusinessNotFoundError internally and returns clean limitation."""
    result = agent_func(state)

    assert isinstance(result, AgentResult)
    assert "could not calculate right now" in result.summary.lower()
    # Must have NO numbers / empty metrics dict
    assert result.metrics == {}
    assert len(result.findings) > 0
    assert len(result.recommendations) > 0


# --------------------------------------------------------------------------- #
# 3. Clean "none found" result when anomaly list is empty
# --------------------------------------------------------------------------- #

def test_empty_anomaly_list_gives_clean_none_found():
    """Verify that when no anomalies are detected, agent reports none were found without making up examples."""
    empty_result = ExpenseAnomaliesResult(
        business_id="biz_001",
        start_date="2026-10-01",
        end_date="2026-10-05",
        anomalies=[],
        calculation_version="finance-engine-1.1.0",
        currency="INR",
        warnings=[],
    )

    with patch("agents.expense.agent.get_expense_anomalies", return_value=empty_result):
        result = expense_agent({"business_id": "biz_001", "start_date": "2026-10-01", "end_date": "2026-10-05"})

        assert isinstance(result, AgentResult)
        assert result.metrics["anomalies_count"] == 0
        assert result.metrics["anomalies"] == []
        assert "no expense anomalies found" in result.summary.lower()
        assert any("no spending anomalies were detected" in f.lower() for f in result.findings)


# --------------------------------------------------------------------------- #
# 4. Tool warnings appear in caveats unchanged (word-for-word)
# --------------------------------------------------------------------------- #

def test_tool_warnings_passed_to_caveats_unchanged():
    """Verify warnings emitted by tools are passed word-for-word into AgentResult.caveats."""
    sample_warning = "Invoice inv-999 (USD 1,200.00) excluded (no FX conversion)."

    with patch("agents.invoice.agent.get_overdue_invoices") as mock_tool:
        mock_result = inv_tools.get_overdue_invoices("biz_001", as_of_date="2026-11-05", today=TODAY)
        # Inject custom tool warning
        mock_result.warnings.append(sample_warning)
        mock_tool.return_value = mock_result

        result = invoice_agent({"business_id": "biz_001", "as_of_date": "2026-11-05", "today": TODAY})

        assert sample_warning in result.caveats
        assert sample_warning in result.metrics["warnings"]


# --------------------------------------------------------------------------- #
# 5. Switching works both ways
# --------------------------------------------------------------------------- #

def test_toggle_switch_works_both_ways(monkeypatch):
    """Verify switching USE_REAL_FINANCE_TOOLS dynamically alternates between real and mock tools."""
    # 1. Real tools mode (True)
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", True)
    real_res = cash_flow_agent({"business_id": "biz_001", "period": "last_month", "today": TODAY})
    assert real_res.metrics["calculation_version"] == "finance-engine-1.1.0"
    assert real_res.metrics["currency"] == "INR"

    # 2. Mock tools mode (False)
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", False)
    mock_res = cash_flow_agent({"business_id": "biz_001", "period": "last_month"})
    assert mock_res.metrics["calculation_version"] == "v1.0.0-mock"
    assert mock_res.metrics["inflows"] == 45000.0
    assert mock_res.metrics["currency"] == "USD"

    # 3. Switch back to Real tools mode (True)
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", True)
    real_res_again = cash_flow_agent({"business_id": "biz_001", "period": "last_month", "today": TODAY})
    assert real_res_again.metrics["calculation_version"] == "finance-engine-1.1.0"
    assert real_res_again.metrics["currency"] == "INR"
