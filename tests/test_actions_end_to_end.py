"""
End-to-end tests for the Day 10 Action Planner.

Covers:
1. Mocked overdue-invoice tool data -> valid ActionProposals (status "pending",
   requires_approval True, action_type "send_payment_reminder").
2. Every recommendation's proposal_id matches a real proposal in the same plan.
3. Amount, days_overdue, due_date and invoice IDs in each proposal match tool data exactly.
4. Idempotency: running plan_actions twice gives identical proposal IDs.
5. No overdue invoices -> empty proposals with a plain-language caveat.
6. Tool failure -> empty plan with a plain caveat, and never raises an error.
7. More than 10 overdue invoices -> only 10 most overdue, plus caveat stating omitted count.
8. Execution isolation: no email/network/send functions are called.
9. Contract preservation: run_chat_query still returns exactly the 7 keys.
10. Serialization: proposal converts to plain dict surviving json.dumps with all expected fields.
11. Real demo business (biz_001) offline test if sample data exists; otherwise skipped.
"""

from datetime import date
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from agents.actions import ActionPlan, plan_actions
from agents.invoice.schemas import OverdueInvoice, OverdueInvoicesResult
from config.settings import settings
from graph.workflow.chat import run_chat_query, SEVEN_KEYS
import graph.workflow.full_graph as fg


def _create_mock_overdue_result(invoices, currency="INR", as_of_date="2026-11-05"):
    """Helper to build a mock OverdueInvoicesResult matching tool_source output."""
    return OverdueInvoicesResult(
        business_id="biz_001",
        as_of_date=as_of_date,
        count=len(invoices),
        total_amount=sum(inv.amount for inv in invoices),
        ageing_bands=[],
        invoices=invoices,
        calculation_version="v1.0.0-mock",
        currency=currency,
        warnings=[],
    )


def test_1_valid_action_proposals_created_from_mock_data():
    """1. Mocked overdue tool data -> valid ActionProposals with pending, requires_approval, send_payment_reminder."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-2024-001",
            customer_id="CUST-ALPHA",
            due_date="2026-10-15",
            amount=4500.0,
            days_overdue=21,
        ),
        OverdueInvoice(
            invoice_id="INV-2024-002",
            customer_id="CUST-BETA",
            due_date="2026-10-01",
            amount=3200.0,
            days_overdue=35,
        ),
    ]
    mock_result = _create_mock_overdue_result(mock_invoices, currency="INR")

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001", as_of_date="2026-11-05")

        assert isinstance(plan, ActionPlan)
        assert len(plan.proposals) == 2
        for proposal in plan.proposals:
            assert proposal.status == "pending"
            assert proposal.requires_approval is True
            assert proposal.action_type == "send_payment_reminder"
            assert proposal.target in {"INV-2024-001", "INV-2024-002"}
            assert proposal.business_id == "biz_001"
            assert "get_overdue_invoices" in proposal.evidence
            assert "2026-11-05" in proposal.evidence


def test_2_every_recommendation_links_to_real_proposal():
    """2. Every recommendation's proposal_id matches a real proposal in the same plan."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-101",
            customer_id="CUST-1",
            due_date="2026-10-20",
            amount=1200.0,
            days_overdue=16,
        ),
        OverdueInvoice(
            invoice_id="INV-102",
            customer_id="CUST-2",
            due_date="2026-10-05",
            amount=2500.0,
            days_overdue=31,
        ),
    ]
    mock_result = _create_mock_overdue_result(mock_invoices)

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001")

        proposal_ids = {p.proposal_id for p in plan.proposals}
        assert len(proposal_ids) == 2
        assert len(plan.recommendations) == 2

        for rec in plan.recommendations:
            assert rec.proposal_id is not None
            assert rec.proposal_id in proposal_ids
            assert rec.requires_approval is True


def test_3_proposal_fields_match_tool_data_exactly():
    """3. Amount, days_overdue, due_date and invoice IDs in each proposal match tool data exactly."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-EXACT-001",
            customer_id="CUST-PRECISE",
            due_date="2026-10-18",
            amount=7890.50,
            days_overdue=18,
        )
    ]
    mock_result = _create_mock_overdue_result(mock_invoices, currency="USD")

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001", as_of_date="2026-11-05")

        assert len(plan.proposals) == 1
        p = plan.proposals[0]

        # Target and payload match exact values
        assert p.target == "INV-EXACT-001"
        assert p.payload["invoice_id"] == "INV-EXACT-001"
        assert p.payload["customer_id"] == "CUST-PRECISE"
        assert p.payload["due_date"] == "2026-10-18"
        assert p.payload["amount"] == 7890.50
        assert p.payload["days_overdue"] == 18
        assert p.payload["currency"] == "USD"

        # Preview and rationale incorporate the exact figures
        assert "INV-EXACT-001" in p.payload["message_preview"]
        assert "7890.5" in p.payload["message_preview"]
        assert "18" in p.payload["message_preview"]
        assert "7890.5" in p.rationale
        assert "18" in p.rationale


def test_4_repeat_runs_produce_identical_proposal_ids():
    """4. Running plan_actions twice gives identical proposal IDs (no duplicates)."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-IDEMP-01",
            customer_id="CUST-SAME",
            due_date="2026-10-10",
            amount=999.99,
            days_overdue=26,
        )
    ]
    mock_result = _create_mock_overdue_result(mock_invoices)

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan1 = plan_actions("biz_tenant", as_of_date="2026-11-05")
        plan2 = plan_actions("biz_tenant", as_of_date="2026-11-05")

        ids1 = [p.proposal_id for p in plan1.proposals]
        ids2 = [p.proposal_id for p in plan2.proposals]

        assert ids1 == ["biz_tenant:INV-IDEMP-01:2026-11-05"]
        assert ids1 == ids2


def test_5_no_overdue_invoices_returns_empty_proposals_with_caveat():
    """5. No overdue invoices -> empty proposals with a plain-language caveat."""
    mock_result = _create_mock_overdue_result([], currency="INR")

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001")

        assert isinstance(plan, ActionPlan)
        assert len(plan.proposals) == 0
        assert len(plan.recommendations) == 0
        assert len(plan.caveats) == 1
        assert "no overdue invoices" in plan.caveats[0].lower()


def test_6_tool_failure_returns_empty_plan_without_raising():
    """6. Tool failure -> empty plan with a plain caveat, and it never raises an error."""
    with patch(
        "agents.actions.planner.get_overdue_invoices",
        side_effect=Exception("Database cluster unreachable"),
    ):
        # Must never raise
        plan = plan_actions("biz_001")

        assert isinstance(plan, ActionPlan)
        assert len(plan.proposals) == 0
        assert len(plan.recommendations) == 0
        assert len(plan.caveats) == 1
        assert "could not be retrieved" in plan.caveats[0].lower()
        # Ensure raw internal exception is not leaked in caveat
        assert "unreachable" not in plan.caveats[0].lower()


def test_7_more_than_ten_invoices_capped_at_top_ten_plus_caveat():
    """7. More than 10 overdue invoices -> only the 10 most overdue, plus a caveat."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id=f"INV-{i:02d}",
            customer_id=f"CUST-{i:02d}",
            due_date="2026-10-01",
            amount=100.0 * i,
            days_overdue=i,
        )
        for i in range(1, 15)  # 14 invoices
    ]
    mock_result = _create_mock_overdue_result(mock_invoices)

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001")

        assert len(plan.proposals) == 10
        assert len(plan.recommendations) == 10

        # Verified sorted by days_overdue descending
        days_seq = [p.payload["days_overdue"] for p in plan.proposals]
        assert days_seq == list(range(14, 4, -1))

        # 14 total - 10 planned = 4 left out
        assert len(plan.caveats) >= 1
        assert any("4" in c and "left out" in c.lower() for c in plan.caveats)


def test_8_no_external_execution_or_sending_functions_called():
    """8. Nothing is ever executed or sent: patch email/network/send functions and assert uncalled."""
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

    with (
        patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result),
        patch("smtplib.SMTP") as mock_smtp,
        patch("urllib.request.urlopen") as mock_urlopen,
        patch("socket.socket.connect") as mock_socket_connect,
    ):
        plan = plan_actions("biz_001")

        assert len(plan.proposals) == 1
        mock_smtp.assert_not_called()
        mock_urlopen.assert_not_called()
        mock_socket_connect.assert_not_called()


def test_9_run_chat_query_still_returns_exactly_seven_keys(monkeypatch):
    """9. run_chat_query still returns exactly the same 7 keys."""
    monkeypatch.setattr(
        fg.full_graph,
        "invoke",
        lambda state: {
            "final_response": {
                "summary": "Financial health is strong.",
                "evidence": ["Positive operating cash flow"],
                "caveats": ["Assumes receivables collected on schedule"],
                "recommendations": ["Follow up on overdue invoices"],
                "missing_parts": [],
                "report_type": "daily",
                "currency": "INR",
            }
        },
    )

    out = run_chat_query("biz_001", "Plan actions for overdue receivables")
    assert isinstance(out, dict)
    assert set(out.keys()) == SEVEN_KEYS
    assert len(out) == 7


def test_10_proposal_converts_to_dict_and_survives_json_dumps():
    """10. A proposal converts to a plain dict that survives json.dumps with all expected fields."""
    mock_invoices = [
        OverdueInvoice(
            invoice_id="INV-SERIAL-01",
            customer_id="CUST-JSON",
            due_date="2026-10-12",
            amount=3750.25,
            days_overdue=24,
        )
    ]
    mock_result = _create_mock_overdue_result(mock_invoices, currency="INR")

    with patch("agents.actions.planner.get_overdue_invoices", return_value=mock_result):
        plan = plan_actions("biz_001", as_of_date="2026-11-05")

        assert len(plan.proposals) == 1
        proposal = plan.proposals[0]

        # Convert to dictionary
        proposal_dict = proposal.model_dump(mode="json")
        assert isinstance(proposal_dict, dict)

        # Survives json.dumps and json.loads
        json_str = json.dumps(proposal_dict)
        assert isinstance(json_str, str)
        loaded = json.loads(json_str)

        # Expected top-level fields for Himanshu's table
        expected_fields = {
            "proposal_id",
            "action_type",
            "target",
            "payload",
            "rationale",
            "requires_approval",
            "status",
            "business_id",
            "evidence",
        }
        for field in expected_fields:
            assert field in loaded, f"Missing expected field: {field}"

        # Expected payload fields
        payload = loaded["payload"]
        expected_payload_fields = {
            "invoice_id",
            "customer_id",
            "due_date",
            "amount",
            "days_overdue",
            "currency",
            "message_preview",
        }
        for p_field in expected_payload_fields:
            assert p_field in payload, f"Missing expected payload field: {p_field}"

        assert loaded["status"] == "pending"
        assert loaded["requires_approval"] is True


def test_11_real_demo_business_biz_001_offline(tmp_path, monkeypatch):
    """11. If real demo business biz_001 data is available offline, test using it; otherwise skip."""
    samples_dir = Path(__file__).resolve().parents[1] / "data" / "samples" / "biz_001"
    if not samples_dir.exists():
        pytest.skip("Offline samples for demo business biz_001 not found at data/samples/biz_001")

    from finance_engine.etl import run_business, write_run_outputs
    from finance_engine.data import clear_cache

    as_of = "2026-11-05"
    today_date = date.fromisoformat(as_of)

    # Build processed data in isolated tmp_path without touching data/
    write_run_outputs(run_business(samples_dir, today=today_date), tmp_path / "biz_001")

    monkeypatch.setattr(settings, "PROCESSED_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "USE_REAL_FINANCE_TOOLS", True)
    monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", as_of)
    clear_cache()

    try:
        plan = plan_actions("biz_001", as_of_date=as_of)
        assert isinstance(plan, ActionPlan)
        assert plan.business_id == "biz_001"
        assert plan.as_of_date == as_of

        # biz_001 sample has 2 overdue invoices as of 2026-11-05 (inv-1002 and inv-1003)
        assert len(plan.proposals) == 2
        assert len(plan.recommendations) == 2

        proposal_targets = {p.target for p in plan.proposals}
        assert proposal_targets == {"inv-1002", "inv-1003"}

        for proposal in plan.proposals:
            assert proposal.status == "pending"
            assert proposal.requires_approval is True
            assert proposal.action_type == "send_payment_reminder"
            assert proposal.business_id == "biz_001"

        proposal_ids = {p.proposal_id for p in plan.proposals}
        for rec in plan.recommendations:
            assert rec.requires_approval is True
            assert rec.proposal_id in proposal_ids
    finally:
        clear_cache()
