"""
Full LangGraph workflow for AgentFi (Day 9).

    START -> router -+-> unclear  -> END
                     +-> failure  -> END
                     +-> specialist <-+ (loops while the to-do list has items)
                              |       |
                              +-------+
                              +-> failure (if every specialist failed)
                              v
                        intelligence -> reporting -> finalize -> END

Every node RETURNS its updates (it never edits the state in place).
The final answer always has exactly 7 keys: summary, evidence, caveats,
recommendations, missing_parts, report_type, currency.
"""

import logging
from typing import Any, Dict, List, Optional

from langgraph.graph import END, START, StateGraph

from graph.router.router import route_query
from graph.state.agent_state import AgentState
from graph.state.models import Intent

from agents.cashflow.agent import cash_flow_agent
from agents.invoice.agent import invoice_agent
from agents.expense.agent import expense_agent
from agents.intelligence.agent import intelligence_agent
from agents.reporting.agent import reporting_agent

logger = logging.getLogger(__name__)

SAFE_FAILURE_MESSAGE = "I could not complete this request right now. Please try again."
UNCLEAR_MESSAGE = "I am not sure I understand the request. Could you please rephrase it?"
ALL_SPECIALISTS = ["cashflow", "invoice", "expense"]
INTENT_TO_SPECIALISTS = {
    Intent.cash_flow: ["cashflow"],
    Intent.invoice: ["invoice"],
    Intent.expense: ["expense"],
}
FAILED_PREFIX = "specialist_failed:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _seven_keys(
    summary: str,
    evidence: Optional[list] = None,
    caveats: Optional[list] = None,
    recommendations: Optional[list] = None,
    missing_parts: Optional[list] = None,
    report_type: str = "daily",
    currency: str = "INR",
) -> Dict[str, Any]:
    return {
        "summary": summary,
        "evidence": list(evidence or []),
        "caveats": list(caveats or []),
        "recommendations": list(recommendations or []),
        "missing_parts": list(missing_parts or []),
        "report_type": report_type,
        "currency": currency,
    }


def _canonical_name(obj: Any) -> str:
    n = str(_get(obj, "agent_name", "") or "").lower()
    if "cash" in n:
        return "cashflow"
    if "invoice" in n:
        return "invoice"
    if "expense" in n:
        return "expense"
    if "intelligence" in n:
        return "intelligence"
    if "report" in n:
        return "reporting"
    return ""


def _results_by_name(state: Dict[str, Any]) -> Dict[str, Any]:
    by_name: Dict[str, Any] = {}
    for res in list(state.get("agent_results") or []):
        name = _canonical_name(res)
        if name and name not in by_name:
            by_name[name] = res
    return by_name


def _last_user_text(state: Dict[str, Any]) -> str:
    messages = state.get("messages") or []
    if not messages:
        return ""
    last = messages[-1]
    if isinstance(last, dict):
        return str(last.get("content") or "")
    if isinstance(last, (list, tuple)) and len(last) == 2:
        return str(last[1] or "")
    return str(getattr(last, "content", "") or "")


def _detect_report_type(text: str) -> str:
    return "monthly" if "month" in text.lower() else "daily"


def _failed_specialists(state: Dict[str, Any]) -> List[str]:
    names: List[str] = []
    for err in list(state.get("errors") or []):
        if isinstance(err, str) and err.startswith(FAILED_PREFIX):
            name = err[len(FAILED_PREFIX):]
            if name and name not in names:
                names.append(name)
    return names


# ---------------------------------------------------------------------------
# Nodes: router and specialists
# ---------------------------------------------------------------------------

def router_node(state: AgentState) -> Dict[str, Any]:
    """Classify the question and build the to-do list of specialists."""
    try:
        business_id = str(state.get("business_id") or "").strip()
        if not business_id:
            return {"errors": ["router_failed"], "status": "failed"}
        text = _last_user_text(state)
        report_type = _detect_report_type(text)
        if not text.strip():
            return {
                "intent": Intent.unknown,
                "todo_agents": [],
                "report_type": report_type,
                "status": "running",
            }
        decision = route_query(text)
        intent = decision.intent
        if intent in INTENT_TO_SPECIALISTS:
            todo = list(INTENT_TO_SPECIALISTS[intent])
        elif intent == Intent.unknown:
            todo = []
        else:
            todo = list(ALL_SPECIALISTS)
        return {
            "router_decision": decision,
            "intent": intent,
            "todo_agents": todo,
            "report_type": report_type,
            "status": "running",
        }
    except Exception as exc:
        logger.warning("Router node failed: %s", type(exc).__name__)
        return {"errors": ["router_failed"], "status": "failed"}


def specialist_node(state: AgentState) -> Dict[str, Any]:
    """Run the next specialist in the to-do list and return the updates."""
    todo = list(state.get("todo_agents") or [])
    if not todo:
        return {"todo_agents": []}
    name = todo.pop(0)
    fn_map = {
        "cashflow": cash_flow_agent,
        "invoice": invoice_agent,
        "expense": expense_agent,
    }
    agent_fn = fn_map.get(name)
    if agent_fn is None:
        logger.warning("Unknown specialist requested")
        return {"todo_agents": todo, "errors": [FAILED_PREFIX + str(name)]}
    try:
        result = agent_fn(state)
        return {"agent_results": [result], "todo_agents": todo}
    except Exception as exc:
        logger.warning("Specialist %s failed: %s", name, type(exc).__name__)
        return {"todo_agents": todo, "errors": [FAILED_PREFIX + name]}


# ---------------------------------------------------------------------------
# Nodes: intelligence, reporting, finalize, end states
# ---------------------------------------------------------------------------

def intelligence_node(state: AgentState) -> Dict[str, Any]:
    """Combine the specialist results into one overall view."""
    try:
        by_name = _results_by_name(state)
        result = intelligence_agent(
            state,
            business_id=state.get("business_id"),
            cashflow_result=by_name.get("cashflow"),
            invoice_result=by_name.get("invoice"),
            expense_result=by_name.get("expense"),
        )
        return {"intelligence_result": result}
    except Exception as exc:
        logger.warning("Intelligence node failed: %s", type(exc).__name__)
        return {"errors": ["intelligence_failed"]}


def reporting_node(state: AgentState) -> Dict[str, Any]:
    """Write the executive report from the earlier results."""
    try:
        by_name = _results_by_name(state)
        result = reporting_agent(
            state,
            business_id=state.get("business_id"),
            report_type=state.get("report_type") or "daily",
            intelligence_result=state.get("intelligence_result"),
            cashflow_result=by_name.get("cashflow"),
            invoice_result=by_name.get("invoice"),
            expense_result=by_name.get("expense"),
        )
        return {"reporting_result": result}
    except Exception as exc:
        logger.warning("Reporting node failed: %s", type(exc).__name__)
        return {"errors": ["reporting_failed"]}


def _response_from_report(rep: Any) -> Optional[Dict[str, Any]]:
    if rep is None:
        return None
    try:
        if isinstance(rep, dict):
            data = rep
        elif callable(getattr(rep, "to_chat_response", None)):
            data = rep.to_chat_response()
        else:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
            return None
        return _seven_keys(
            data.get("summary", ""),
            data.get("evidence"),
            data.get("caveats"),
            data.get("recommendations"),
            data.get("missing_parts"),
            str(data.get("report_type") or "daily"),
            str(data.get("currency") or "INR"),
        )
    except Exception as exc:
        logger.warning("Could not read report: %s", type(exc).__name__)
        return None


def finalize_node(state: AgentState) -> Dict[str, Any]:
    """Decide success / partial / failure and build the 7-key answer."""
    try:
        report_type = str(state.get("report_type") or "daily")
        errors = list(state.get("errors") or [])
        missing = _failed_specialists(state)
        if "intelligence_failed" in errors:
            missing.append("intelligence")
        if "reporting_failed" in errors or state.get("reporting_result") is None:
            if "report" not in missing:
                missing.append("report")

        final = _response_from_report(state.get("reporting_result"))
        if final is not None:
            final["report_type"] = report_type
            problems = [m for m in missing if m != "report"]
            if problems:
                for m in problems:
                    if m not in final["missing_parts"]:
                        final["missing_parts"].append(m)
                note = "Some parts of this analysis could not be completed right now."
                if note not in final["caveats"]:
                    final["caveats"].append(note)
            return {"final_response": final, "status": "success"}

        # No report: return whatever the specialists produced (partial answer)
        by_name = _results_by_name(state)
        found = [by_name[n] for n in ALL_SPECIALISTS if n in by_name]
        if found:
            summaries = [str(_get(r, "summary", "") or "") for r in found]
            caveats: List[str] = []
            recs: List[str] = []
            for r in found:
                caveats.extend([c for c in (_get(r, "caveats", []) or []) if isinstance(c, str)])
                recs.extend([x for x in (_get(r, "recommendations", []) or []) if isinstance(x, str)])
            caveats.append("Some parts of this analysis could not be completed right now.")
            return {
                "final_response": _seven_keys(
                    " ".join(s for s in summaries if s) or SAFE_FAILURE_MESSAGE,
                    [], caveats, recs, missing, report_type,
                ),
                "status": "success",
            }

        return {
            "final_response": _seven_keys(SAFE_FAILURE_MESSAGE, report_type=report_type),
            "status": "failed",
        }
    except Exception as exc:
        logger.warning("Finalize node failed: %s", type(exc).__name__)
        return {"final_response": _seven_keys(SAFE_FAILURE_MESSAGE), "status": "failed"}


def unclear_node(state: AgentState) -> Dict[str, Any]:
    """Friendly answer when the question is not understood."""
    return {
        "final_response": _seven_keys(
            UNCLEAR_MESSAGE, report_type=str(state.get("report_type") or "daily")
        ),
        "status": "success",
    }


def failure_node(state: AgentState) -> Dict[str, Any]:
    """Safe failure end state: no numbers, no error details."""
    return {
        "final_response": _seven_keys(
            SAFE_FAILURE_MESSAGE, report_type=str(state.get("report_type") or "daily")
        ),
        "status": "failed",
    }


# ---------------------------------------------------------------------------
# Routing decisions and graph build
# ---------------------------------------------------------------------------

def _after_router(state: AgentState) -> str:
    if state.get("status") == "failed":
        return "failure"
    if state.get("intent") == Intent.unknown or not state.get("todo_agents"):
        return "unclear"
    return "specialist"


def _after_specialist(state: AgentState) -> str:
    if state.get("todo_agents"):
        return "specialist"
    if not state.get("agent_results"):
        return "failure"
    return "intelligence"


def build_full_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("router", router_node)
    workflow.add_node("specialist", specialist_node)
    workflow.add_node("intelligence", intelligence_node)
    workflow.add_node("reporting", reporting_node)
    workflow.add_node("finalize", finalize_node)
    workflow.add_node("unclear", unclear_node)
    workflow.add_node("failure", failure_node)

    workflow.add_edge(START, "router")
    workflow.add_conditional_edges(
        "router", _after_router,
        {"specialist": "specialist", "unclear": "unclear", "failure": "failure"},
    )
    workflow.add_conditional_edges(
        "specialist", _after_specialist,
        {"specialist": "specialist", "intelligence": "intelligence", "failure": "failure"},
    )
    workflow.add_edge("intelligence", "reporting")
    workflow.add_edge("reporting", "finalize")
    workflow.add_edge("finalize", END)
    workflow.add_edge("unclear", END)
    workflow.add_edge("failure", END)
    return workflow.compile()


full_graph = build_full_graph()
