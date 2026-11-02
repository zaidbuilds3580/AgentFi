"""
Tests for the minimal LangGraph workflow (START -> router -> END).

Verifies that the workflow executes end-to-end without requiring an OpenAI API key,
correctly updates AgentState, and enforces tenant isolation (business_id).
"""

from unittest.mock import patch
from langchain_core.messages import HumanMessage

from config.settings import settings
from graph.state.agent_state import AgentState
from graph.state.models import AgentResult, Intent, RouterDecision
from graph.workflow.minimal_graph import minimal_graph
from langgraph.graph import StateGraph, START, END


def test_minimal_graph_end_to_end_without_openai_key():
    """
    Test that the minimal graph runs end to end without any OpenAI API key,
    successfully routing a cash flow question.
    """
    with patch.object(settings, "OPENAI_API_KEY", ""):
        initial_state: AgentState = {
            "business_id": "biz_sme_12345",
            "user_id": "user_owner_01",
            "messages": [
                HumanMessage(content="What is our current cash balance and forecast?")
            ],
            "agent_results": [],
            "action_proposals": [],
            "errors": [],
            "status": "running",
        }

        result = minimal_graph.invoke(initial_state)

        # Check execution status
        assert result["status"] == "success"
        assert result["business_id"] == "biz_sme_12345"

        # Check intent classification in state
        assert result["intent"] == Intent.cash_flow

        # Check router decision details
        decision = result["router_decision"]
        assert isinstance(decision, RouterDecision)
        assert decision.intent == Intent.cash_flow
        assert "cashflow" in decision.target_agents
        assert decision.confidence >= 0.9


def test_minimal_graph_multi_agent_query():
    """
    Test minimal graph with a multi-agent query without an OpenAI key:
    "Why is my cash low and which invoices are overdue?"
    """
    with patch.object(settings, "OPENAI_API_KEY", ""):
        initial_state: AgentState = {
            "business_id": "biz_sme_99999",
            "user_id": "user_cfo_02",
            "messages": [
                HumanMessage(content="Why is my cash low and which invoices are overdue?")
            ],
            "agent_results": [],
            "action_proposals": [],
            "errors": [],
            "status": "running",
        }

        result = minimal_graph.invoke(initial_state)

        assert result["status"] == "success"
        assert result["intent"] == Intent.multi_agent
        decision = result["router_decision"]
        assert isinstance(decision, RouterDecision)
        assert set(decision.target_agents) == {"cashflow", "invoice"}


def test_minimal_graph_enforces_business_id():
    """
    RULE: business_id is required for all state executions.
    Graph must fail cleanly when business_id is missing or blank.
    """
    with patch.object(settings, "OPENAI_API_KEY", ""):
        invalid_state: AgentState = {
            "business_id": "",  # Empty business_id!
            "user_id": "user_unknown",
            "messages": [
                HumanMessage(content="What is our cash balance?")
            ],
            "agent_results": [],
            "action_proposals": [],
            "errors": [],
            "status": "running",
        }

        result = minimal_graph.invoke(invalid_state)

        assert result["status"] == "failed"
        assert any("business_id" in err for err in result.get("errors", []))


def test_agent_results_reducer_appends_across_nodes():
    """
    Test that two nodes each returning one agent_result both end up in the final state,
    proving that Annotated[List[...], operator.add] correctly appends instead of overwriting.
    """
    def cashflow_agent_node(state: AgentState):
        result = AgentResult(
            agent_name="cashflow",
            summary="Cash runway is 6 months.",
            metrics={"runway": 6},
            findings=["Healthy reserves"],
            recommendations=["Continue as planned"],
            requires_approval=False,
        )
        return {"agent_results": [result]}

    def invoice_agent_node(state: AgentState):
        result = AgentResult(
            agent_name="invoice",
            summary="Found 2 unpaid invoices.",
            metrics={"unpaid_count": 2},
            findings=["Client ABC is 10 days late"],
            recommendations=["Send gentle reminder"],
            requires_approval=False,
        )
        return {"agent_results": [result]}

    # Construct test workflow: START -> cashflow_agent -> invoice_agent -> END
    workflow = StateGraph(AgentState)
    workflow.add_node("cashflow_node", cashflow_agent_node)
    workflow.add_node("invoice_node", invoice_agent_node)
    workflow.add_edge(START, "cashflow_node")
    workflow.add_edge("cashflow_node", "invoice_node")
    workflow.add_edge("invoice_node", END)
    compiled_app = workflow.compile()

    initial_state: AgentState = {
        "business_id": "biz_multi_test",
        "user_id": "test_user",
        "messages": [],
        "agent_results": [],
        "action_proposals": [],
        "errors": [],
        "status": "running",
    }

    final_state = compiled_app.invoke(initial_state)

    # Both agent results must be present in the final state
    results = final_state["agent_results"]
    assert len(results) == 2
    assert results[0].agent_name == "cashflow"
    assert results[1].agent_name == "invoice"

