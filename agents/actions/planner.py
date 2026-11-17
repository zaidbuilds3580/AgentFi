"""
Deterministic Action Planner for AgentFi.

Plans external business actions (e.g. sending payment reminders) based strictly
on deterministic finance tool data. Enforces human-in-the-loop governance:
proposals and action recommendations always require approval.
"""

import logging
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

from config.settings import settings
from graph.state.models import ActionProposal
from agents.intelligence.schemas import IntelligenceRecommendation
from agents.tool_source import get_overdue_invoices

logger = logging.getLogger(__name__)


class ActionPlan(BaseModel):
    """
    Structured action plan containing proposed actions and recommendations.
    """
    business_id: str = Field(
        default="",
        description="Tenant business identifier."
    )
    as_of_date: str = Field(
        default="",
        description="Reference date for action planning (YYYY-MM-DD)."
    )
    proposals: List[ActionProposal] = Field(
        default_factory=list,
        description="List of proposed actions requiring human sign-off."
    )
    recommendations: List[IntelligenceRecommendation] = Field(
        default_factory=list,
        description="List of prioritized intelligence recommendations."
    )
    caveats: List[str] = Field(
        default_factory=list,
        description="Limitations, data gaps, or omitted items."
    )
    currency: str = Field(
        default="INR",
        description="Base currency code for invoice figures."
    )


def plan_actions(
    business_id: str,
    as_of_date: Optional[str] = None,
) -> ActionPlan:
    """
    Generates an ActionPlan for overdue invoice payment reminders.

    Guarantees:
    - Never raises an exception.
    - Uses DEMO_AS_OF_DATE as default, never system date.
    - Generates predictable proposal IDs: f"{business_id}:{invoice_id}:{as_of_date}".
    - Enforces requires_approval=True on all proposals and recommendations.
    - Limits proposals to the 10 most overdue invoices, adding a caveat if any are omitted.
    - Returns an empty plan with a single generic caveat if business_id is empty,
      if the tool fails, or if no overdue invoices exist.
    - Executes no external network, email, or database operations.
    """
    try:
        # Resolve effective date: default to DEMO_AS_OF_DATE, never system date
        if as_of_date is not None and str(as_of_date).strip():
            effective_as_of_date = str(as_of_date).strip()
        else:
            demo_date = getattr(settings, "DEMO_AS_OF_DATE", None)
            if demo_date and str(demo_date).strip():
                effective_as_of_date = str(demo_date).strip()
            else:
                effective_as_of_date = "2026-11-05"

        # Validate business_id
        if not business_id or not str(business_id).strip():
            return ActionPlan(
                business_id="",
                as_of_date=effective_as_of_date,
                proposals=[],
                recommendations=[],
                caveats=["Business ID is required to plan actions."],
                currency="INR",
            )

        clean_business_id = str(business_id).strip()

        # Call overdue invoices tool
        try:
            overdue_result = get_overdue_invoices(clean_business_id, effective_as_of_date)
        except Exception as tool_exc:
            logger.warning(
                "Tool execution failed in action planner: %s",
                type(tool_exc).__name__,
            )
            return ActionPlan(
                business_id=clean_business_id,
                as_of_date=effective_as_of_date,
                proposals=[],
                recommendations=[],
                caveats=["Overdue invoices could not be retrieved at this time."],
                currency="INR",
            )

        currency = getattr(overdue_result, "currency", "INR") or "INR"
        raw_invoices = list(getattr(overdue_result, "invoices", []) or [])

        if not raw_invoices:
            return ActionPlan(
                business_id=clean_business_id,
                as_of_date=effective_as_of_date,
                proposals=[],
                recommendations=[],
                caveats=["No overdue invoices found for this business."],
                currency=currency,
            )

        def _get_days_overdue(inv: Any) -> int:
            if isinstance(inv, dict):
                return int(inv.get("days_overdue", 0))
            return int(getattr(inv, "days_overdue", 0))

        # Sort by days_overdue, highest first
        sorted_invoices = sorted(raw_invoices, key=_get_days_overdue, reverse=True)

        caveats: List[str] = []
        if len(sorted_invoices) > 10:
            left_out = len(sorted_invoices) - 10
            caveats.append(
                f"{left_out} overdue invoices were left out of this plan (only the 10 most overdue are included)."
            )
            planned_invoices = sorted_invoices[:10]
        else:
            planned_invoices = sorted_invoices

        proposals: List[ActionProposal] = []
        recommendations: List[IntelligenceRecommendation] = []

        for inv in planned_invoices:
            if isinstance(inv, dict):
                inv_id = str(inv.get("invoice_id", ""))
                cust_id = str(inv.get("customer_id", ""))
                due_date = str(inv.get("due_date", ""))
                amount = float(inv.get("amount", 0.0))
                days_overdue = int(inv.get("days_overdue", 0))
            else:
                inv_id = str(getattr(inv, "invoice_id", ""))
                cust_id = str(getattr(inv, "customer_id", ""))
                due_date = str(getattr(inv, "due_date", ""))
                amount = float(getattr(inv, "amount", 0.0))
                days_overdue = int(getattr(inv, "days_overdue", 0))

            proposal_id = f"{clean_business_id}:{inv_id}:{effective_as_of_date}"

            message_preview = (
                f"Payment reminder: Invoice {inv_id} for customer {cust_id} "
                f"of amount {currency} {amount} was due on {due_date} "
                f"and is now {days_overdue} days overdue. Please process payment."
            )

            rationale = (
                f"Invoice is {days_overdue} days overdue with an outstanding balance of {currency} {amount}."
            )

            payload: Dict[str, Any] = {
                "invoice_id": inv_id,
                "customer_id": cust_id,
                "due_date": due_date,
                "amount": amount,
                "days_overdue": days_overdue,
                "currency": currency,
                "message_preview": message_preview,
            }

            proposal = ActionProposal(
                proposal_id=proposal_id,
                action_type="send_payment_reminder",
                target=inv_id,
                payload=payload,
                rationale=rationale,
                requires_approval=True,
                status="pending",
                business_id=clean_business_id,
                evidence=["get_overdue_invoices", effective_as_of_date],
            )
            proposals.append(proposal)

            priority: Literal["high", "medium"] = "high" if days_overdue >= 30 else "medium"
            rec_reason = (
                f"Invoice {inv_id} for customer {cust_id} is {days_overdue} days overdue "
                f"for amount {currency} {amount}."
            )
            recommendation = IntelligenceRecommendation(
                recommendation=f"Send payment reminder for invoice {inv_id} ({currency} {amount}).",
                priority=priority,
                reason=rec_reason,
                requires_approval=True,
                proposal_id=proposal_id,
            )
            recommendations.append(recommendation)

        return ActionPlan(
            business_id=clean_business_id,
            as_of_date=effective_as_of_date,
            proposals=proposals,
            recommendations=recommendations,
            caveats=caveats,
            currency=currency,
        )

    except Exception as exc:
        logger.warning("Unexpected error in plan_actions: %s", type(exc).__name__)
        return ActionPlan(
            business_id=str(business_id).strip() if business_id else "",
            as_of_date=as_of_date or getattr(settings, "DEMO_AS_OF_DATE", "2026-11-05") or "2026-11-05",
            proposals=[],
            recommendations=[],
            caveats=["An unexpected error occurred while planning actions."],
            currency="INR",
        )
