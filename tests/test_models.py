"""
Tests for AgentFi Pydantic models and state schemas.
"""

import pytest
from pydantic import ValidationError

from graph.state.models import (
    ActionProposal,
    AgentResult,
    FinalResponse,
    Intent,
    RouterDecision,
)
from graph.state.agent_state import (
    AgentState,
    validate_agent_state,
    validate_business_id,
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


def test_final_response_model():
    """Verify FinalResponse instantiation with summary, evidence, caveats, and recommendations."""
    resp = FinalResponse(
        summary="Business is operating cash-flow positive with 3.5 months runway.",
        evidence=["Inflows exceed outflows by $13,000", "Cash balance at $78,000"],
        caveats=["Assumes receivables are collected within 30 days"],
        recommendations=["Follow up on unpaid invoices", "Maintain current expense caps"],
    )
    assert "cash-flow positive" in resp.summary
    assert len(resp.evidence) == 2
    assert len(resp.caveats) == 1
    assert len(resp.recommendations) == 2

    # Verify default empty lists
    resp_minimal = FinalResponse(summary="Summary only.")
    assert resp_minimal.summary == "Summary only."
    assert resp_minimal.evidence == []
    assert resp_minimal.caveats == []
    assert resp_minimal.recommendations == []

    # Summary is required
    with pytest.raises(ValidationError):
        FinalResponse()


def test_validate_business_id_must_not_be_empty():
    """Verify business_id validation raises clear error on empty/missing/whitespace values."""
    assert validate_business_id("biz_1234") == "biz_1234"
    assert validate_business_id("  biz_space  ") == "biz_space"

    with pytest.raises(ValueError, match="business_id must not be empty"):
        validate_business_id("")

    with pytest.raises(ValueError, match="business_id must not be empty"):
        validate_business_id("   ")

    with pytest.raises(ValueError, match="business_id must not be empty"):
        validate_business_id(None)


def test_validate_agent_state():
    """Verify validate_agent_state ensures non-empty business_id in state dictionary."""
    valid_state: AgentState = {
        "business_id": "biz_real_01",
        "request_id": "req_1001",
    }
    validate_agent_state(valid_state)  # Should not raise

    invalid_state: AgentState = {
        "business_id": "",
        "request_id": "req_1002",
    }
    with pytest.raises(ValueError, match="business_id must not be empty"):
        validate_agent_state(invalid_state)

    missing_biz_state: AgentState = {
        "request_id": "req_1003",
    }
    with pytest.raises(ValueError, match="business_id must not be empty"):
        validate_agent_state(missing_biz_state)


def test_agent_state_fields():
    """Verify AgentState supports request_id and final_response."""
    state: AgentState = {
        "business_id": "biz_tenant_1",
        "request_id": "req_abc_123",
        "final_response": FinalResponse(
            summary="Test summary",
            evidence=["Ev1"],
            caveats=["Cav1"],
            recommendations=["Rec1"],
        ),
        "status": "success",
    }
    assert state["request_id"] == "req_abc_123"
    assert state["business_id"] == "biz_tenant_1"
    assert state["final_response"].summary == "Test summary"


