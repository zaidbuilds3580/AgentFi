"""
AgentState definition for the AgentFi multi-agent LangGraph workflow.

CORE RULES:
1. 'business_id' is REQUIRED for all operations to ensure tenant isolation.
2. The state MUST NEVER hold raw financial records (such as raw bank transaction logs,
   ledger dumps, or raw CSV rows). Only aggregated metrics, structured summaries,
   findings, and action proposals are permitted in state.
"""

import operator
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from graph.state.models import ActionProposal, AgentResult, Intent, RouterDecision


class AgentState(TypedDict, total=False):
    """
    Central state passed between nodes in the AgentFi graph.

    Note on TypedDict:
    'business_id' is logically mandatory for tenant context. In TypedDict(total=False),
    all fields can be updated partially by nodes, but initial state passed to graph.invoke()
    must provide 'business_id'.
    """

    # MANDATORY: Tenant identifier for the SME business
    business_id: str

    # User identifier who initiated the interaction
    user_id: str

    # Conversation history with LangGraph's built-in message reducer (appends new messages)
    messages: Annotated[List[AnyMessage], add_messages]

    # Current classified intent
    intent: Optional[Union[Intent, str]]

    # Detailed decision output from the router node
    router_decision: Optional[Union[RouterDecision, Dict[str, Any]]]

    # Collection of results produced by domain agents (metrics, summaries, advice)
    # Uses operator.add reducer so nodes append their results without overwriting
    agent_results: Annotated[List[Union[AgentResult, Dict[str, Any]]], operator.add]

    # Human-in-the-loop action proposals requiring SME owner sign-off
    # Uses operator.add reducer so nodes append proposals
    action_proposals: Annotated[List[Union[ActionProposal, Dict[str, Any]]], operator.add]

    # System or execution error messages logged during the run
    # Uses operator.add reducer so errors accumulate
    errors: Annotated[List[str], operator.add]

    # Current execution status of the graph
    status: Literal["running", "success", "failed"]

