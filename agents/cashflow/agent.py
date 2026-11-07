"""
Cash Flow Agent for AgentFi.

Fetches aggregated financial summaries and forecasts using finance tools (currently mock),
and uses OpenAI to explain the results in plain words without inventing or calculating figures.
"""

import logging
from typing import Any, Dict, Union

from config.settings import settings
from graph.state.agent_state import AgentState
from graph.state.models import AgentResult
from agents.tool_source import forecast_cash_flow, get_currency_symbol, get_financial_summary
from agents.cashflow.prompts import (
    CASHFLOW_SYSTEM_PROMPT,
    CASHFLOW_USER_PROMPT_TEMPLATE,
)
from agents.cashflow.schemas import CashFlowExplanation

logger = logging.getLogger(__name__)


def cash_flow_agent(state: Union[AgentState, Dict[str, Any]]) -> AgentResult:
    """
    Executes the Cash Flow Agent logic.

    1. Calls finance tools to retrieve financial summary and cash flow forecast.
    2. Copies metrics directly from tool outputs (never invented or calculated by AI).
    3. Asks OpenAI (structured output, timeout=10, max_retries=1) to explain figures in plain English.
    4. Falls back to a deterministic template summary if OpenAI fails or has no API key.
    5. Returns an AgentResult with requires_approval=False.

    Guarantees:
    - Never crashes.
    - If a tool fails, returns a clear limitation message ("could not calculate right now") with no numbers.
    - If OpenAI fails or has no key, returns a template summary built from tool numbers with generic error logging.
    """
    # Extract parameters from state
    if isinstance(state, dict):
        business_id = state.get("business_id", "")
        period = state.get("period", "current_month")
        horizon_days = state.get("horizon_days", 30)
        today = state.get("today")
    else:
        business_id = getattr(state, "business_id", "")
        period = getattr(state, "period", "current_month")
        horizon_days = getattr(state, "horizon_days", 30)
        today = getattr(state, "today", None)

    # Step 1: Call finance tools (via tool_source toggle)
    try:
        financial_summary = get_financial_summary(
            business_id=str(business_id),
            period=period or "current_month",
            today=today,
        )
        cash_flow_forecast = forecast_cash_flow(
            business_id=str(business_id),
            horizon_days=horizon_days or 30,
            today=today,
        )
    except Exception as tool_exc:
        logger.warning(
            "Cash flow tool execution failed: %s",
            type(tool_exc).__name__,
        )
        return AgentResult(
            agent_name="cashflow",
            summary="Cash flow metrics could not calculate right now.",
            metrics={},
            findings=["Financial calculation tool was unavailable or encountered an error."],
            recommendations=["Please verify your financial data connection or try again later."],
            caveats=["Data unavailable or calculation error occurred."],
            requires_approval=False,
        )

    # Combine tool warnings word-for-word
    tool_warnings: list[str] = []
    for w in getattr(financial_summary, "warnings", []):
        if w not in tool_warnings:
            tool_warnings.append(w)
    for w in getattr(cash_flow_forecast, "warnings", []):
        if w not in tool_warnings:
            tool_warnings.append(w)

    currency = getattr(financial_summary, "currency", "INR")
    sym = get_currency_symbol(currency)

    # Step 2: Copy metrics straight from the tool outputs (NOT from AI)
    metrics: Dict[str, Any] = {
        "business_id": financial_summary.business_id,
        "period": financial_summary.period,
        "inflows": financial_summary.inflows,
        "outflows": financial_summary.outflows,
        "net_cash_flow": financial_summary.net_cash_flow,
        "cash_balance": financial_summary.cash_balance,
        "receivables": financial_summary.receivables,
        "calculation_version": financial_summary.calculation_version,
        "horizon_days": cash_flow_forecast.horizon_days,
        "projected_balance": cash_flow_forecast.projected_balance,
        "method": cash_flow_forecast.method,
        "risk_level": cash_flow_forecast.risk_level,
        "data_coverage": cash_flow_forecast.data_coverage,
        "currency": currency,
        "warnings": list(tool_warnings),
    }

    # Step 3: Prepare template fallback built strictly from tool numbers
    template_summary = (
        f"For {financial_summary.period}, total inflows were {sym}{financial_summary.inflows:,.2f} "
        f"and outflows were {sym}{financial_summary.outflows:,.2f}, resulting in net cash flow of "
        f"{sym}{financial_summary.net_cash_flow:,.2f} and an ending cash balance of {sym}{financial_summary.cash_balance:,.2f}. "
        f"Over a {cash_flow_forecast.horizon_days}-day horizon, projected cash balance is "
        f"{sym}{cash_flow_forecast.projected_balance:,.2f} with a {cash_flow_forecast.risk_level} risk level."
    )
    template_findings = [
        f"Net cash flow is {sym}{financial_summary.net_cash_flow:,.2f} (inflows: {sym}{financial_summary.inflows:,.2f}, outflows: {sym}{financial_summary.outflows:,.2f}).",
        f"Outstanding customer receivables total {sym}{financial_summary.receivables:,.2f}.",
        f"Projected {cash_flow_forecast.horizon_days}-day balance is {sym}{cash_flow_forecast.projected_balance:,.2f} assessed at {cash_flow_forecast.risk_level} risk.",
    ]
    template_recommendations = [
        f"Monitor collection efforts for {sym}{financial_summary.receivables:,.2f} in pending receivables.",
        "Maintain ongoing operating expenditures within current levels to preserve cash reserves.",
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
            warnings_text = "\n".join(f"- {w}" for w in tool_warnings) if tool_warnings else "None"
            user_prompt = CASHFLOW_USER_PROMPT_TEMPLATE.format(
                business_id=financial_summary.business_id,
                period=financial_summary.period,
                inflows=financial_summary.inflows,
                outflows=financial_summary.outflows,
                net_cash_flow=financial_summary.net_cash_flow,
                cash_balance=financial_summary.cash_balance,
                receivables=financial_summary.receivables,
                calculation_version=financial_summary.calculation_version,
                horizon_days=cash_flow_forecast.horizon_days,
                projected_balance=cash_flow_forecast.projected_balance,
                method=cash_flow_forecast.method,
                risk_level=cash_flow_forecast.risk_level,
                data_coverage=cash_flow_forecast.data_coverage,
                currency=currency,
                currency_symbol=sym,
                warnings_text=warnings_text,
            )
            completion = client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": CASHFLOW_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=CashFlowExplanation,
            )
            explanation = completion.choices[0].message.parsed
            if explanation is not None:
                return AgentResult(
                    agent_name="cashflow",
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
                "OpenAI cash flow explanation unavailable: %s. Using template fallback.",
                type(ai_exc).__name__,
            )

    # Return template summary when OpenAI is unavailable, unconfigured, or failed
    return AgentResult(
        agent_name="cashflow",
        summary=template_summary,
        metrics=metrics,  # copied straight from tool output!
        findings=template_findings,
        recommendations=template_recommendations,
        caveats=list(tool_warnings),
        requires_approval=False,
    )
