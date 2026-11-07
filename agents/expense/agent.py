"""
Expense Agent for AgentFi.

Fetches detected expense anomalies and baseline variances using finance tools (currently mock),
and uses OpenAI to explain the results in plain words without inventing or calculating figures.
"""

import logging
from typing import Any, Dict, Union

from config.settings import settings
from graph.state.agent_state import AgentState
from graph.state.models import AgentResult
from agents.tool_source import get_currency_symbol, get_expense_anomalies
from agents.expense.prompts import (
    EXPENSE_SYSTEM_PROMPT,
    EXPENSE_USER_PROMPT_TEMPLATE,
)
from agents.expense.schemas import ExpenseExplanation

logger = logging.getLogger(__name__)


def expense_agent(state: Union[AgentState, Dict[str, Any]]) -> AgentResult:
    """
    Executes the Expense Anomaly Agent logic.

    1. Validates that business_id is present (returns limitation immediately if empty).
    2. Calls finance tool to retrieve detected expense anomalies and baselines.
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
        start_date = state.get("start_date")
        end_date = state.get("end_date")
        today = state.get("today")
    else:
        business_id = getattr(state, "business_id", "")
        start_date = getattr(state, "start_date", None)
        end_date = getattr(state, "end_date", None)
        today = getattr(state, "today", None)

    # Empty business_id: return a limitation immediately, do not call the tool
    if not business_id or not str(business_id).strip():
        return AgentResult(
            agent_name="expense",
            summary="Expense anomalies could not calculate right now: business_id is required.",
            metrics={},
            findings=["Validation error: business_id was not provided or is empty."],
            recommendations=["Please specify a valid business_id to analyze expense anomalies."],
            caveats=["Missing business_id parameter."],
            requires_approval=False,
        )

    # Step 1: Call finance tools (via tool_source toggle)
    try:
        expense_result = get_expense_anomalies(
            business_id=str(business_id).strip(),
            start_date=start_date,
            end_date=end_date,
            today=today,
        )
    except Exception as tool_exc:
        logger.warning(
            "Expense tool execution failed: %s",
            type(tool_exc).__name__,
        )
        return AgentResult(
            agent_name="expense",
            summary="Expense anomalies could not calculate right now.",
            metrics={},
            findings=["Expense calculation tool was unavailable or encountered an error."],
            recommendations=["Please verify your expense data connection or try again later."],
            caveats=["Data unavailable or calculation error occurred."],
            requires_approval=False,
        )

    tool_warnings = list(getattr(expense_result, "warnings", []))
    currency = getattr(expense_result, "currency", "INR")
    sym = get_currency_symbol(currency)

    # Step 2: Copy metrics straight from the tool outputs (NOT from AI)
    metrics: Dict[str, Any] = {
        "business_id": expense_result.business_id,
        "start_date": expense_result.start_date,
        "end_date": expense_result.end_date,
        "anomalies_count": len(expense_result.anomalies),
        "anomalies": [anomaly.model_dump() for anomaly in expense_result.anomalies],
        "calculation_version": expense_result.calculation_version,
        "currency": currency,
        "warnings": list(tool_warnings),
    }

    # Step 3: Prepare template fallback built strictly from tool numbers
    if expense_result.anomalies:
        high_sev_count = sum(1 for a in expense_result.anomalies if a.severity == "high")
        template_summary = (
            f"Identified {len(expense_result.anomalies)} expense anomalies between "
            f"{expense_result.start_date} and {expense_result.end_date}, including "
            f"{high_sev_count} high-severity variance."
        )
        template_findings = [
            f"Flagged {a.anomaly_id} ({a.vendor}, {a.category}): {sym}{a.amount:,.2f} vs baseline {sym}{a.expected_baseline:,.2f} [{a.severity} severity] - {a.rule}."
            for a in expense_result.anomalies
        ]
        primary_anomaly = expense_result.anomalies[0]
        template_recommendations = [
            f"Review invoice for vendor {primary_anomaly.vendor} ({primary_anomaly.anomaly_id}) exceeding baseline by {sym}{primary_anomaly.amount - primary_anomaly.expected_baseline:,.2f}.",
            "Audit recurring subscriptions and verify departmental spending limits against budget caps.",
        ]
    else:
        template_summary = (
            f"No expense anomalies found between {expense_result.start_date} and {expense_result.end_date}."
        )
        template_findings = [
            "All expenditures conform to expected baselines; no spending anomalies were detected."
        ]
        template_recommendations = [
            "All expenditures conform to expected baselines; maintain regular spending reviews."
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

            if expense_result.anomalies:
                anomalies_text = "\n".join(
                    f"- Anomaly {a.anomaly_id}: Vendor '{a.vendor}', Category '{a.category}', Amount {sym}{a.amount:,.2f}, Expected Baseline {sym}{a.expected_baseline:,.2f}, Severity: {a.severity}, Rule: {a.rule}"
                    for a in expense_result.anomalies
                )
            else:
                anomalies_text = "No anomalies detected in this period."

            warnings_text = "\n".join(f"- {w}" for w in tool_warnings) if tool_warnings else "None"
            user_prompt = EXPENSE_USER_PROMPT_TEMPLATE.format(
                business_id=expense_result.business_id,
                start_date=expense_result.start_date,
                end_date=expense_result.end_date,
                anomalies_count=len(expense_result.anomalies),
                calculation_version=expense_result.calculation_version,
                anomalies_text=anomalies_text,
                currency=currency,
                currency_symbol=sym,
                warnings_text=warnings_text,
            )

            completion = client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": EXPENSE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=ExpenseExplanation,
            )
            explanation = completion.choices[0].message.parsed
            if explanation is not None:
                return AgentResult(
                    agent_name="expense",
                    summary=explanation.summary,
                    metrics=metrics,  # copied straight from tool output!
                    findings=explanation.findings,
                    recommendations=explanation.recommendations,
                    caveats=list(tool_warnings),
                    requires_approval=False,
                )
        except Exception as ai_exc:
            # Generic error logging without secrets or stack traces
            logger.warning(
                "OpenAI expense explanation unavailable: %s. Using template fallback.",
                type(ai_exc).__name__,
            )

    # Return template summary when OpenAI is unavailable, unconfigured, or failed
    return AgentResult(
        agent_name="expense",
        summary=template_summary,
        metrics=metrics,  # copied straight from tool output!
        findings=template_findings,
        recommendations=template_recommendations,
        caveats=list(tool_warnings),
        requires_approval=False,
    )
