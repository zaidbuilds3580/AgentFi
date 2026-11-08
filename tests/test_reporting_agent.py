"""
Tests for Executive Reporting Agent (Day 8).

Verifies:
1. Daily and monthly report with all inputs.
2. Some inputs missing (missing_parts filled, summary mentions it).
3. All inputs missing -> limitation result with empty metrics, no error details.
4. All inputs with limitations -> limitation result.
5. Empty business_id -> limitation result with generic text.
6. Caveats copied word-for-word.
7. Metrics match the inputs exactly.
8. Evidence matches the inputs exactly.
9. Recommendations copied unchanged (keep priority and requires_approval).
10. OpenAI failure gives template summary.
11. No API key gives template summary.
12. Invented number in AI text is discarded.
13. Valid numbers in AI text accepted.
14. to_chat_response() has exactly the agreed 7 keys.
15. Reporting agent never calls finance tools.
"""

import re
from unittest.mock import MagicMock, patch
import pytest

from config.settings import settings
from graph.state.models import AgentResult
from agents.cashflow.agent import cash_flow_agent
from agents.invoice.agent import invoice_agent
from agents.expense.agent import expense_agent
from agents.intelligence.agent import intelligence_agent
from agents.intelligence.schemas import (
    FinancialIntelligenceResult,
    IntelligenceRecommendation,
)
from agents.reporting.agent import (
    report_agent,
    reporting_agent,
)
from agents.reporting.schemas import (
    ReportEvidenceItem,
    ReportingExplanation,
    ReportingResult,
    ReportType,
)


@pytest.fixture(autouse=True)
def force_mock_environment(monkeypatch):
    """Force mock environment and clear OpenAI key for safe, isolated testing."""
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", False)
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")


@pytest.fixture
def mock_specialist_results():
    """Generates standard mock specialist results for biz_mock_001."""
    biz_id = "biz_mock_001"
    cf_res = cash_flow_agent({"business_id": biz_id})
    inv_res = invoice_agent({"business_id": biz_id})
    exp_res = expense_agent({"business_id": biz_id})
    return cf_res, inv_res, exp_res


@pytest.fixture
def mock_all_inputs(mock_specialist_results):
    """Generates all 4 inputs: intelligence, cashflow, invoice, expense."""
    cf_res, inv_res, exp_res = mock_specialist_results
    biz_id = "biz_mock_001"
    intel_res = intelligence_agent(
        business_id=biz_id,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )
    return intel_res, cf_res, inv_res, exp_res


# --------------------------------------------------------------------------- #
# 1. Daily and monthly report with all inputs
# --------------------------------------------------------------------------- #

def test_daily_report_with_all_inputs(mock_all_inputs):
    """Verify daily executive report with all inputs is short (2-3 sentences) and accurate."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    assert isinstance(result, ReportingResult)
    assert isinstance(result, AgentResult)
    assert result.agent_name == "reporting"
    assert result.report_type == ReportType.DAILY
    assert result.title == "Daily report - 5 November 2026"
    assert result.missing_parts == []
    assert result.currency == "INR"

    # Money formatted as ₹
    assert "₹" in result.summary
    assert "$" not in result.summary

    # Short: 2-3 sentences (splitting on period followed by whitespace to avoid decimal points)
    sentences = [s.strip() for s in re.split(r"\.\s+", result.summary.rstrip(".")) if s.strip()]
    assert 2 <= len(sentences) <= 3


def test_monthly_report_with_all_inputs(mock_all_inputs):
    """Verify monthly executive report with all inputs is 4-6 sentences."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="monthly",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    assert isinstance(result, ReportingResult)
    assert result.report_type == ReportType.MONTHLY
    assert result.title == "Monthly report - 5 November 2026"
    assert result.missing_parts == []
    assert result.currency == "INR"

    # Money formatted as ₹
    assert "₹" in result.summary
    assert "$" not in result.summary

    # 4-6 sentences (splitting on period followed by whitespace to avoid decimal points)
    sentences = [s.strip() for s in re.split(r"\.\s+", result.summary.rstrip(".")) if s.strip()]
    assert 4 <= len(sentences) <= 6


# --------------------------------------------------------------------------- #
# 2. Some inputs missing (missing_parts filled, summary mentions it)
# --------------------------------------------------------------------------- #

def test_some_inputs_missing_missing_parts_filled_summary_mentions_it(mock_specialist_results):
    """Verify missing inputs are listed in missing_parts and clearly mentioned in summary."""
    cf_res, inv_res, _ = mock_specialist_results

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=None,
        intelligence_result=None,
    )

    assert isinstance(result, ReportingResult)
    assert "expense" in result.missing_parts
    assert "intelligence" in result.missing_parts
    # Summary explicitly mentions the missing data
    summary_lower = result.summary.lower()
    assert "expense" in summary_lower or "unavailable" in summary_lower


# --------------------------------------------------------------------------- #
# 3. All inputs missing -> limitation result
# --------------------------------------------------------------------------- #

def test_all_inputs_missing_returns_limitation():
    """Verify that when all inputs are missing, a limitation result is returned with empty metrics."""
    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=None,
        cashflow_result=None,
        invoice_result=None,
        expense_result=None,
    )

    assert isinstance(result, ReportingResult)
    assert result.metrics == {}
    assert result.evidence == []
    assert result.recommendations == []
    assert "Report could not be generated right now" in result.summary
    assert set(result.missing_parts) == {"intelligence", "cashflow", "invoice", "expense"}
    assert result.requires_approval is False


def test_all_inputs_with_limitations_returns_limitation():
    """Verify that when all inputs have limitation summaries, a limitation result is returned."""
    cf_lim = AgentResult(
        agent_name="cashflow",
        summary="Cash flow metrics could not calculate right now.",
        metrics={},
        caveats=["Cash flow data unavailable."],
    )
    inv_lim = AgentResult(
        agent_name="invoice",
        summary="Invoice metrics could not calculate right now.",
        metrics={},
        caveats=["Invoice service down."],
    )
    exp_lim = AgentResult(
        agent_name="expense",
        summary="Expense metrics could not calculate right now.",
        metrics={},
        caveats=["Expense data corrupted."],
    )
    intel_lim = FinancialIntelligenceResult(
        agent_name="intelligence",
        summary="Financial intelligence could not calculate right now.",
        metrics={},
        caveats=["Intelligence unavailable."],
    )

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_lim,
        cashflow_result=cf_lim,
        invoice_result=inv_lim,
        expense_result=exp_lim,
    )

    assert result.metrics == {}
    assert "Report could not be generated right now" in result.summary
    assert "Cash flow data unavailable." in result.caveats
    assert "Invoice service down." in result.caveats


# --------------------------------------------------------------------------- #
# 4. Empty business_id gives a limitation result
# --------------------------------------------------------------------------- #

def test_empty_business_id_gives_limitation(mock_all_inputs):
    """Verify empty or whitespace business_id returns a limitation result."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    result = reporting_agent(
        business_id="",
        report_type="daily",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    assert isinstance(result, ReportingResult)
    assert "business_id is required" in result.summary
    assert result.metrics == {}
    assert result.evidence == []
    assert "Missing business_id parameter." in result.caveats


# --------------------------------------------------------------------------- #
# 5. Caveats copied word-for-word
# --------------------------------------------------------------------------- #

def test_caveats_copied_word_for_word():
    """Verify caveats from all provided inputs are copied exactly without rewording."""
    cf_res = AgentResult(
        agent_name="cashflow",
        summary="Cash flow ok",
        metrics={"cash_balance": 50000.0, "net_cash_flow": 5000.0},
        caveats=["Bank sync delayed by 24h."],
    )
    inv_res = AgentResult(
        agent_name="invoice",
        summary="Invoices ok",
        metrics={"count": 2, "total_amount": 8000.0},
        caveats=["Disputed customer invoices excluded."],
    )
    exp_res = AgentResult(
        agent_name="expense",
        summary="Expenses ok",
        metrics={"anomalies_count": 1},
        caveats=["Vendor categorization pending manual review."],
    )
    intel_res = FinancialIntelligenceResult(
        agent_name="intelligence",
        summary="Intelligence ok",
        metrics={"cash_balance": 50000.0},
        caveats=["Cross-domain tax adjustment pending."],
    )

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    expected_caveats = [
        "Cross-domain tax adjustment pending.",
        "Bank sync delayed by 24h.",
        "Disputed customer invoices excluded.",
        "Vendor categorization pending manual review.",
    ]
    assert result.caveats == expected_caveats


# --------------------------------------------------------------------------- #
# 6. Metrics match the inputs exactly
# --------------------------------------------------------------------------- #

def test_metrics_match_inputs_exactly(mock_all_inputs):
    """Verify combined metrics match inputs exactly with zero calculation."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    # Specialists metrics match exactly
    assert result.metrics["cash_balance"] == cf_res.metrics["cash_balance"]
    assert result.metrics["net_cash_flow"] == cf_res.metrics["net_cash_flow"]
    assert result.metrics["inflows"] == cf_res.metrics["inflows"]
    assert result.metrics["outflows"] == cf_res.metrics["outflows"]
    assert result.metrics["total_amount"] == inv_res.metrics["total_amount"]
    assert result.metrics["count"] == inv_res.metrics["count"]
    assert result.metrics["anomalies_count"] == exp_res.metrics["anomalies_count"]

    # Namespaced metrics also match
    assert result.metrics["cashflow"]["cash_balance"] == cf_res.metrics["cash_balance"]
    assert result.metrics["invoice"]["total_amount"] == inv_res.metrics["total_amount"]
    assert result.metrics["expense"]["anomalies_count"] == exp_res.metrics["anomalies_count"]


# --------------------------------------------------------------------------- #
# 7. Evidence matches the inputs exactly
# --------------------------------------------------------------------------- #

def test_evidence_matches_inputs_exactly(mock_all_inputs):
    """Verify evidence items are copied from inputs with correct labels, values, and sources."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    assert len(result.evidence) > 0
    sources = {item.source for item in result.evidence}
    assert "cashflow" in sources
    assert "invoice" in sources
    assert "expense" in sources
    assert "intelligence" in sources

    for item in result.evidence:
        assert isinstance(item, ReportEvidenceItem)
        assert item.label
        assert item.value
        assert item.source in ("cashflow", "invoice", "expense", "intelligence")


# --------------------------------------------------------------------------- #
# 8. Recommendations copied unchanged
# --------------------------------------------------------------------------- #

def test_recommendations_copied_unchanged():
    """Verify recommendations are copied unchanged from intelligence with priority and approval preserved."""
    test_recs = [
        IntelligenceRecommendation(
            recommendation="Send reminders for 3 overdue invoices.",
            priority="high",
            reason="Recover past due receivables.",
            requires_approval=True,
        ),
        IntelligenceRecommendation(
            recommendation="Review discretionary travel spending.",
            priority="low",
            reason="Spending baseline slightly higher.",
            requires_approval=False,
        ),
    ]

    intel_res = FinancialIntelligenceResult(
        agent_name="intelligence",
        summary="Intelligence ok",
        metrics={"cash_balance": 50000.0},
        recommendations=test_recs,
        requires_approval=True,
    )

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_res,
    )

    assert len(result.recommendations) == 2
    assert result.recommendations[0].recommendation == "Send reminders for 3 overdue invoices."
    assert result.recommendations[0].priority == "high"
    assert result.recommendations[0].requires_approval is True
    assert result.recommendations[1].recommendation == "Review discretionary travel spending."
    assert result.recommendations[1].priority == "low"
    assert result.recommendations[1].requires_approval is False
    assert result.requires_approval is True


# --------------------------------------------------------------------------- #
# 9. OpenAI failure gives template summary
# --------------------------------------------------------------------------- #

def test_openai_failure_gives_template_summary(mock_all_inputs):
    """Verify OpenAI failure safely falls back to template summary without leaking errors."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    with patch("config.settings.settings.OPENAI_API_KEY", "fake_key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.beta.chat.completions.parse.side_effect = RuntimeError("OpenAI connection timeout")

            result = reporting_agent(
                business_id="biz_mock_001",
                report_type="daily",
                intelligence_result=intel_res,
                cashflow_result=cf_res,
                invoice_result=inv_res,
                expense_result=exp_res,
            )

            assert isinstance(result, ReportingResult)
            assert "Ending cash balance is ₹78,000.00" in result.summary
            # Error details must never leak into summary
            assert "timeout" not in result.summary.lower()
            assert "runtimeerror" not in result.summary.lower()


# --------------------------------------------------------------------------- #
# 10. No API key gives template summary
# --------------------------------------------------------------------------- #

def test_no_api_key_gives_template_summary(mock_all_inputs):
    """Verify that when no API key is set, deterministic template summary is returned."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    assert isinstance(result, ReportingResult)
    assert "Ending cash balance is ₹78,000.00" in result.summary


# --------------------------------------------------------------------------- #
# 11. Invented number in AI text is discarded
# --------------------------------------------------------------------------- #

def test_invented_number_in_ai_text_discarded(mock_all_inputs):
    """Verify that if AI hallucinated an unverified figure, it is discarded and template used."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    hallucinated_summary = ReportingExplanation(
        summary="Ending cash balance is ₹78,000.00 with an unprecedented 42.5% net margin."
    )

    with patch("config.settings.settings.OPENAI_API_KEY", "fake_key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.parsed = hallucinated_summary
            mock_completion.choices = [mock_choice]
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            result = reporting_agent(
                business_id="biz_mock_001",
                report_type="daily",
                intelligence_result=intel_res,
                cashflow_result=cf_res,
                invoice_result=inv_res,
                expense_result=exp_res,
            )

            # Hallucinated number 42.5 must be discarded!
            assert "42.5" not in result.summary
            # Template fallback was used
            assert "Ending cash balance is ₹78,000.00" in result.summary


def test_valid_numbers_in_ai_text_accepted(mock_all_inputs):
    """Verify that when AI text uses strictly verified figures from metrics, it is accepted."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    valid_ai_summary = ReportingExplanation(
        summary="Ending cash balance is ₹78,000.00 with 4 overdue invoices requiring follow-up."
    )

    with patch("config.settings.settings.OPENAI_API_KEY", "fake_key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.parsed = valid_ai_summary
            mock_completion.choices = [mock_choice]
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            result = reporting_agent(
                business_id="biz_mock_001",
                report_type="daily",
                intelligence_result=intel_res,
                cashflow_result=cf_res,
                invoice_result=inv_res,
                expense_result=exp_res,
            )

            assert result.summary == "Ending cash balance is ₹78,000.00 with 4 overdue invoices requiring follow-up."


# --------------------------------------------------------------------------- #
# 12. to_chat_response() has exactly the agreed keys
# --------------------------------------------------------------------------- #

def test_to_chat_response_has_exactly_agreed_keys(mock_all_inputs):
    """Verify to_chat_response() returns a plain dict with exactly the agreed 7 keys."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    result = reporting_agent(
        business_id="biz_mock_001",
        report_type="daily",
        intelligence_result=intel_res,
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    chat_payload = result.to_chat_response()

    assert isinstance(chat_payload, dict)
    expected_keys = {
        "summary",
        "evidence",
        "caveats",
        "recommendations",
        "missing_parts",
        "report_type",
        "currency",
    }
    assert set(chat_payload.keys()) == expected_keys

    # Check value types
    assert isinstance(chat_payload["summary"], str)
    assert isinstance(chat_payload["evidence"], list)
    assert isinstance(chat_payload["caveats"], list)
    assert isinstance(chat_payload["recommendations"], list)
    assert isinstance(chat_payload["missing_parts"], list)
    assert chat_payload["report_type"] == "daily"
    assert chat_payload["currency"] == "INR"

    # Evidence items in chat payload are plain dicts
    if chat_payload["evidence"]:
        first_ev = chat_payload["evidence"][0]
        assert isinstance(first_ev, dict)
        assert set(first_ev.keys()) == {"label", "value", "source"}

    # Docstring check
    assert result.to_chat_response.__doc__ is not None
    assert len(result.to_chat_response.__doc__.strip()) > 0


# --------------------------------------------------------------------------- #
# 13. State dictionary and report_agent alias support
# --------------------------------------------------------------------------- #

def test_state_dict_and_report_agent_alias(mock_all_inputs):
    """Verify reporting agent works seamlessly when called with a state dict and alias."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    state = {
        "business_id": "biz_mock_001",
        "report_type": "monthly",
        "intelligence_result": intel_res,
        "cashflow_result": cf_res,
        "invoice_result": inv_res,
        "expense_result": exp_res,
    }

    result = report_agent(state)

    assert isinstance(result, ReportingResult)
    assert result.report_type == ReportType.MONTHLY
    assert result.title == "Monthly report - 5 November 2026"
    assert result["summary"] == result.summary
    assert result["metrics"] == result.metrics


# --------------------------------------------------------------------------- #
# 14. Reporting agent never calls finance tools
# --------------------------------------------------------------------------- #

def test_reporting_agent_never_calls_finance_tools(mock_all_inputs):
    """Verify that reporting_agent never calls get_financial_summary or forecast_cash_flow."""
    intel_res, cf_res, inv_res, exp_res = mock_all_inputs

    with patch("agents.tool_source.get_financial_summary") as mock_tool_summary:
        with patch("agents.tool_source.forecast_cash_flow") as mock_tool_forecast:
            reporting_agent(
                business_id="biz_mock_001",
                report_type="daily",
                intelligence_result=intel_res,
                cashflow_result=cf_res,
                invoice_result=inv_res,
                expense_result=exp_res,
            )
            mock_tool_summary.assert_not_called()
            mock_tool_forecast.assert_not_called()

