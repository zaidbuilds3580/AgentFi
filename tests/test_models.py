"""
Tests for AgentFi Pydantic models and state schemas.
"""

import pytest
from pydantic import ValidationError

from graph.state.models import (
    ActionProposal,
    AgentResult,
    Intent,
    RouterDecision,
)


def test_intent_enum_values():
    """Verify all required Intent values exist."""
    expected_values = {
        "cash_flow",
        "invoice",
        "expense",
        "financial_intelligence",
        "reporting",
        "multi_agent",
        "unknown",
    }
    actual_values = {i.value for i in Intent}
    assert expected_values == actual_values


def test_router_decision_model():
    """Verify RouterDecision instantiation and validation."""
    decision = RouterDecision(
        intent=Intent.cash_flow,
        target_agents=["cashflow"],
        confidence=0.95,
        reasoning="Matched keyword cash",
    )
    assert decision.intent == Intent.cash_flow
    assert decision.confidence == 0.95

    # Confidence must be between 0.0 and 1.0
    with pytest.raises(ValidationError):
        RouterDecision(
            intent=Intent.cash_flow,
            target_agents=["cashflow"],
            confidence=1.5,  # Invalid: > 1.0
            reasoning="Too confident",
        )


def test_agent_result_model():
    """Verify AgentResult handles summaries and aggregated metrics."""
    result = AgentResult(
        agent_name="cashflow",
        summary="Cash reserves are sufficient for 4.2 months.",
        metrics={"runway_months": 4.2, "burn_rate": 12000.0},
        findings=["Burn rate decreased by 5% this month."],
        recommendations=["Maintain current inventory spending."],
        requires_approval=False,
    )
    assert result.agent_name == "cashflow"
    assert result.metrics["runway_months"] == 4.2
    assert not result.requires_approval


def test_action_proposal_approval_invariant():
    """
    CRITICAL RULE:
    ActionProposal.requires_approval must always be True to preserve
    human-in-the-loop control for any SME financial action.
    """
    # Valid proposal with default requires_approval=True
    proposal = ActionProposal(
        proposal_id="prop_001",
        action_type="send_payment_reminder",
        target="client_invoice_491",
        payload={"invoice_id": "491", "days_overdue": 14},
        rationale="Invoice 491 is overdue by 14 days.",
        status="pending",
    )
    assert proposal.requires_approval is True
    assert proposal.status == "pending"

    # Attempting to set requires_approval=False MUST fail validation
    with pytest.raises(ValidationError) as exc_info:
        ActionProposal(
            proposal_id="prop_002",
            action_type="auto_pay_vendor",
            target="vendor_bill_99",
            payload={},
            rationale="Automated payment without approval",
            requires_approval=False,  # Disallowed!
            status="pending",
        )
    assert "requires_approval must always be True" in str(exc_info.value)

