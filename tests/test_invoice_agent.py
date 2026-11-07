"""
Tests for Invoice Agent and mock financial tools.

Verifies:
1. Metrics in the result match the tool output exactly.
2. Tool failure gives a limitation message and no numbers.
3. OpenAI failure gives the template summary.
4. No OpenAI key gives the template summary.
5. Empty business_id gives a limitation without calling the tool.
6. Result is a valid AgentResult.
7. Mock tool sample data is internally consistent (invoice totals add up).
"""

from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError

from config.settings import settings
from graph.state.models import AgentResult
from agents.invoice.agent import invoice_agent
from agents.invoice.mock_tools import get_overdue_invoices
from agents.invoice.schemas import (
    AgeingBand,
    InvoiceExplanation,
    OverdueInvoice,
    OverdueInvoicesResult,
)


@pytest.fixture(autouse=True)
def force_mock_finance_tools(monkeypatch):
    """Force mock finance tools for existing mock-data tests."""
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", False)


def test_mock_tools_return_fixed_sample_data_and_totals_add_up():
    """
    Verify mock tool returns the expected schema and that invoice totals and ageing
    bands are internally consistent (sums add up exactly).
    """
    biz_id = "biz_inv_001"
    result = get_overdue_invoices(biz_id, as_of_date="2026-11-03")

    assert isinstance(result, OverdueInvoicesResult)
    assert result.business_id == biz_id
    assert result.as_of_date == "2026-11-03"
    assert result.calculation_version == "v1.0.0-mock"

    # Consistency checks:
    # 1. Total invoice count matches invoices list length
    assert len(result.invoices) == result.count
    assert result.count == 4

    # 2. Sum of individual invoice amounts matches total_amount exactly
    sum_invoices_amount = sum(inv.amount for inv in result.invoices)
    assert sum_invoices_amount == result.total_amount
    assert result.total_amount == 20000.0

    # 3. Sum of ageing bands count matches total count
    sum_bands_count = sum(band.count for band in result.ageing_bands)
    assert sum_bands_count == result.count

    # 4. Sum of ageing bands total_amount matches total_amount
    sum_bands_amount = sum(band.total_amount for band in result.ageing_bands)
    assert sum_bands_amount == result.total_amount

    # 5. Check ageing band counts and amounts
    bands_by_name = {b.band: b for b in result.ageing_bands}
    assert bands_by_name["1-30"].count == 2
    assert bands_by_name["1-30"].total_amount == 7700.0
    assert bands_by_name["31-60"].count == 1
    assert bands_by_name["31-60"].total_amount == 6800.0
    assert bands_by_name["61+"].count == 1
    assert bands_by_name["61+"].total_amount == 5500.0


def test_schemas_enforce_business_id():
    """Verify OverdueInvoicesResult rejects empty or whitespace business_id."""
    with pytest.raises(ValidationError):
        OverdueInvoicesResult(
            business_id="",
            as_of_date="2026-11-03",
            count=0,
            total_amount=0.0,
            ageing_bands=[],
            invoices=[],
        )

    with pytest.raises(ValidationError):
        OverdueInvoicesResult(
            business_id="   ",
            as_of_date="2026-11-03",
            count=0,
            total_amount=0.0,
            ageing_bands=[],
            invoices=[],
        )


def test_metrics_match_tool_output_exactly_with_openai():
    """
    Verify metrics in AgentResult are copied directly from tool output,
    and narrative explanation is populated from OpenAI.
    """
    biz_id = "biz_client_acme"
    expected_data = get_overdue_invoices(biz_id)

    mock_explanation = InvoiceExplanation(
        summary="You have 4 overdue invoices totaling $20,000.00 requiring immediate follow-up.",
        findings=[
            "Oldest invoice is INV-2024-004 which is 80 days overdue for $5,500.00.",
            "70% of overdue debt sits in the 30+ days overdue bands.",
        ],
        recommendations=[
            "Consider sending a payment reminder for invoice INV-2024-004.",
            "Contact customer CUST-DELTA regarding 80-day overdue balance.",
        ],
    )

    with patch.object(settings, "OPENAI_API_KEY", "mock-openai-key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_completion = MagicMock()
            mock_completion.choices[0].message.parsed = mock_explanation
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            result = invoice_agent({"business_id": biz_id})

            # Verify OpenAI initialization parameters (timeout=10, max_retries=1)
            mock_openai_cls.assert_called_once_with(
                api_key="mock-openai-key",
                timeout=10,
                max_retries=1,
            )

            # 1. Result is a valid AgentResult
            assert isinstance(result, AgentResult)
            assert result.agent_name == "invoice"
            assert result.requires_approval is False

            # 2. Summary, findings, and recommendations match AI explanation
            assert result.summary == mock_explanation.summary
            assert result.findings == mock_explanation.findings
            assert result.recommendations == mock_explanation.recommendations

            # 3. Metrics in the result match the tool output EXACTLY
            assert result.metrics["business_id"] == biz_id
            assert result.metrics["as_of_date"] == expected_data.as_of_date
            assert result.metrics["count"] == expected_data.count
            assert result.metrics["total_amount"] == expected_data.total_amount
            assert result.metrics["calculation_version"] == expected_data.calculation_version
            assert len(result.metrics["ageing_bands"]) == len(expected_data.ageing_bands)
            assert len(result.metrics["invoices"]) == len(expected_data.invoices)
            assert result.metrics["invoices"][0]["invoice_id"] == "INV-2024-001"


def test_tool_failure_gives_limitation_and_no_numbers():
    """
    RULE: If a tool fails: return a clear 'could not calculate right now' limitation,
    no made-up numbers.
    """
    with patch(
        "agents.invoice.agent.get_overdue_invoices",
        side_effect=RuntimeError("Accounts receivable ledger connection lost"),
    ):
        result = invoice_agent({"business_id": "biz_broken_ledger"})

        assert isinstance(result, AgentResult)
        assert result.agent_name == "invoice"
        assert result.requires_approval is False

        # Clear limitation message
        assert "could not calculate right now" in result.summary.lower()

        # No made-up numbers: metrics must be completely empty
        assert result.metrics == {}
        assert len(result.findings) > 0
        assert len(result.recommendations) > 0


def test_empty_business_id_gives_limitation_without_calling_tool():
    """
    RULE: Empty business_id: return a limitation, do not call the tool.
    """
    with patch("agents.invoice.agent.get_overdue_invoices") as mock_tool:
        result = invoice_agent({"business_id": ""})

        # Tool must NOT be called
        mock_tool.assert_not_called()

        assert isinstance(result, AgentResult)
        assert result.agent_name == "invoice"
        assert "could not calculate right now" in result.summary.lower()
        assert "business_id" in result.summary.lower()
        assert result.metrics == {}
        assert result.requires_approval is False


def test_whitespace_business_id_gives_limitation_without_calling_tool():
    """Verify whitespace-only business_id also gives limitation without calling tool."""
    with patch("agents.invoice.agent.get_overdue_invoices") as mock_tool:
        result = invoice_agent({"business_id": "   "})

        mock_tool.assert_not_called()
        assert isinstance(result, AgentResult)
        assert result.metrics == {}


def test_openai_failure_gives_template_summary():
    """
    RULE: If OpenAI fails: return a simple template summary built from the tool numbers.
    Never crash. Use generic error text (no secrets or stack traces).
    """
    biz_id = "biz_inv_resilience"
    expected_data = get_overdue_invoices(biz_id)

    with patch.object(settings, "OPENAI_API_KEY", "sensitive-secret-token"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_client.beta.chat.completions.parse.side_effect = Exception(
                "Upstream 502 Bad Gateway with auth Bearer sensitive-secret-token"
            )

            result = invoice_agent({"business_id": biz_id})

            # Must not crash; must return valid AgentResult
            assert isinstance(result, AgentResult)
            assert result.agent_name == "invoice"
            assert result.requires_approval is False

            # Template summary must contain exact figures from tools
            assert "$20,000.00" in result.summary
            assert "4 overdue invoices" in result.summary

            # Metrics must match tool output exactly
            assert result.metrics["total_amount"] == expected_data.total_amount
            assert result.metrics["count"] == expected_data.count
            assert len(result.metrics["invoices"]) == 4

            # Findings and recommendations should be populated from template
            assert len(result.findings) >= 2
            assert any("payment reminder" in rec.lower() for rec in result.recommendations)


def test_no_openai_key_gives_template_summary():
    """Verify unconfigured OpenAI key returns template summary without attempting LLM call."""
    biz_id = "biz_no_key_test"
    with patch.object(settings, "OPENAI_API_KEY", ""):
        result = invoice_agent({"business_id": biz_id})

        assert isinstance(result, AgentResult)
        assert result.agent_name == "invoice"
        assert result.requires_approval is False

        # Template summary present with tool numbers
        assert "$20,000.00" in result.summary
        assert "4 overdue invoices" in result.summary

        # Metrics match tool numbers exactly
        assert result.metrics["total_amount"] == 20000.0
        assert result.metrics["count"] == 4
        assert result.metrics["calculation_version"] == "v1.0.0-mock"
