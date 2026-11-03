"""
Invoice Agent for AgentFi.

Fetches aggregated overdue invoices and ageing bands using finance tools (currently mock),
and uses OpenAI to explain the results in plain words without inventing or calculating figures.
"""

import logging
from typing import Any, Dict, Union

from config.settings import settings
from graph.state.agent_state import AgentState
from graph.state.models import AgentResult
from agents.invoice.mock_tools import get_overdue_invoices
from agents.invoice.prompts import (
    INVOICE_SYSTEM_PROMPT,
    INVOICE_USER_PROMPT_TEMPLATE,
)
from agents.invoice.schemas import InvoiceExplanation

logger = logging.getLogger(__name__)


def invoice_agent(state: Union[AgentState, Dict[str, Any]]) -> AgentResult:
    """
    Executes the Overdue Invoice Agent logic.

    1. Validates that business_id is present (returns limitation immediately if empty).
    2. Calls finance tool to retrieve overdue invoices and ageing breakdown.
    3. Copies metrics directly from tool outputs (never invented or calculated by AI).
    4. Asks OpenAI (structured output, timeout=10, max_retries=1) to explain figures in plain English.
    5. Falls back to a deterministic template summary if OpenAI fails or has no API key.
    6. Returns an AgentResult with requires_approval=False.

    Guarantees:
    - Never crashes.
    - If business_id is empty, returns a clear limitation message without calling tools.
    - If a tool fails, returns a clear limitation message ("could not calculate right now") with no numbers.
    - If OpenAI fails or has no key, returns a template summary built from tool numbers with generic error logging.
    """
    # Extract parameters from state
    if isinstance(state, dict):
        business_id = state.get("business_id", "")
        as_of_date = state.get("as_of_date")
    else:
        business_id = getattr(state, "business_id", "")
        as_of_date = getattr(state, "as_of_date", None)

    # Empty business_id: return a limitation immediately, do not call the tool
    if not business_id or not str(business_id).strip():
        return AgentResult(
            agent_name="invoice",
            summary="Overdue invoices could not calculate right now: business_id is required.",
            metrics={},
            findings=["Validation error: business_id was not provided or is empty."],
            recommendations=["Please specify a valid business_id to inspect overdue invoices."],
            requires_approval=False,
        )

    # Step 1: Call finance tools (MOCK tools)
    try:
        overdue_result = get_overdue_invoices(
            business_id=str(business_id).strip(),
            as_of_date=as_of_date,
        )
    except Exception as tool_exc:
        logger.warning(
            "Invoice tool execution failed: %s",
            type(tool_exc).__name__,
        )
        return AgentResult(
            agent_name="invoice",
            summary="Overdue invoices could not calculate right now.",
            metrics={},
            findings=["Invoice calculation tool was unavailable or encountered an error."],
            recommendations=["Please verify your invoicing data connection or try again later."],
            requires_approval=False,
        )

    # Step 2: Copy metrics straight from the tool outputs (NOT from AI)
    metrics: Dict[str, Any] = {
        "business_id": overdue_result.business_id,
        "as_of_date": overdue_result.as_of_date,
        "count": overdue_result.count,
        "total_amount": overdue_result.total_amount,
        "ageing_bands": [band.model_dump() for band in overdue_result.ageing_bands],
        "invoices": [inv.model_dump() for inv in overdue_result.invoices],
        "calculation_version": overdue_result.calculation_version,
    }

    # Step 3: Prepare template fallback built strictly from tool numbers
    template_summary = (
        f"As of {overdue_result.as_of_date}, there are {overdue_result.count} overdue invoices "
        f"totaling ${overdue_result.total_amount:,.2f} across {len(overdue_result.ageing_bands)} ageing bands."
    )
    template_findings = [
        f"Total overdue receivables stand at ${overdue_result.total_amount:,.2f} across {overdue_result.count} invoices.",
        f"Ageing distribution: {', '.join(f'Band {b.band}: ${b.total_amount:,.2f} ({b.count} invoices)' for b in overdue_result.ageing_bands)}.",
    ]
    if overdue_result.invoices:
        most_overdue = max(overdue_result.invoices, key=lambda inv: inv.days_overdue)
        template_findings.append(
            f"Most critical overdue invoice: {most_overdue.invoice_id} ({most_overdue.days_overdue} days overdue, ${most_overdue.amount:,.2f})."
        )
        template_recommendations = [
            f"Consider sending a payment reminder for invoice {most_overdue.invoice_id}.",
            f"Prioritize collection contact with customer {most_overdue.customer_id} regarding overdue balance.",
        ]
    else:
        template_recommendations = [
            "All invoices are currently up to date; maintain standard billing procedures."
        ]

    # Step 4: Ask OpenAI to explain numbers in plain words if key is present
    api_key = settings.OPENAI_API_KEY.strip() if settings.OPENAI_API_KEY else ""

    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                timeout=10,
                max_retries=1,
            )

            ageing_text = "\n".join(
                f"- Band {b.band}: {b.count} invoices, ${b.total_amount:,.2f}"
                for b in overdue_result.ageing_bands
            )
            invoices_text = "\n".join(
                f"- Invoice {inv.invoice_id}: Customer {inv.customer_id}, Due {inv.due_date}, Amount ${inv.amount:,.2f}, {inv.days_overdue} days overdue"
                for inv in overdue_result.invoices
            )
            user_prompt = INVOICE_USER_PROMPT_TEMPLATE.format(
                business_id=overdue_result.business_id,
                as_of_date=overdue_result.as_of_date,
                count=overdue_result.count,
                total_amount=overdue_result.total_amount,
                calculation_version=overdue_result.calculation_version,
                ageing_bands_text=ageing_text,
                invoices_text=invoices_text,
            )

            completion = client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": INVOICE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=InvoiceExplanation,
            )
            explanation = completion.choices[0].message.parsed
            if explanation is not None:
                return AgentResult(
                    agent_name="invoice",
                    summary=explanation.summary,
                    metrics=metrics,  # copied straight from tool output!
                    findings=explanation.findings,
                    recommendations=explanation.recommendations,
                    requires_approval=False,
                )
        except Exception as ai_exc:
            # Generic error logging without secrets or stack traces
            logger.warning(
                "OpenAI invoice explanation unavailable: %s. Using template fallback.",
                type(ai_exc).__name__,
            )

    # Return template summary when OpenAI is unavailable, unconfigured, or failed
    return AgentResult(
        agent_name="invoice",
        summary=template_summary,
        metrics=metrics,  # copied straight from tool output!
        findings=template_findings,
        recommendations=template_recommendations,
        requires_approval=False,
    )
