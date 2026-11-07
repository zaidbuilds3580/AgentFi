"""
Financial Intelligence Agent for AgentFi.

Synthesizes cross-domain findings from Cash Flow, Invoice, and Expense specialist
agents into a unified financial health picture without calculating or inventing new figures.
"""

import logging
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from config.settings import settings
from graph.state.agent_state import AgentState
from graph.state.models import AgentResult
from agents.intelligence.prompts import (
    INTELLIGENCE_SYSTEM_PROMPT,
    INTELLIGENCE_USER_PROMPT_TEMPLATE,
)
from agents.intelligence.schemas import (
    FinancialIntelligenceExplanation,
    FinancialIntelligenceResult,
    IntelligenceRecommendation,
)

logger = logging.getLogger(__name__)

# Keywords indicating an external or consequential business action
ACTION_KEYWORDS = [
    "send",
    "remind",
    "reminder",
    "chase",
    "contact",
    "adjust",
    "cut",
    "freeze",
    "cancel",
    "terminate",
    "pay",
    "draft",
    "issue",
    "negotiate",
    "escalate",
    "collect",
    "pause",
    "halt",
    "reduce",
]


def _extract_inputs(
    state: Optional[Union[AgentState, Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Tuple[
    str,
    Optional[Union[AgentResult, Dict[str, Any]]],
    Optional[Union[AgentResult, Dict[str, Any]]],
    Optional[Union[AgentResult, Dict[str, Any]]],
]:
    """
    Extracts business_id, cashflow_result, invoice_result, and expense_result
    from state dictionary, AgentState, or keyword arguments.
    """
    state_dict: Dict[str, Any] = {}
    if isinstance(state, dict):
        state_dict = state
    elif state is not None and hasattr(state, "__dict__"):
        state_dict = state.__dict__
    elif isinstance(state, str):
        state_dict = {"business_id": state}

    business_id = str(
        kwargs.get("business_id")
        or state_dict.get("business_id")
        or ""
    ).strip()

    cf_res = kwargs.get("cashflow_result") or state_dict.get("cashflow_result")
    inv_res = kwargs.get("invoice_result") or state_dict.get("invoice_result")
    exp_res = kwargs.get("expense_result") or state_dict.get("expense_result")

    agent_results = kwargs.get("agent_results") or state_dict.get("agent_results") or []
    if isinstance(agent_results, (list, tuple)):
        for res in agent_results:
            name = ""
            if hasattr(res, "agent_name"):
                name = getattr(res, "agent_name")
            elif isinstance(res, dict):
                name = res.get("agent_name", "")
            name = str(name).lower()
            if name in ("cashflow", "cash_flow") and cf_res is None:
                cf_res = res
            elif name == "invoice" and inv_res is None:
                inv_res = res
            elif name == "expense" and exp_res is None:
                exp_res = res

    return business_id, cf_res, inv_res, exp_res


def _is_specialist_limitation(result: Optional[Union[AgentResult, Dict[str, Any]]]) -> bool:
    """Returns True if a specialist result is missing or represents a limitation."""
    if result is None:
        return True
    metrics = getattr(result, "metrics", None) if not isinstance(result, dict) else result.get("metrics")
    if not metrics:
        return True
    summary = getattr(result, "summary", "") if not isinstance(result, dict) else result.get("summary", "")
    if "could not calculate" in str(summary).lower():
        return True
    return False


def _get_all_valid_numbers(metrics: Dict[str, Any]) -> Set[float]:
    """Recursively collects all numeric values from specialist metrics."""
    valid_numbers: Set[float] = set()

    def _collect(val: Any) -> None:
        if isinstance(val, bool):
            return
        if isinstance(val, (int, float)):
            valid_numbers.add(float(val))
        elif isinstance(val, dict):
            for v in val.values():
                _collect(v)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                _collect(item)
        elif isinstance(val, str):
            for match in re.findall(r"\b\d+(?:\.\d+)?\b", val):
                try:
                    valid_numbers.add(float(match))
                except ValueError:
                    pass

    _collect(metrics)
    return valid_numbers


def _validate_ai_text_numbers(text: str, valid_numbers: Set[float]) -> bool:
    """
    Checks that every number appearing in the AI text exists in valid_numbers.
    Returns True if all numbers exist in metrics, False if any invented number is found.
    """
    pattern = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:,\d{3})*(?:\.\d+)?(?![A-Za-z0-9_])")
    matches = pattern.findall(text)

    for match in matches:
        clean = match.replace(",", "")
        try:
            val = float(clean)
        except ValueError:
            continue
        found = any(math.isclose(val, v, rel_tol=1e-5, abs_tol=1e-5) for v in valid_numbers)
        if not found:
            logger.warning(
                "Invented figure found in AI text: %r (parsed: %f). Allowed numbers: %s",
                match,
                val,
                valid_numbers,
            )
            return False
    return True


def _check_action_requires_approval(rec: IntelligenceRecommendation) -> bool:
    """Enforces ActionProposal governance for recommendations proposing actions."""
    if rec.requires_approval:
        return True
    rec_text = f"{rec.recommendation} {rec.reason}".lower()
    for kw in ACTION_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", rec_text):
            return True
    return False


def _format_specialist_section(name: str, res: Optional[Union[AgentResult, Dict[str, Any]]]) -> str:
    """Formats specialist report for prompt injection."""
    if _is_specialist_limitation(res):
        return f"{name.title()} Specialist: Data unavailable or calculation limitation returned."
    summary = getattr(res, "summary", "") if not isinstance(res, dict) else res.get("summary", "")
    metrics = getattr(res, "metrics", {}) if not isinstance(res, dict) else res.get("metrics", {})
    return f"Summary: {summary}\nKey Metrics: {metrics}"


def intelligence_agent(
    state: Optional[Union[AgentState, Dict[str, Any]]] = None,
    **kwargs: Any,
) -> FinancialIntelligenceResult:
    """
    Executes the Financial Intelligence Agent logic.

    1. Validates that business_id is present (returns limitation immediately if empty).
    2. Inspects Cash Flow, Invoice, and Expense agent results.
    3. Identifies missing or limited specialists and copies caveats word-for-word.
    4. If all three are missing/limitations, returns a limitation result with empty metrics.
    5. Copies specialist metrics directly without inventing or calculating figures.
    6. Asks OpenAI (structured output, timeout=10, max_retries=1) to synthesize findings.
    7. Verifies that every number in the AI text exists in the specialist metrics.
       If any number does not, throws AI text away and uses template fallback.
    8. Falls back to a deterministic template summary if OpenAI fails, has no key, or violates rules.
    9. Returns a FinancialIntelligenceResult (AgentResult subclass).

    Guarantees:
    - Never crashes.
    - Never calls any finance tool.
    - Never calculates new numbers (no sums, averages, percentages of its own).
    - Preserves specialist caveats word-for-word.
    """
    business_id, cf_res, inv_res, exp_res = _extract_inputs(state, **kwargs)

    # Hard Rule 4: Empty business_id gives a limitation
    if not business_id or not business_id.strip():
        return FinancialIntelligenceResult(
            agent_name="intelligence",
            overall_summary="Financial intelligence could not calculate right now: business_id is required.",
            summary="Financial intelligence could not calculate right now: business_id is required.",
            metrics={},
            findings=["Validation error: business_id was not provided or is empty."],
            links=[],
            key_risks=[],
            recommendations=[],
            caveats=["Missing business_id parameter."],
            missing_parts=["cashflow", "invoice", "expense"],
            requires_approval=False,
        )

    # Detect limitations and missing parts
    missing_parts: List[str] = []
    specialists = [("cashflow", cf_res), ("invoice", inv_res), ("expense", exp_res)]

    for name, res in specialists:
        if _is_specialist_limitation(res):
            missing_parts.append(name)

    # Copy specialist caveats word-for-word
    combined_caveats: List[str] = []
    for _, res in specialists:
        if res is not None:
            cav_list = getattr(res, "caveats", None) if not isinstance(res, dict) else res.get("caveats")
            if cav_list:
                for cav in cav_list:
                    if cav not in combined_caveats:
                        combined_caveats.append(str(cav))

    # Hard Rule 3: If all three are missing, return limitation result with empty metrics
    if len(missing_parts) == 3:
        return FinancialIntelligenceResult(
            agent_name="intelligence",
            overall_summary="Financial intelligence could not calculate right now: all specialist data unavailable.",
            summary="Financial intelligence could not calculate right now: all specialist data unavailable.",
            metrics={},
            findings=["All financial specialists (cashflow, invoice, expense) reported limitations or were unavailable."],
            links=[],
            key_risks=[],
            recommendations=[],
            caveats=combined_caveats or ["Specialist financial data unavailable or calculation error occurred."],
            missing_parts=["cashflow", "invoice", "expense"],
            requires_approval=False,
        )

    # Copy metrics straight from specialist results (never calculated by AI)
    combined_metrics: Dict[str, Any] = {}
    for name, res in specialists:
        if name not in missing_parts and res is not None:
            m = getattr(res, "metrics", {}) if not isinstance(res, dict) else res.get("metrics", {})
            if m:
                combined_metrics[name] = dict(m)
                for k, v in m.items():
                    if k not in combined_metrics:
                        combined_metrics[k] = v

    valid_numbers = _get_all_valid_numbers(combined_metrics)

    # Build deterministic template fallback using ONLY specialist numbers
    summary_parts: List[str] = []
    if "cashflow" not in missing_parts and "cash_balance" in combined_metrics:
        cb = combined_metrics["cash_balance"]
        ncf = combined_metrics["net_cash_flow"]
        inf = combined_metrics["inflows"]
        out = combined_metrics["outflows"]
        summary_parts.append(
            f"Ending cash balance is ₹{cb:,.2f} with net cash flow of ₹{ncf:,.2f} (inflows ₹{inf:,.2f}, outflows ₹{out:,.2f})."
        )
    if "invoice" not in missing_parts and "total_amount" in combined_metrics:
        cnt = combined_metrics["count"]
        tot = combined_metrics["total_amount"]
        summary_parts.append(
            f"Outstanding receivables include {cnt} overdue invoices totaling ₹{tot:,.2f}."
        )
    if "expense" not in missing_parts and "anomalies_count" in combined_metrics:
        acnt = combined_metrics["anomalies_count"]
        summary_parts.append(
            f"{acnt} anomalous expense items were identified."
        )
    if missing_parts:
        summary_parts.append(
            f"Data for {', '.join(missing_parts)} was unavailable or encountered a calculation limitation."
        )
    template_overall_summary = " ".join(summary_parts)

    template_links: List[str] = []
    if "cashflow" not in missing_parts and "invoice" not in missing_parts:
        if "total_amount" in combined_metrics and "cash_balance" in combined_metrics:
            tot = combined_metrics["total_amount"]
            cnt = combined_metrics["count"]
            cb = combined_metrics["cash_balance"]
            template_links.append(
                f"Overdue receivables of ₹{tot:,.2f} across {cnt} invoices delay cash collection and constrain cash balance (currently ₹{cb:,.2f})."
            )
    if "cashflow" not in missing_parts and "expense" not in missing_parts:
        if "anomalies_count" in combined_metrics and "net_cash_flow" in combined_metrics:
            acnt = combined_metrics["anomalies_count"]
            ncf = combined_metrics["net_cash_flow"]
            template_links.append(
                f"{acnt} anomalous expense items increase outflows against baseline spending, affecting net cash flow (₹{ncf:,.2f})."
            )
    if "cashflow" not in missing_parts and "horizon_days" in combined_metrics:
        hdays = combined_metrics["horizon_days"]
        pbal = combined_metrics["projected_balance"]
        ncf = combined_metrics["net_cash_flow"]
        template_links.append(
            f"Net cash flow of ₹{ncf:,.2f} supports the {hdays}-day projected balance of ₹{pbal:,.2f}."
        )

    template_key_risks: List[str] = []
    if "cashflow" not in missing_parts and "risk_level" in combined_metrics:
        rlevel = combined_metrics["risk_level"]
        hdays = combined_metrics["horizon_days"]
        pbal = combined_metrics["projected_balance"]
        template_key_risks.append(
            f"Liquidity risk assessed as {rlevel} over a {hdays}-day horizon with projected balance of ₹{pbal:,.2f}."
        )
    if "invoice" not in missing_parts and "total_amount" in combined_metrics:
        cnt = combined_metrics["count"]
        tot = combined_metrics["total_amount"]
        template_key_risks.append(
            f"Receivables risk: {cnt} overdue invoices total ₹{tot:,.2f}."
        )
    if "expense" not in missing_parts and "anomalies_count" in combined_metrics:
        acnt = combined_metrics["anomalies_count"]
        template_key_risks.append(
            f"Expense risk: {acnt} expense anomalies detected."
        )
    if missing_parts:
        template_key_risks.append(
            f"Data completeness risk: Missing specialist insights from {', '.join(missing_parts)}."
        )

    template_recs: List[IntelligenceRecommendation] = []
    if "invoice" not in missing_parts and "total_amount" in combined_metrics and combined_metrics.get("count", 0) > 0:
        cnt = combined_metrics["count"]
        tot = combined_metrics["total_amount"]
        template_recs.append(
            IntelligenceRecommendation(
                recommendation=f"Send payment reminders for {cnt} overdue invoices totaling ₹{tot:,.2f}.",
                priority="high",
                reason=f"Recovering ₹{tot:,.2f} in past-due receivables will improve liquid cash reserves.",
                requires_approval=True,
            )
        )
    if "expense" not in missing_parts and combined_metrics.get("anomalies_count", 0) > 0:
        acnt = combined_metrics["anomalies_count"]
        template_recs.append(
            IntelligenceRecommendation(
                recommendation="Review and investigate flagged anomalous expenses with department heads.",
                priority="medium",
                reason=f"{acnt} expenses exceeded expected baseline levels.",
                requires_approval=False,
            )
        )
    if "cashflow" not in missing_parts and "cash_balance" in combined_metrics:
        cb = combined_metrics["cash_balance"]
        risk = combined_metrics.get("risk_level", "low")
        template_recs.append(
            IntelligenceRecommendation(
                recommendation="Maintain ongoing operating expenditures within current budget limits.",
                priority="high" if risk == "high" else ("medium" if risk == "medium" else "low"),
                reason=f"Cash balance is ₹{cb:,.2f} with {risk} projected liquidity risk.",
                requires_approval=False,
            )
        )

    # Attempt OpenAI synthesis if configured
    api_key = settings.OPENAI_API_KEY.strip() if settings.OPENAI_API_KEY else ""

    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                timeout=10,
                max_retries=1,
            )

            cashflow_sec = _format_specialist_section("Cash Flow", cf_res)
            invoice_sec = _format_specialist_section("Overdue Invoice", inv_res)
            expense_sec = _format_specialist_section("Expense Anomaly", exp_res)
            caveats_sec = "\n".join(f"- {c}" for c in combined_caveats) if combined_caveats else "None"
            missing_sec = ", ".join(missing_parts) if missing_parts else "None"

            user_prompt = INTELLIGENCE_USER_PROMPT_TEMPLATE.format(
                business_id=business_id,
                currency_symbol="₹",
                cashflow_section=cashflow_sec,
                invoice_section=invoice_sec,
                expense_section=expense_sec,
                caveats_section=caveats_sec,
                missing_parts_section=missing_sec,
            )

            completion = client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": INTELLIGENCE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=FinancialIntelligenceExplanation,
            )
            explanation = completion.choices[0].message.parsed

            if explanation is not None:
                # Hard Rule 2: Check that every figure in AI text exists in specialist metrics
                ai_texts = [
                    explanation.overall_summary,
                    *explanation.links,
                    *explanation.key_risks,
                    *[f"{r.recommendation} {r.reason}" for r in explanation.recommendations],
                ]
                full_ai_text = " ".join(ai_texts)

                if _validate_ai_text_numbers(full_ai_text, valid_numbers):
                    processed_recs: List[IntelligenceRecommendation] = []
                    for rec in explanation.recommendations:
                        req_app = _check_action_requires_approval(rec)
                        processed_recs.append(
                            IntelligenceRecommendation(
                                recommendation=rec.recommendation,
                                priority=rec.priority,
                                reason=rec.reason,
                                requires_approval=req_app,
                            )
                        )

                    return FinancialIntelligenceResult(
                        agent_name="intelligence",
                        overall_summary=explanation.overall_summary,
                        summary=explanation.overall_summary,
                        metrics=combined_metrics,
                        findings=[*explanation.links, *explanation.key_risks],
                        links=explanation.links,
                        key_risks=explanation.key_risks,
                        recommendations=processed_recs,
                        caveats=combined_caveats,
                        missing_parts=missing_parts,
                        requires_approval=any(r.requires_approval for r in processed_recs),
                    )
                else:
                    logger.warning(
                        "AI text contained unverified figures not in specialist metrics. Falling back to template summary."
                    )
        except Exception as ai_exc:
            logger.warning(
                "OpenAI financial intelligence unavailable: %s. Using template fallback.",
                type(ai_exc).__name__,
            )

    # Template fallback
    return FinancialIntelligenceResult(
        agent_name="intelligence",
        overall_summary=template_overall_summary,
        summary=template_overall_summary,
        metrics=combined_metrics,
        findings=[*template_links, *template_key_risks],
        links=template_links,
        key_risks=template_key_risks,
        recommendations=template_recs,
        caveats=combined_caveats,
        missing_parts=missing_parts,
        requires_approval=any(r.requires_approval for r in template_recs),
    )


# Alias for explicit naming
financial_intelligence_agent = intelligence_agent
