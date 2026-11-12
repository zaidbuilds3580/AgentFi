"""
Executive Reporting Agent for AgentFi.

Synthesizes cross-domain financial intelligence and specialist metrics into
daily or monthly executive summary reports without calling finance tools
or calculating new figures.
"""

import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from config.settings import settings
from graph.state.agent_state import AgentState
from graph.state.models import AgentResult
from agents.intelligence.agent import (
    _get_all_valid_numbers,
    _is_specialist_limitation,
    _validate_ai_text_numbers,
)
from agents.intelligence.schemas import (
    FinancialIntelligenceResult,
    IntelligenceRecommendation,
)
from agents.reporting.prompts import (
    REPORTING_SYSTEM_PROMPT,
    REPORTING_USER_PROMPT_TEMPLATE,
)
from agents.reporting.schemas import (
    ReportEvidenceItem,
    ReportingExplanation,
    ReportingResult,
    ReportType,
)

logger = logging.getLogger(__name__)


def _money(value):
    """Format money as -₹1,234.00 (minus sign before the rupee symbol)."""
    rounded = round(value, 2)
    sign = "-" if rounded < 0 else ""
    return f"{sign}₹{abs(rounded):,.2f}"


def _extract_inputs(
    state: Optional[Union[AgentState, Dict[str, Any]]] = None,
    **kwargs: Any,
) -> Tuple[
    str,
    ReportType,
    date,
    Optional[Union[FinancialIntelligenceResult, AgentResult, Dict[str, Any]]],
    Optional[Union[AgentResult, Dict[str, Any]]],
    Optional[Union[AgentResult, Dict[str, Any]]],
    Optional[Union[AgentResult, Dict[str, Any]]],
]:
    """
    Extracts business_id, report_type, as_of_date, intelligence_result, cashflow_result,
    invoice_result, and expense_result from state dictionary, AgentState, or kwargs.
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

    raw_report_type = kwargs.get("report_type") or state_dict.get("report_type") or "daily"
    if isinstance(raw_report_type, ReportType):
        report_type = raw_report_type
    elif isinstance(raw_report_type, str):
        normalized = raw_report_type.strip().lower()
        if normalized == "monthly":
            report_type = ReportType.MONTHLY
        else:
            report_type = ReportType.DAILY
    else:
        report_type = ReportType.DAILY

    raw_date = kwargs.get("as_of_date") or kwargs.get("today") or state_dict.get("as_of_date") or state_dict.get("today")
    if raw_date is not None and str(raw_date).strip():
        if isinstance(raw_date, date):
            as_of_date = raw_date
        else:
            try:
                as_of_date = date.fromisoformat(str(raw_date).strip())
            except ValueError:
                as_of_date = settings.get_as_of_date()
    else:
        as_of_date = settings.get_as_of_date()

    intel_res = (
        kwargs.get("intelligence_result")
        or kwargs.get("financial_intelligence_result")
        or state_dict.get("intelligence_result")
        or state_dict.get("financial_intelligence_result")
    )
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
            if name in ("intelligence", "financial_intelligence") and intel_res is None:
                intel_res = res
            elif name in ("cashflow", "cash_flow") and cf_res is None:
                cf_res = res
            elif name == "invoice" and inv_res is None:
                inv_res = res
            elif name == "expense" and exp_res is None:
                exp_res = res

    return business_id, report_type, as_of_date, intel_res, cf_res, inv_res, exp_res


def _is_input_limitation(result: Optional[Union[AgentResult, Dict[str, Any]]]) -> bool:
    """Returns True if an agent result is missing or represents a limitation."""
    if result is None:
        return True
    return _is_specialist_limitation(result)


def _build_template_summary(
    report_type: ReportType,
    combined_metrics: Dict[str, Any],
    missing_parts: List[str],
    intel_res: Optional[Union[FinancialIntelligenceResult, AgentResult, Dict[str, Any]]] = None,
) -> str:
    """
    Builds a deterministic template executive summary using ONLY verified numbers.
    Daily report: 2-3 sentences.
    Monthly report: 4-6 sentences.
    """
    if report_type == ReportType.DAILY:
        sentences: List[str] = []
        if "cash_balance" in combined_metrics and "net_cash_flow" in combined_metrics:
            cb = combined_metrics["cash_balance"]
            ncf = combined_metrics["net_cash_flow"]
            sentences.append(f"Ending cash balance is {_money(cb)} with net cash flow of {_money(ncf)}.")
        elif intel_res:
            s = getattr(intel_res, "overall_summary", "") if not isinstance(intel_res, dict) else intel_res.get("overall_summary", "")
            if s:
                sentences.append(s.split(".")[0].strip() + ".")

        ops_parts: List[str] = []
        if "total_amount" in combined_metrics and "count" in combined_metrics:
            tot = combined_metrics["total_amount"]
            cnt = combined_metrics["count"]
            ops_parts.append("no overdue invoices" if cnt == 0 else f"{cnt} overdue invoice{'s' if cnt != 1 else ''} total {_money(tot)}")
        if "anomalies_count" in combined_metrics:
            acnt = combined_metrics["anomalies_count"]
            ops_parts.append(f"{acnt} expense anomalies were detected")
        if ops_parts:
            sentences.append("Operations show " + " and ".join(ops_parts) + ".")

        if missing_parts:
            sentences.append(f"Data for {', '.join(missing_parts)} was unavailable.")
        elif not sentences:
            sentences.append("Financial status is stable with routine operational activity.")

        return " ".join(sentences)

    else:
        # Monthly report (4-6 sentences)
        sentences = []
        if "cash_balance" in combined_metrics and "net_cash_flow" in combined_metrics:
            cb = combined_metrics["cash_balance"]
            ncf = combined_metrics["net_cash_flow"]
            inf = combined_metrics.get("inflows", 0.0)
            out = combined_metrics.get("outflows", 0.0)
            if "inflows" in combined_metrics and "outflows" in combined_metrics:
                sentences.append(
                    f"Monthly cash balance concluded at {_money(cb)} with a net cash flow of {_money(ncf)} (inflows {_money(inf)}, outflows {_money(out)})."
                )
            else:
                sentences.append(
                    f"Monthly cash balance concluded at {_money(cb)} with a net cash flow of {_money(ncf)}."
                )

        if "projected_balance" in combined_metrics and "horizon_days" in combined_metrics:
            pb = combined_metrics["projected_balance"]
            hdays = combined_metrics["horizon_days"]
            risk = combined_metrics.get("risk_level", "low")
            sentences.append(
                f"The {hdays}-day projected cash balance stands at {_money(pb)} under a {risk} liquidity risk assessment."
            )

        if "total_amount" in combined_metrics and "count" in combined_metrics:
            tot = combined_metrics["total_amount"]
            cnt = combined_metrics["count"]
            sentences.append(
                ("There are no overdue invoices." if cnt == 0 else f"Outstanding receivables include {cnt} overdue invoice{'s' if cnt != 1 else ''} totaling {_money(tot)} requiring collection attention.")
            )

        if "anomalies_count" in combined_metrics:
            acnt = combined_metrics["anomalies_count"]
            sentences.append(
                f"A total of {acnt} anomalous expense transactions were detected during the period."
            )

        if missing_parts:
            sentences.append(
                f"Data for {', '.join(missing_parts)} was unavailable or encountered a limitation."
            )

        if len(sentences) < 4:
            sentences.append(
                "Overall financial performance requires continued oversight of working capital and discretionary spending."
            )

        return " ".join(sentences)


def reporting_agent(
    state: Optional[Union[AgentState, Dict[str, Any]]] = None,
    **kwargs: Any,
) -> ReportingResult:
    """
    Executes the Executive Reporting Agent logic.

    1. Validates that business_id is present (returns limitation immediately if empty).
    2. Takes FinancialIntelligenceResult plus optional Cash Flow, Invoice, and Expense results.
    3. Identifies missing inputs or limitations; if ALL inputs are missing, returns limitation result.
    4. Copies metrics and evidence directly without inventing or calculating figures.
    5. Copies caveats and warnings word-for-word.
    6. Copies recommendations strictly from financial intelligence (preserves priority and approval flag).
    7. Asks OpenAI (structured output, timeout=10, max_retries=1) to write the summary text.
    8. Verifies that every number in the AI summary exists in input metrics/evidence. If not, discards and uses template fallback.
    9. Returns a ReportingResult with to_chat_response() support.

    Guarantees:
    - Never crashes.
    - Never calls any finance tool.
    - Never calculates new numbers.
    - Preserves specialist caveats and recommendations without modification.
    """
    business_id, report_type, as_of_date, intel_res, cf_res, inv_res, exp_res = _extract_inputs(state, **kwargs)

    report_type_val = report_type.value if hasattr(report_type, "value") else str(report_type)
    title = f"{report_type_val.capitalize()} report - {as_of_date.day} {as_of_date.strftime('%B')} {as_of_date.year}"

    # Hard Rule 5: Empty business_id gives a limitation result
    if not business_id or not business_id.strip():
        return ReportingResult(
            agent_name="reporting",
            report_type=report_type,
            title=title,
            summary="Report could not be generated right now: business_id is required.",
            evidence=[],
            caveats=["Missing business_id parameter."],
            recommendations=[],
            missing_parts=["business_id"],
            metrics={},
            currency="INR",
            requires_approval=False,
        )

    # Detect missing parts and limitations
    missing_parts: List[str] = []
    inputs = [
        ("intelligence", intel_res),
        ("cashflow", cf_res),
        ("invoice", inv_res),
        ("expense", exp_res),
    ]

    for name, res in inputs:
        if _is_input_limitation(res):
            missing_parts.append(name)

    # If intelligence result itself reported missing parts, include them
    if intel_res is not None and not _is_input_limitation(intel_res):
        intel_missing = (
            getattr(intel_res, "missing_parts", [])
            if not isinstance(intel_res, dict)
            else intel_res.get("missing_parts", [])
        )
        for part in intel_missing:
            if part not in missing_parts:
                missing_parts.append(part)

    # Copy caveats and warnings word-for-word
    combined_caveats: List[str] = []
    for _, res in inputs:
        if res is not None:
            cav_list = getattr(res, "caveats", None) if not isinstance(res, dict) else res.get("caveats")
            if cav_list:
                for cav in cav_list:
                    cav_str = str(cav)
                    if cav_str not in combined_caveats:
                        combined_caveats.append(cav_str)

    # Hard Rule 4: If ALL inputs are missing, return limitation result
    if len(missing_parts) >= 4 and all(_is_input_limitation(res) for _, res in inputs):
        return ReportingResult(
            agent_name="reporting",
            report_type=report_type,
            title=title,
            summary="Report could not be generated right now.",
            evidence=[],
            caveats=combined_caveats or ["Specialist and intelligence financial data unavailable or reported limitations."],
            recommendations=[],
            missing_parts=["intelligence", "cashflow", "invoice", "expense"],
            metrics={},
            currency="INR",
            requires_approval=False,
        )

    # Rule 1: Copy metrics directly from inputs (never calculated or invented)
    combined_metrics: Dict[str, Any] = {}
    for name, res in inputs:
        if name not in missing_parts and res is not None:
            m = getattr(res, "metrics", {}) if not isinstance(res, dict) else res.get("metrics", {})
            if m:
                combined_metrics[name] = dict(m)
                for k, v in m.items():
                    if k not in combined_metrics:
                        combined_metrics[k] = v

    # If intelligence was provided, also inherit any nested specialist metrics if not already present
    if intel_res is not None and not _is_input_limitation(intel_res):
        im = getattr(intel_res, "metrics", {}) if not isinstance(intel_res, dict) else intel_res.get("metrics", {})
        if im:
            for k, v in im.items():
                if k not in combined_metrics:
                    combined_metrics[k] = v

    # Rule 1: Copy evidence items directly from inputs
    evidence: List[ReportEvidenceItem] = []

    # Cashflow evidence
    if "cashflow" not in missing_parts or ("cash_balance" in combined_metrics and cf_res is not None):
        if "cash_balance" in combined_metrics:
            cb = combined_metrics["cash_balance"]
            evidence.append(ReportEvidenceItem(label="Cash Balance", value=f"{_money(cb)}", source="cashflow"))
        if "net_cash_flow" in combined_metrics:
            ncf = combined_metrics["net_cash_flow"]
            evidence.append(ReportEvidenceItem(label="Net Cash Flow", value=f"{_money(ncf)}", source="cashflow"))
        if "projected_balance" in combined_metrics:
            pb = combined_metrics["projected_balance"]
            evidence.append(ReportEvidenceItem(label="Projected Cash Balance", value=f"{_money(pb)}", source="cashflow"))
        if "risk_level" in combined_metrics:
            rlevel = str(combined_metrics["risk_level"]).title()
            evidence.append(ReportEvidenceItem(label="Liquidity Risk Level", value=rlevel, source="cashflow"))

    # Invoice evidence
    if "invoice" not in missing_parts or ("total_amount" in combined_metrics and inv_res is not None):
        if "total_amount" in combined_metrics:
            tot = combined_metrics["total_amount"]
            evidence.append(ReportEvidenceItem(label="Overdue Invoices Total", value=f"{_money(tot)}", source="invoice"))
        if "count" in combined_metrics:
            cnt = str(combined_metrics["count"])
            evidence.append(ReportEvidenceItem(label="Overdue Invoices Count", value=cnt, source="invoice"))

    # Expense evidence
    if "expense" not in missing_parts or ("anomalies_count" in combined_metrics and exp_res is not None):
        if "anomalies_count" in combined_metrics:
            acnt = str(combined_metrics["anomalies_count"])
            evidence.append(ReportEvidenceItem(label="Expense Anomalies Count", value=acnt, source="expense"))

    # Financial Intelligence evidence
    if "intelligence" not in missing_parts and intel_res is not None:
        ov_sum = (
            getattr(intel_res, "overall_summary", "")
            or getattr(intel_res, "summary", "")
            if not isinstance(intel_res, dict)
            else (intel_res.get("overall_summary") or intel_res.get("summary", ""))
        )
        if ov_sum:
            evidence.append(ReportEvidenceItem(label="Overall Financial Health", value=str(ov_sum), source="intelligence"))

        key_risks = getattr(intel_res, "key_risks", []) if not isinstance(intel_res, dict) else intel_res.get("key_risks", [])
        for r in key_risks:
            evidence.append(ReportEvidenceItem(label="Key Risk", value=str(r), source="intelligence"))

        links = getattr(intel_res, "links", []) if not isinstance(intel_res, dict) else intel_res.get("links", [])
        for lk in links:
            evidence.append(ReportEvidenceItem(label="Cross-domain Link", value=str(lk), source="intelligence"))

    # Rule 3: Copy recommendations from intelligence result (keep priority and requires_approval)
    combined_recommendations: List[IntelligenceRecommendation] = []
    if intel_res is not None and not _is_input_limitation(intel_res):
        raw_recs = (
            getattr(intel_res, "recommendations", [])
            if not isinstance(intel_res, dict)
            else intel_res.get("recommendations", [])
        )
        for r in raw_recs:
            if isinstance(r, IntelligenceRecommendation):
                combined_recommendations.append(r)
            elif isinstance(r, dict):
                combined_recommendations.append(
                    IntelligenceRecommendation(
                        recommendation=r.get("recommendation", r.get("action", "")),
                        priority=r.get("priority", "medium"),
                        reason=r.get("reason", ""),
                        requires_approval=r.get("requires_approval", False),
                    )
                )

    # Collect all valid numbers from metrics and evidence for hallucination check
    valid_numbers: Set[float] = _get_all_valid_numbers(combined_metrics)
    for ev in evidence:
        for match in re.findall(r"\b\d+(?:,\d{3})*(?:\.\d+)?\b", ev.value):
            try:
                valid_numbers.add(float(match.replace(",", "")))
            except ValueError:
                pass

    # Build deterministic template fallback
    template_summary = _build_template_summary(
        report_type=report_type,
        combined_metrics=combined_metrics,
        missing_parts=missing_parts,
        intel_res=intel_res,
    )

    final_summary = template_summary

    # Attempt OpenAI summary generation if configured
    api_key = settings.OPENAI_API_KEY.strip() if settings.OPENAI_API_KEY else ""

    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                timeout=10,
                max_retries=1,
            )

            evidence_text = (
                "\n".join(f"- {ev.label}: {ev.value} ({ev.source})" for ev in evidence)
                if evidence
                else "None"
            )
            caveats_text = "\n".join(f"- {c}" for c in combined_caveats) if combined_caveats else "None"
            missing_text = ", ".join(missing_parts) if missing_parts else "None"
            metrics_text = str(combined_metrics)
            length_guidance = "2-3 sentences" if report_type == ReportType.DAILY else "4-6 sentences"

            user_prompt = REPORTING_USER_PROMPT_TEMPLATE.format(
                business_id=business_id,
                report_type=report_type_val,
                length_guidance=length_guidance,
                evidence_section=evidence_text,
                metrics_section=metrics_text,
                caveats_section=caveats_text,
                missing_parts_section=missing_text,
                currency_symbol="₹",
            )

            completion = client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": REPORTING_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format=ReportingExplanation,
            )
            parsed = completion.choices[0].message.parsed

            if parsed is not None and parsed.summary:
                ai_text = parsed.summary.strip()
                # Rule 7: Check that every figure in AI text exists in metrics/evidence
                if _validate_ai_text_numbers(ai_text, valid_numbers):
                    # Rule 4: If any input is missing, ensure summary clearly mentions it
                    if missing_parts:
                        has_missing_mention = (
                            any(p.lower() in ai_text.lower() for p in missing_parts)
                            or "unavailable" in ai_text.lower()
                            or "missing" in ai_text.lower()
                        )
                        if not has_missing_mention:
                            ai_text += f" Data for {', '.join(missing_parts)} was unavailable."
                    final_summary = ai_text
                else:
                    logger.warning(
                        "AI summary contained unverified figures not in metrics/evidence. Falling back to template summary."
                    )
        except Exception as ai_exc:
            logger.warning(
                "OpenAI reporting summary unavailable: %s. Using template fallback.",
                type(ai_exc).__name__,
            )

    return ReportingResult(
        agent_name="reporting",
        report_type=report_type,
        title=title,
        summary=final_summary,
        evidence=evidence,
        caveats=combined_caveats,
        recommendations=combined_recommendations,
        missing_parts=missing_parts,
        metrics=combined_metrics,
        currency="INR",
        requires_approval=any(r.requires_approval for r in combined_recommendations),
    )


# Alias for explicit naming
report_agent = reporting_agent

