"""
Minimal LangGraph workflow for AgentFi query routing.

Architecture:
START -> router_node -> END

This minimal graph serves as the foundation and integration test harness
for AgentFi. It reads the user's latest message, calls route_query, and records
the resulting RouterDecision and Intent directly in the central AgentState.
"""

from typing import Any, Dict
from langgraph.graph import StateGraph, START, END

from graph.state.agent_state import AgentState
from graph.router.router import route_query


def router_node(state: AgentState) -> Dict[str, Any]:
    """
    Router node in the LangGraph workflow.

    Responsibilities:
    1. Validate that 'business_id' is present (architectural invariant for tenant isolation).
    2. Extract the latest user question from state['messages'].
    3. Call route_query(question).
    4. Return state updates: router_decision, intent, and execution status.
    """
    # Invariant check: business_id is mandatory
    business_id = state.get("business_id")
    if not business_id or not str(business_id).strip():
        return {
            "errors": ["Validation error: 'business_id' is required."],
            "status": "failed",
        }

    # Extract the latest message content
    messages = state.get("messages", [])
    if not messages:
        return {
            "errors": ["Routing error: No messages provided to route."],
            "status": "failed",
        }

    last_msg = messages[-1]
    if hasattr(last_msg, "content"):
        query = str(last_msg.content)
    elif isinstance(last_msg, dict) and "content" in last_msg:
        query = str(last_msg["content"])
    else:
        query = str(last_msg)

    # Route the query (will never crash, keyword first, then LLM fallback)
    decision = route_query(query)

    return {
        "router_decision": decision,
        "intent": decision.intent,
        "status": "success",
    }


def build_minimal_graph():
    """
    Constructs and compiles the minimal LangGraph workflow.
    
    START -> router -> END
    """
    workflow = StateGraph(AgentState)

    # Register the router node
    workflow.add_node("router", router_node)

    # Define simple linear control flow
    workflow.add_edge(START, "router")
    workflow.add_edge("router", END)

    return workflow.compile()


# Pre-compiled instance ready for invocation
minimal_graph = build_minimal_graph()

