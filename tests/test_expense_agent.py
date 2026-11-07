"""
Tests for Expense Agent and mock financial tools.

Verifies:
1. Metrics in the result match the tool output exactly.
2. Tool failure gives a limitation message and no numbers.
3. OpenAI failure gives the template summary.
4. No OpenAI key gives the template summary.
5. Empty business_id gives a limitation without calling the tool.
6. Result is a valid AgentResult.
7. Mock tool sample data contains anomalies with different severities (low, medium, high).
"""

from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError

from config.settings import settings
from graph.state.models import AgentResult
from agents.expense.agent import expense_agent
from agents.expense.mock_tools import get_expense_anomalies
from agents.expense.schemas import (
    ExpenseAnomaliesResult,
    ExpenseAnomaly,
    ExpenseExplanation,
)


@pytest.fixture(autouse=True)
def force_mock_finance_tools(monkeypatch):
    """Force mock finance tools for existing mock-data tests."""
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", False)


def test_mock_tools_return_fixed_sample_data_with_different_severities():
    """
    Verify mock tool returns the expected schema and 3 anomalies covering
    different severity levels (high, medium, low).
    """
    biz_id = "biz_exp_001"
    result = get_expense_anomalies(biz_id, start_date="2026-10-01", end_date="2026-10-31")

    assert isinstance(result, ExpenseAnomaliesResult)
    assert result.business_id == biz_id
    assert result.start_date == "2026-10-01"
    assert result.end_date == "2026-10-31"
    assert result.calculation_version == "v1.0.0-mock"

    # Verify 3 anomalies
    assert len(result.anomalies) == 3

    # Verify different severities are present
    severities = {a.severity for a in result.anomalies}
    assert severities == {"low", "medium", "high"}

    # Verify anomaly structure
    high_anom = next(a for a in result.anomalies if a.severity == "high")
    assert high_anom.anomaly_id == "ANOM-2024-001"
    assert high_anom.category == "Software & SaaS"
    assert high_anom.vendor == "CloudScale Hosting"
    assert high_anom.amount == 8450.0
    assert high_anom.expected_baseline == 2100.0
    assert len(high_anom.evidence_refs) > 0


def test_schemas_enforce_business_id_and_severity():
    """Verify ExpenseAnomaliesResult enforces business_id and ExpenseAnomaly validates severity."""
    with pytest.raises(ValidationError):
        ExpenseAnomaliesResult(
            business_id="",
            start_date="2026-10-01",
            end_date="2026-10-31",
            anomalies=[],
        )

    with pytest.raises(ValidationError):
        ExpenseAnomaly(
            anomaly_id="ANOM-X",
            category="Travel",
            vendor="Airline",
            amount=500.0,
            expected_baseline=200.0,
            severity="critical",  # Invalid: must be low/medium/high
            rule="Flagged",
            evidence_refs=[],
        )


def test_metrics_match_tool_output_exactly_with_openai():
    """
    Verify metrics in AgentResult are copied directly from tool output,
    and narrative explanation is populated from OpenAI.
    """
    biz_id = "biz_corp_spending"
    expected_data = get_expense_anomalies(biz_id)

    mock_explanation = ExpenseExplanation(
        summary="Detected 3 expense anomalies, highlighting a major spike in SaaS hosting costs.",
        findings=[
            "CloudScale Hosting was billed at $8,450.00 versus an expected baseline of $2,100.00.",
            "Office supplies had an unexpected volume surge totaling $2,650.00.",
        ],
        recommendations=[
            "Review CloudScale Hosting invoice details and infrastructure auto-scaling rules.",
            "Verify departmental approval for bulk office supply orders.",
        ],
    )

    with patch.object(settings, "OPENAI_API_KEY", "mock-openai-key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_completion = MagicMock()
            mock_completion.choices[0].message.parsed = mock_explanation
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            result = expense_agent({"business_id": biz_id})

            # Verify OpenAI initialization parameters (timeout=10, max_retries=1)
            mock_openai_cls.assert_called_once_with(
                api_key="mock-openai-key",
                timeout=10,
                max_retries=1,
            )

            # 1. Result is a valid AgentResult
            assert isinstance(result, AgentResult)
            assert result.agent_name == "expense"
            assert result.requires_approval is False

            # 2. Summary, findings, and recommendations match AI explanation
            assert result.summary == mock_explanation.summary
            assert result.findings == mock_explanation.findings
            assert result.recommendations == mock_explanation.recommendations

            # 3. Metrics in the result match the tool output EXACTLY
            assert result.metrics["business_id"] == biz_id
            assert result.metrics["start_date"] == expected_data.start_date
            assert result.metrics["end_date"] == expected_data.end_date
            assert result.metrics["anomalies_count"] == len(expected_data.anomalies)
            assert result.metrics["calculation_version"] == expected_data.calculation_version
            assert len(result.metrics["anomalies"]) == 3
            assert result.metrics["anomalies"][0]["vendor"] == "CloudScale Hosting"
            assert result.metrics["anomalies"][0]["amount"] == 8450.0


def test_tool_failure_gives_limitation_and_no_numbers():
    """
    RULE: If a tool fails: return a clear 'could not calculate right now' limitation,
    no made-up numbers.
    """
    with patch(
        "agents.expense.agent.get_expense_anomalies",
        side_effect=RuntimeError("Expense classification service unreachable"),
    ):
        result = expense_agent({"business_id": "biz_broken_expense"})

        assert isinstance(result, AgentResult)
        assert result.agent_name == "expense"
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
    with patch("agents.expense.agent.get_expense_anomalies") as mock_tool:
        result = expense_agent({"business_id": ""})

        # Tool must NOT be called
        mock_tool.assert_not_called()

        assert isinstance(result, AgentResult)
        assert result.agent_name == "expense"
        assert "could not calculate right now" in result.summary.lower()
        assert "business_id" in result.summary.lower()
        assert result.metrics == {}
        assert result.requires_approval is False


def test_whitespace_business_id_gives_limitation_without_calling_tool():
    """Verify whitespace-only business_id also gives limitation without calling tool."""
    with patch("agents.expense.agent.get_expense_anomalies") as mock_tool:
        result = expense_agent({"business_id": "   "})

        mock_tool.assert_not_called()
        assert isinstance(result, AgentResult)
        assert result.metrics == {}


def test_openai_failure_gives_template_summary():
    """
    RULE: If OpenAI fails: return a simple template summary built from the tool numbers.
    Never crash. Use generic error text (no secrets or stack traces).
    """
    biz_id = "biz_exp_resilience"
    expected_data = get_expense_anomalies(biz_id)

    with patch.object(settings, "OPENAI_API_KEY", "sensitive-secret-token"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_client.beta.chat.completions.parse.side_effect = Exception(
                "Upstream 504 Gateway Timeout with Bearer sensitive-secret-token"
            )

            result = expense_agent({"business_id": biz_id})

            # Must not crash; must return valid AgentResult
            assert isinstance(result, AgentResult)
            assert result.agent_name == "expense"
            assert result.requires_approval is False

            # Template summary must contain exact figures from tools
            assert "Identified 3 expense anomalies" in result.summary
            assert "1 high-severity variance" in result.summary

            # Metrics must match tool output exactly
            assert result.metrics["anomalies_count"] == 3
            assert len(result.metrics["anomalies"]) == 3
            assert result.metrics["anomalies"][0]["amount"] == 8450.0

            # Findings and recommendations should be populated from template
            assert len(result.findings) >= 3
            assert any("CloudScale Hosting" in rec for rec in result.recommendations)


def test_no_openai_key_gives_template_summary():
    """Verify unconfigured OpenAI key returns template summary without attempting LLM call."""
    biz_id = "biz_no_key_exp"
    with patch.object(settings, "OPENAI_API_KEY", ""):
        result = expense_agent({"business_id": biz_id})

        assert isinstance(result, AgentResult)
        assert result.agent_name == "expense"
        assert result.requires_approval is False

        # Template summary present with tool numbers
        assert "Identified 3 expense anomalies" in result.summary

        # Metrics match tool numbers exactly
        assert result.metrics["anomalies_count"] == 3
        assert result.metrics["anomalies"][0]["vendor"] == "CloudScale Hosting"
        assert result.metrics["calculation_version"] == "v1.0.0-mock"
