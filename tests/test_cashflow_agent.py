"""
Tests for Cash Flow Agent and mock financial tools.

Verifies:
1. Metrics in the result match the tool output exactly.
2. Tool failure gives a limitation message and no numbers.
3. OpenAI failure gives the template summary.
4. Result is a valid AgentResult.
5. All OpenAI calls are mocked; no real external API calls.
"""

from unittest.mock import MagicMock, patch
import pytest
from pydantic import ValidationError

from config.settings import settings
from graph.state.models import AgentResult
from agents.cashflow.agent import cash_flow_agent
from agents.cashflow.mock_tools import forecast_cash_flow, get_financial_summary
from agents.cashflow.schemas import (
    CashFlowExplanation,
    CashFlowForecast,
    FinancialSummary,
)


def test_mock_tools_return_fixed_sample_data():
    """Verify mock tools return the exact expected schema and fixed sample numbers."""
    biz_id = "biz_mock_001"
    summary = get_financial_summary(biz_id, period="current_month")
    assert isinstance(summary, FinancialSummary)
    assert summary.business_id == biz_id
    assert summary.period == "current_month"
    assert summary.inflows == 45000.0
    assert summary.outflows == 32000.0
    assert summary.net_cash_flow == 13000.0
    assert summary.cash_balance == 78000.0
    assert summary.receivables == 15000.0
    assert summary.calculation_version == "v1.0.0-mock"

    forecast = forecast_cash_flow(biz_id, horizon_days=30)
    assert isinstance(forecast, CashFlowForecast)
    assert forecast.business_id == biz_id
    assert forecast.horizon_days == 30
    assert forecast.projected_balance == 91000.0
    assert forecast.method == "linear_runway"
    assert forecast.risk_level == "low"
    assert forecast.data_coverage == 0.95


def test_schemas_enforce_business_id_and_risk_level():
    """Verify schema validation on business_id and forecast risk_level."""
    with pytest.raises(ValidationError):
        FinancialSummary(
            business_id="",
            period="current_month",
            inflows=100.0,
            outflows=50.0,
            net_cash_flow=50.0,
            cash_balance=500.0,
            receivables=20.0,
            calculation_version="v1",
        )

    with pytest.raises(ValidationError):
        CashFlowForecast(
            business_id="biz_1",
            horizon_days=30,
            projected_balance=1000.0,
            method="linear",
            risk_level="critical",  # Invalid: not low/medium/high
            data_coverage=0.9,
        )


def test_metrics_match_tool_output_exactly_with_openai():
    """
    Verify metrics in AgentResult are copied directly from tool output,
    and narrative explanation is populated from OpenAI.
    """
    biz_id = "biz_test_corp"
    expected_summary = get_financial_summary(biz_id)
    expected_forecast = forecast_cash_flow(biz_id)

    mock_explanation = CashFlowExplanation(
        summary="Business has a strong liquidity buffer with positive monthly cash flow.",
        findings=[
            "Net cash flow is positive at $13,000.00.",
            "Ending cash balance is solid at $78,000.00.",
        ],
        recommendations=[
            "Accelerate follow-ups on $15,000.00 in outstanding receivables.",
        ],
    )

    with patch.object(settings, "OPENAI_API_KEY", "mock-openai-key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_completion = MagicMock()
            mock_completion.choices[0].message.parsed = mock_explanation
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            result = cash_flow_agent({"business_id": biz_id})

            # Verify OpenAI initialization parameters (timeout=10, max_retries=1)
            mock_openai_cls.assert_called_once_with(
                api_key="mock-openai-key",
                timeout=10,
                max_retries=1,
            )

            # 1. Result is a valid AgentResult
            assert isinstance(result, AgentResult)
            assert result.agent_name == "cashflow"
            assert result.requires_approval is False

            # 2. Summary, findings, and recommendations match AI explanation
            assert result.summary == mock_explanation.summary
            assert result.findings == mock_explanation.findings
            assert result.recommendations == mock_explanation.recommendations

            # 3. Metrics in the result match the tool output EXACTLY (not invented by AI)
            assert result.metrics["business_id"] == biz_id
            assert result.metrics["inflows"] == expected_summary.inflows
            assert result.metrics["outflows"] == expected_summary.outflows
            assert result.metrics["net_cash_flow"] == expected_summary.net_cash_flow
            assert result.metrics["cash_balance"] == expected_summary.cash_balance
            assert result.metrics["receivables"] == expected_summary.receivables
            assert result.metrics["calculation_version"] == expected_summary.calculation_version
            assert result.metrics["period"] == expected_summary.period
            assert result.metrics["horizon_days"] == expected_forecast.horizon_days
            assert result.metrics["projected_balance"] == expected_forecast.projected_balance
            assert result.metrics["method"] == expected_forecast.method
            assert result.metrics["risk_level"] == expected_forecast.risk_level
            assert result.metrics["data_coverage"] == expected_forecast.data_coverage


def test_tool_failure_gives_limitation_and_no_numbers():
    """
    RULE: If a tool fails: return a clear 'could not calculate right now' limitation,
    no made-up numbers.
    """
    with patch(
        "agents.cashflow.agent.get_financial_summary",
        side_effect=RuntimeError("Underlying ledger database unavailable"),
    ):
        result = cash_flow_agent({"business_id": "biz_broken_source"})

        assert isinstance(result, AgentResult)
        assert result.agent_name == "cashflow"
        assert result.requires_approval is False

        # Clear limitation message
        assert "could not calculate right now" in result.summary.lower()

        # No made-up numbers: metrics must be completely empty
        assert result.metrics == {}
        assert len(result.findings) > 0
        assert len(result.recommendations) > 0


def test_forecast_tool_failure_gives_limitation_and_no_numbers():
    """Verify forecast tool failure also triggers limitation with no numbers."""
    with patch(
        "agents.cashflow.agent.forecast_cash_flow",
        side_effect=ValueError("Insufficient historical data for forecasting"),
    ):
        result = cash_flow_agent({"business_id": "biz_no_history"})

        assert isinstance(result, AgentResult)
        assert "could not calculate right now" in result.summary.lower()
        assert result.metrics == {}
        assert result.requires_approval is False


def test_empty_business_id_triggers_limitation():
    """Verify empty business_id passed to cash_flow_agent fails cleanly with limitation."""
    result = cash_flow_agent({"business_id": ""})

    assert isinstance(result, AgentResult)
    assert "could not calculate right now" in result.summary.lower()
    assert result.metrics == {}


def test_openai_failure_gives_template_summary():
    """
    RULE: If OpenAI fails: return a simple template summary built from the tool numbers.
    Never crash. Use generic error text (no secrets or stack traces).
    """
    biz_id = "biz_test_resilience"
    expected_summary = get_financial_summary(biz_id)
    expected_forecast = forecast_cash_flow(biz_id)

    with patch.object(settings, "OPENAI_API_KEY", "sensitive-secret-token"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_client.beta.chat.completions.parse.side_effect = Exception(
                "Upstream 503 service unavailable with Bearer sensitive-secret-token"
            )

            result = cash_flow_agent({"business_id": biz_id})

            # Must not crash; must return valid AgentResult
            assert isinstance(result, AgentResult)
            assert result.agent_name == "cashflow"
            assert result.requires_approval is False

            # Template summary must contain exact figures from tools
            assert "$13,000.00" in result.summary
            assert "$78,000.00" in result.summary
            assert "$91,000.00" in result.summary
            assert "low" in result.summary

            # Metrics must match tool output exactly
            assert result.metrics["net_cash_flow"] == expected_summary.net_cash_flow
            assert result.metrics["cash_balance"] == expected_summary.cash_balance
            assert result.metrics["projected_balance"] == expected_forecast.projected_balance

            # Findings and recommendations should be populated from template
            assert len(result.findings) >= 2
            assert len(result.recommendations) >= 1


def test_no_openai_key_gives_template_summary():
    """Verify unconfigured OpenAI key returns template summary without attempting LLM call."""
    biz_id = "biz_no_key"
    with patch.object(settings, "OPENAI_API_KEY", ""):
        result = cash_flow_agent({"business_id": biz_id})

        assert isinstance(result, AgentResult)
        assert result.agent_name == "cashflow"
        assert result.requires_approval is False

        # Template summary present
        assert "$13,000.00" in result.summary
        assert "$78,000.00" in result.summary

        # Metrics match tool numbers exactly
        assert result.metrics["cash_balance"] == 78000.0
        assert result.metrics["inflows"] == 45000.0
        assert result.metrics["outflows"] == 32000.0
