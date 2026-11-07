"""
Tests for Financial Intelligence Agent (Day 7).

Verifies:
1. All three healthy -> combined result with metrics copied exactly.
2. One specialist has a limitation -> listed in missing_parts, nothing guessed.
3. All three limitations -> limitation result with empty metrics.
4. Specialist caveats copied word-for-word.
5. OpenAI fails or has no key -> deterministic template summary.
6. AI text with an invented number -> rejected, template fallback used.
7. Empty business_id -> limitation result with generic text.
8. Recommendations have priority and requires_approval set correctly.
9. Agent never calls any finance tool.
10. Currency formatted as ₹.
"""

from unittest.mock import MagicMock, patch
import pytest

from config.settings import settings
from graph.state.models import AgentResult
from agents.cashflow.agent import cash_flow_agent
from agents.invoice.agent import invoice_agent
from agents.expense.agent import expense_agent
from agents.intelligence.agent import (
    financial_intelligence_agent,
    intelligence_agent,
)
from agents.intelligence.schemas import (
    FinancialIntelligenceExplanation,
    FinancialIntelligenceResult,
    IntelligenceRecommendation,
)


@pytest.fixture(autouse=True)
def force_mock_environment(monkeypatch):
    """Force mock finance tools and clear OpenAI key for safe, isolated testing."""
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


# --------------------------------------------------------------------------- #
# 1. All three healthy -> combined result with metrics copied exactly
# --------------------------------------------------------------------------- #

def test_all_three_healthy_combined_metrics_copied_exactly(mock_specialist_results):
    """Verify combined result copies every metric from specialists without modification."""
    cf_res, inv_res, exp_res = mock_specialist_results

    result = intelligence_agent(
        business_id="biz_mock_001",
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    assert isinstance(result, FinancialIntelligenceResult)
    assert isinstance(result, AgentResult)
    assert result.agent_name == "intelligence"
    assert result.missing_parts == []

    # Exact metrics matching from Cash Flow
    assert result.metrics["inflows"] == cf_res.metrics["inflows"]
    assert result.metrics["outflows"] == cf_res.metrics["outflows"]
    assert result.metrics["net_cash_flow"] == cf_res.metrics["net_cash_flow"]
    assert result.metrics["cash_balance"] == cf_res.metrics["cash_balance"]
    assert result.metrics["receivables"] == cf_res.metrics["receivables"]
    assert result.metrics["horizon_days"] == cf_res.metrics["horizon_days"]
    assert result.metrics["projected_balance"] == cf_res.metrics["projected_balance"]

    # Exact metrics matching from Invoice
    assert result.metrics["count"] == inv_res.metrics["count"]
    assert result.metrics["total_amount"] == inv_res.metrics["total_amount"]

    # Exact metrics matching from Expense
    assert result.metrics["anomalies_count"] == exp_res.metrics["anomalies_count"]
    assert result.metrics["anomalies"] == exp_res.metrics["anomalies"]

    # Also available under specialist names
    assert result.metrics["cashflow"]["cash_balance"] == cf_res.metrics["cash_balance"]
    assert result.metrics["invoice"]["total_amount"] == inv_res.metrics["total_amount"]
    assert result.metrics["expense"]["anomalies_count"] == exp_res.metrics["anomalies_count"]

    # Money formatted as ₹
    assert "₹" in result.overall_summary
    assert "$" not in result.overall_summary


# --------------------------------------------------------------------------- #
# 2. One specialist has a limitation -> listed in missing_parts, nothing guessed
# --------------------------------------------------------------------------- #

def test_one_specialist_limitation_listed_in_missing_parts(mock_specialist_results):
    """Verify specialist with limitation is listed in missing_parts and not guessed."""
    cf_res, _, exp_res = mock_specialist_results

    # Invoice agent returns limitation
    inv_limited = AgentResult(
        agent_name="invoice",
        summary="Overdue invoices could not calculate right now.",
        metrics={},
        findings=["Database connection unavailable."],
        recommendations=["Retry later."],
        caveats=["Invoice data gap."],
        requires_approval=False,
    )

    result = intelligence_agent(
        business_id="biz_mock_001",
        cashflow_result=cf_res,
        invoice_result=inv_limited,
        expense_result=exp_res,
    )

    assert "invoice" in result.missing_parts
    assert "cashflow" not in result.missing_parts
    assert "expense" not in result.missing_parts

    # Cashflow and expense metrics are present
    assert result.metrics["cash_balance"] == cf_res.metrics["cash_balance"]
    assert result.metrics["anomalies_count"] == exp_res.metrics["anomalies_count"]

    # Invoice metrics must NOT exist
    assert "total_amount" not in result.metrics
    assert "invoice" not in result.metrics

    # Caveats from all provided specialists (including the limited one) are preserved
    assert "Invoice data gap." in result.caveats


def test_specialist_not_provided_listed_in_missing_parts(mock_specialist_results):
    """Verify an omitted specialist (None) is identified in missing_parts."""
    cf_res, _, exp_res = mock_specialist_results

    result = intelligence_agent(
        business_id="biz_mock_001",
        cashflow_result=cf_res,
        invoice_result=None,
        expense_result=exp_res,
    )

    assert result.missing_parts == ["invoice"]
    assert "invoice" not in result.metrics
    assert result.metrics["cash_balance"] == cf_res.metrics["cash_balance"]


# --------------------------------------------------------------------------- #
# 3. All three limitations -> limitation result with empty metrics
# --------------------------------------------------------------------------- #

def test_all_three_limitations_returns_limitation_result():
    """Verify that when all three specialists fail, a limitation result with empty metrics is returned."""
    cf_lim = AgentResult(
        agent_name="cashflow",
        summary="Cash flow metrics could not calculate right now.",
        metrics={},
        caveats=["Cash flow data offline."],
        requires_approval=False,
    )
    inv_lim = AgentResult(
        agent_name="invoice",
        summary="Overdue invoices could not calculate right now.",
        metrics={},
        caveats=["Invoice system unavailable."],
        requires_approval=False,
    )
    exp_lim = AgentResult(
        agent_name="expense",
        summary="Expense anomalies could not calculate right now.",
        metrics={},
        caveats=["Expense categorization pending."],
        requires_approval=False,
    )

    result = intelligence_agent(
        business_id="biz_mock_001",
        cashflow_result=cf_lim,
        invoice_result=inv_lim,
        expense_result=exp_lim,
    )

    assert result.metrics == {}
    assert set(result.missing_parts) == {"cashflow", "invoice", "expense"}
    assert "could not calculate" in result.overall_summary.lower()
    assert result.requires_approval is False
    # Caveats copied word-for-word
    assert "Cash flow data offline." in result.caveats
    assert "Invoice system unavailable." in result.caveats
    assert "Expense categorization pending." in result.caveats


# --------------------------------------------------------------------------- #
# 4. Caveats copied word for word
# --------------------------------------------------------------------------- #

def test_caveats_copied_word_for_word():
    """Verify specialist caveats are copied exactly word-for-word without rewording."""
    cf_res = AgentResult(
        agent_name="cashflow",
        summary="Cash flow ok",
        metrics={"cash_balance": 50000.0, "net_cash_flow": 5000.0, "inflows": 20000.0, "outflows": 15000.0},
        caveats=["Bank synchronization delayed by 48 hours.", "Unreconciled tax account."],
        requires_approval=False,
    )
    inv_res = AgentResult(
        agent_name="invoice",
        summary="Invoices ok",
        metrics={"count": 2, "total_amount": 8000.0},
        caveats=["Disputed customer claims excluded from overdue totals."],
        requires_approval=False,
    )
    exp_res = AgentResult(
        agent_name="expense",
        summary="Expenses ok",
        metrics={"anomalies_count": 1, "anomalies": []},
        caveats=["OCR parsing incomplete on 3 scanned vendor invoices."],
        requires_approval=False,
    )

    result = intelligence_agent(
        business_id="biz_custom",
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    expected_caveats = [
        "Bank synchronization delayed by 48 hours.",
        "Unreconciled tax account.",
        "Disputed customer claims excluded from overdue totals.",
        "OCR parsing incomplete on 3 scanned vendor invoices.",
    ]
    assert result.caveats == expected_caveats


# --------------------------------------------------------------------------- #
# 5. OpenAI fails / no key -> template summary
# --------------------------------------------------------------------------- #

def test_openai_fails_or_no_key_uses_template_summary(mock_specialist_results):
    """Verify template summary is used when OpenAI key is missing or call throws exception."""
    cf_res, inv_res, exp_res = mock_specialist_results

    # 1. No API key (default fixture)
    result_no_key = intelligence_agent(
        business_id="biz_mock_001",
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )
    assert "Ending cash balance is ₹78,000.00" in result_no_key.overall_summary
    assert len(result_no_key.links) > 0
    assert len(result_no_key.key_risks) > 0
    assert len(result_no_key.recommendations) > 0

    # 2. OpenAI raises an exception
    with patch("config.settings.settings.OPENAI_API_KEY", "fake_key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.beta.chat.completions.parse.side_effect = RuntimeError("OpenAI API timeout")

            result_error = intelligence_agent(
                business_id="biz_mock_001",
                cashflow_result=cf_res,
                invoice_result=inv_res,
                expense_result=exp_res,
            )
            assert isinstance(result_error, FinancialIntelligenceResult)
            assert "Ending cash balance is ₹78,000.00" in result_error.overall_summary


# --------------------------------------------------------------------------- #
# 6. AI text with an invented number -> rejected, template used
# --------------------------------------------------------------------------- #

def test_ai_text_with_invented_number_rejected_template_used(mock_specialist_results):
    """
    CRITICAL HARD RULE 2:
    If OpenAI invents a number not in the specialist metrics,
    throw the AI text away and use the template summary instead.
    """
    cf_res, inv_res, exp_res = mock_specialist_results

    # Invented numbers: 25% profit margin, 150 days runway (neither is in specialist metrics)
    hallucinated_explanation = FinancialIntelligenceExplanation(
        overall_summary="The business is performing well with a 25% profit margin and 150 days of runway.",
        links=["Cash is healthy."],
        key_risks=["No immediate risks."],
        recommendations=[
            IntelligenceRecommendation(
                recommendation="Expand business aggressively.",
                priority="high",
                reason="25% margin allows aggressive reinvestment.",
                requires_approval=False,
            )
        ],
    )

    with patch("config.settings.settings.OPENAI_API_KEY", "fake_key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.parsed = hallucinated_explanation
            mock_completion.choices = [mock_choice]
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            result = intelligence_agent(
                business_id="biz_mock_001",
                cashflow_result=cf_res,
                invoice_result=inv_res,
                expense_result=exp_res,
            )

            # The hallucinated AI text must be discarded!
            assert "25%" not in result.overall_summary
            assert "150" not in result.overall_summary
            # Must fall back to deterministic template summary
            assert "Ending cash balance is ₹78,000.00" in result.overall_summary


def test_ai_text_with_valid_numbers_accepted(mock_specialist_results):
    """Verify that when AI text strictly uses existing numbers from metrics, it is accepted."""
    cf_res, inv_res, exp_res = mock_specialist_results

    # Valid numbers strictly from specialist metrics: 78000.0, 20000.0, 4
    cb = cf_res.metrics["cash_balance"]
    tot = inv_res.metrics["total_amount"]
    cnt = inv_res.metrics["count"]
    valid_explanation = FinancialIntelligenceExplanation(
        overall_summary=f"Liquid balance is strong at ₹{cb:,.2f} with {cnt} overdue accounts totaling ₹{tot:,.2f}.",
        links=["Overdue receivables delay cash collection."],
        key_risks=[f"Collection risk on {cnt} accounts."],
        recommendations=[
            IntelligenceRecommendation(
                recommendation="Send payment reminders for overdue invoices.",
                priority="high",
                reason="Recover past-due funds to bolster cash buffer.",
                requires_approval=True,
            )
        ],
    )

    with patch("config.settings.settings.OPENAI_API_KEY", "fake_key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_completion = MagicMock()
            mock_choice = MagicMock()
            mock_choice.message.parsed = valid_explanation
            mock_completion.choices = [mock_choice]
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            result = intelligence_agent(
                business_id="biz_mock_001",
                cashflow_result=cf_res,
                invoice_result=inv_res,
                expense_result=exp_res,
            )

            assert result.overall_summary == valid_explanation.overall_summary
            assert result.recommendations[0].recommendation == "Send payment reminders for overdue invoices."
            assert result.recommendations[0].requires_approval is True


# --------------------------------------------------------------------------- #
# 7. Empty business_id -> limitation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("empty_id", ["", "   ", None])
def test_empty_business_id_returns_limitation(empty_id):
    """Verify that empty or whitespace business_id returns a limitation with generic text."""
    result = intelligence_agent(business_id=empty_id)

    assert isinstance(result, FinancialIntelligenceResult)
    assert result.metrics == {}
    assert "business_id is required" in result.overall_summary
    assert result.missing_parts == ["cashflow", "invoice", "expense"]
    assert result.requires_approval is False


# --------------------------------------------------------------------------- #
# 8. Recommendations have priority and requires_approval set correctly
# --------------------------------------------------------------------------- #

def test_recommendations_governance_and_priority(mock_specialist_results):
    """
    Verify recommendations have priority ('high', 'medium', 'low') and follow
    ActionProposal rules: action-type recommendations have requires_approval=True.
    """
    cf_res, inv_res, exp_res = mock_specialist_results

    result = intelligence_agent(
        business_id="biz_mock_001",
        cashflow_result=cf_res,
        invoice_result=inv_res,
        expense_result=exp_res,
    )

    assert len(result.recommendations) > 0
    has_approval_required = False

    for rec in result.recommendations:
        assert rec.priority in ("high", "medium", "low")
        assert len(rec.reason) > 0
        assert isinstance(rec.requires_approval, bool)

        # Sending payment reminders is an external business action
        if "send" in rec.recommendation.lower() or "reminder" in rec.recommendation.lower():
            assert rec.requires_approval is True
            has_approval_required = True

    # If any recommendation requires approval, the result requires_approval must be True
    assert result.requires_approval == has_approval_required


# --------------------------------------------------------------------------- #
# 9. Agent NEVER calls any finance tool
# --------------------------------------------------------------------------- #

def test_intelligence_agent_never_calls_finance_tools(mock_specialist_results):
    """
    HARD RULE 1:
    The Financial Intelligence Agent must NOT call any finance tool.
    """
    cf_res, inv_res, exp_res = mock_specialist_results

    with patch("agents.tool_source.get_financial_summary") as mock_cf_tool, \
         patch("agents.tool_source.forecast_cash_flow") as mock_fc_tool, \
         patch("agents.tool_source.get_overdue_invoices") as mock_inv_tool, \
         patch("agents.tool_source.get_expense_anomalies") as mock_exp_tool:

        intelligence_agent(
            business_id="biz_mock_001",
            cashflow_result=cf_res,
            invoice_result=inv_res,
            expense_result=exp_res,
        )

        mock_cf_tool.assert_not_called()
        mock_fc_tool.assert_not_called()
        mock_inv_tool.assert_not_called()
        mock_exp_tool.assert_not_called()


# --------------------------------------------------------------------------- #
# 10. AgentState integration
# --------------------------------------------------------------------------- #

def test_intelligence_agent_with_agent_state_dict(mock_specialist_results):
    """Verify intelligence_agent can take an AgentState dict with agent_results list."""
    cf_res, inv_res, exp_res = mock_specialist_results

    state = {
        "business_id": "biz_mock_001",
        "agent_results": [cf_res, inv_res, exp_res],
    }

    result = intelligence_agent(state)

    assert isinstance(result, FinancialIntelligenceResult)
    assert result.metrics["cash_balance"] == 78000.0
    assert result.metrics["total_amount"] == 20000.0
    assert result.missing_parts == []
