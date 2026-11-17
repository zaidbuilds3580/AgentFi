"""
Tests for Action Planner (Day 10).

Verifies deterministic action planning for overdue invoice payment reminders:
- Enforces requires_approval=True on all proposals and recommendations
- Matches all mock values in payload and message previews
- Never crashes or raises on tool failures, missing business_ids, or empty data
- Generates predictable, idempotent proposal IDs
- Restricts proposals to top 10 most overdue invoices with caveats for omitted items
- Validates proposal_id linkage between recommendations and proposals
- Confirms run_chat_query response retains exactly 7 keys
"""

from unittest.mock import MagicMock, patch
import pytest

from agents.actions import ActionPlan, plan_actions
from agents.invoice.schemas import OverdueInvoice, OverdueInvoicesResult
from config.settings import settings
from graph.workflow.chat import run_chat_query, SEVEN_KEYS
import graph.workflow.full_graph as fg


def _create_mock_overdue_result(invoices, currency="INR", as_of_date="2026-11-05"):
    return OverdueInvoicesResult(
        business_id="biz_001",
        as_of_date=as_of_date,
        count=len(invoices),
        total_amount=sum(inv.amount for inv in invoices),
        ageing_bands=[],
        invoices=invoices,
        calculation_version="v1.0.0-test",
        currency=currency,
        warnings=[],
    )


def test_three_overdue_invoices_give_three_proposals():
    """Verify 3 overdue invoices result in 3 proposals, all pending with requires_approval=True."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-001",
            customer_id="CUST-ALPHA",
            due_date="2026-10-25",
            amount=1500.0,
            days_overdue=11,
        ),
        OverdueInvoice(
            invoice_id="INV-002",
            customer_id="CUST-BETA",
            due_date="2026-10-01",
            amount=4200.5,
            days_overdue=35,
        ),
        OverdueInvoice(
            invoice_id="INV-003",
            customer_id="CUST-GAMMA",
            due_date="2026-10-15",
            amount=850.0,
            days_overdue=21,
        ),
    ]
    mock_result = _create_mock_overdue_result(mock_invoices, currency="INR")

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result) as mock_tool:
        plan = plan_actions("biz_001", as_of_date="2026-11-05")

        mock_tool.assert_called_once_with("biz_001", "2026-11-05")
        assert isinstance(plan, ActionPlan)
        assert len(plan.proposals) == 3
        assert len(plan.recommendations) == 3

        for proposal in plan.proposals:
            assert proposal.status == "pending"
            assert proposal.requires_approval is True
            assert proposal.action_type == "send_payment_reminder"
            assert proposal.target in {"INV-001", "INV-002", "INV-003"}
            assert proposal.business_id == "biz_001"
            assert "get_overdue_invoices" in proposal.evidence
            assert "2026-11-05" in proposal.evidence

        for rec in plan.recommendations:
            assert rec.requires_approval is True


def test_numbers_in_payload_and_message_preview_match_mock():
    """Verify every number in payload and message_preview strictly matches mock data."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-101",
            customer_id="CUST-ACME",
            due_date="2026-10-20",
            amount=3456.78,
            days_overdue=16,
        )
    ]
    mock_result = _create_mock_overdue_result(mock_invoices, currency="USD")

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001", as_of_date="2026-11-05")

        assert len(plan.proposals) == 1
        proposal = plan.proposals[0]
        payload = proposal.payload

        # Payload values match mock data
        assert payload["invoice_id"] == "INV-101"
        assert payload["customer_id"] == "CUST-ACME"
        assert payload["due_date"] == "2026-10-20"
        assert payload["amount"] == 3456.78
        assert payload["days_overdue"] == 16
        assert payload["currency"] == "USD"

        # Message preview contains all data points
        preview = payload["message_preview"]
        assert "INV-101" in preview
        assert "CUST-ACME" in preview
        assert "2026-10-20" in preview
        assert "3456.78" in preview
        assert "16" in preview
        assert "USD" in preview

        # Rationale contains tool values
        assert "16" in proposal.rationale
        assert "3456.78" in proposal.rationale


def test_no_overdue_gives_empty_plan_plus_caveat():
    """Verify empty overdue invoices list returns an empty plan with one plain caveat."""
    mock_result = _create_mock_overdue_result([], currency="INR")

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001")

        assert isinstance(plan, ActionPlan)
        assert len(plan.proposals) == 0
        assert len(plan.recommendations) == 0
        assert len(plan.caveats) == 1
        assert "no overdue invoices" in plan.caveats[0].lower()


def test_tool_failure_gives_empty_plan_no_crash():
    """Verify tool execution failure returns an empty plan with a generic caveat without crashing."""
    with patch(
        "agents.actions.planner.get_overdue_invoices",
        side_effect=RuntimeError("Sensitive database connection timeout 500ms"),
    ):
        plan = plan_actions("biz_001")

        assert isinstance(plan, ActionPlan)
        assert len(plan.proposals) == 0
        assert len(plan.recommendations) == 0
        assert len(plan.caveats) == 1
        # Generic wording, no internal exception or sensitive details leaked
        assert "Sensitive" not in plan.caveats[0]
        assert "timeout" not in plan.caveats[0]


def test_empty_business_id():
    """Verify missing, empty, or whitespace business_id returns empty plan with plain caveat."""
    with patch("agents.actions.planner.get_overdue_invoices") as mock_tool:
        for bad_id in ["", "   ", None]:
            plan = plan_actions(bad_id)
            assert isinstance(plan, ActionPlan)
            assert len(plan.proposals) == 0
            assert len(plan.recommendations) == 0
            assert len(plan.caveats) == 1
            assert "business id is required" in plan.caveats[0].lower()
            mock_tool.assert_not_called()


def test_same_ids_on_repeat_runs():
    """Verify running plan_actions multiple times yields identical, deterministic proposal IDs."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-999",
            customer_id="CUST-XYZ",
            due_date="2026-10-10",
            amount=5000.0,
            days_overdue=26,
        )
    ]
    mock_result = _create_mock_overdue_result(mock_invoices)

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan_run1 = plan_actions("biz_test", as_of_date="2026-11-05")
        plan_run2 = plan_actions("biz_test", as_of_date="2026-11-05")

        assert plan_run1.proposals[0].proposal_id == "biz_test:INV-999:2026-11-05"
        assert plan_run1.proposals[0].proposal_id == plan_run2.proposals[0].proposal_id


def test_twelve_invoices_gives_ten_proposals_plus_caveat():
    """Verify that when 12 invoices are overdue, only the 10 most overdue are planned, plus a caveat."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id=f"INV-{i:03d}",
            customer_id=f"CUST-{i:03d}",
            due_date=f"2026-10-{max(1, 30 - i):02d}",
            amount=100.0 * i,
            days_overdue=i,  # 1 to 12 days overdue
        )
        for i in range(1, 13)
    ]
    mock_result = _create_mock_overdue_result(mock_invoices)

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001")

        assert len(plan.proposals) == 10
        assert len(plan.recommendations) == 10

        # Must be sorted descending by days_overdue (12, 11, ..., 3)
        planned_days = [p.payload["days_overdue"] for p in plan.proposals]
        assert planned_days == list(range(12, 2, -1))

        # Invoices 1 and 2 were left out (total 2 omitted)
        assert len(plan.caveats) >= 1
        assert any("2" in c and "left out" in c.lower() for c in plan.caveats)


def test_every_recommendation_proposal_id_matches_real_proposal():
    """Verify that each IntelligenceRecommendation links directly to an existing ActionProposal."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-A",
            customer_id="CUST-1",
            due_date="2026-10-01",
            amount=2000.0,
            days_overdue=35,  # >= 30 -> priority high
        ),
        OverdueInvoice(
            invoice_id="INV-B",
            customer_id="CUST-2",
            due_date="2026-10-20",
            amount=1000.0,
            days_overdue=16,  # < 30 -> priority medium
        ),
    ]
    mock_result = _create_mock_overdue_result(mock_invoices)

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001")

        proposal_ids = {p.proposal_id for p in plan.proposals}
        assert len(proposal_ids) == 2

        for rec in plan.recommendations:
            assert rec.proposal_id is not None
            assert rec.proposal_id in proposal_ids
            assert rec.requires_approval is True

        # Check priority logic
        high_rec = next(r for r in plan.recommendations if "INV-A" in r.recommendation)
        medium_rec = next(r for r in plan.recommendations if "INV-B" in r.recommendation)
        assert high_rec.priority == "high"
        assert medium_rec.priority == "medium"


def test_chat_answer_still_has_exactly_seven_keys(monkeypatch):
    """Verify chat answer contract retains exactly 7 keys without altering run_chat_query."""
    monkeypatch.setattr(
        fg.full_graph,
        "invoke",
        lambda state: {
            "final_response": {
                "summary": "Financial position is stable with positive operating cash flow.",
                "evidence": ["Operating margin is healthy"],
                "caveats": ["Receivables subject to collection timing"],
                "recommendations": ["Follow up on ageing receivables"],
                "missing_parts": [],
                "report_type": "daily",
                "currency": "INR",
            }
        },
    )

    out = run_chat_query("biz_001", "What invoices are overdue?")
    assert isinstance(out, dict)
    assert set(out.keys()) == SEVEN_KEYS
    assert len(out) == 7

    # Failure path also retains 7 keys
    fail_out = run_chat_query("", "What invoices are overdue?")
    assert isinstance(fail_out, dict)
    assert set(fail_out.keys()) == SEVEN_KEYS
    assert len(fail_out) == 7
