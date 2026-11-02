"""
Tests for the AgentFi query router.

Verifies keyword matching across all domains, multi-agent coordination detection,
fallback behavior without an OpenAI API key, and safety invariants (never crash,
never calculate math).
"""

import pytest
from unittest.mock import patch

from config.settings import settings
from graph.router.router import route_query
from graph.state.models import Intent, RouterDecision


# ------------------------------------------------------------------------------
# Test Questions Covering Every Intent (12 comprehensive questions)
# ------------------------------------------------------------------------------
TEST_QUESTIONS = [
    # 1. Cash flow (cash, balance)
    ("What is my current cash balance?", Intent.cash_flow, ["cashflow"]),
    # 2. Cash flow (forecast)
    ("Can you forecast our financial runway for Q4?", Intent.cash_flow, ["cashflow"]),
    # 3. Invoice (invoice, overdue)
    ("Which client invoices are overdue right now?", Intent.invoice, ["invoice"]),
    # 4. Invoice (payment due)
    ("When is the next payment due from Globex Corp?", Intent.invoice, ["invoice"]),
    # 5. Expense (spending)
    ("Can you break down our software spending this month?", Intent.expense, ["expense"]),
    # 6. Expense (expense, cost)
    ("What is the total operational cost and travel expense?", Intent.expense, ["expense"]),
    # 7. Financial Intelligence (health, overall)
    ("What is the overall financial health of our company?", Intent.financial_intelligence, ["intelligence"]),
    # 8. Financial Intelligence (health)
    ("Is the financial health of our business strong?", Intent.financial_intelligence, ["intelligence"]),
    # 9. Reporting (report)
    ("Please generate an executive financial report for last quarter.", Intent.reporting, ["reporting"]),
    # 10. Reporting (summary)
    ("I need a monthly summary of our financial performance.", Intent.reporting, ["reporting"]),
    # 11. Multi-Agent Example 1: cash + invoices overdue
    ("Why is my cash low and which invoices are overdue?", Intent.multi_agent, ["cashflow", "invoice"]),
    # 12. Multi-Agent Example 2: summary/report + spending/cost
    ("Generate a summary report of our spending and costs this month.", Intent.multi_agent, ["expense", "reporting"]),
    # 13. Nonsense question (no keywords match)
    ("Tell me a joke about purple flying elephants", Intent.unknown, []),
]


@pytest.mark.parametrize("question,expected_intent,expected_agents", TEST_QUESTIONS)
def test_route_query_intents(question, expected_intent, expected_agents):
    """
    Test that questions are accurately classified into their respective intents
    and target agents, using keyword rules without needing an OpenAI key.
    """
    # Ensure test runs purely via deterministic rules without external API calls
    with patch.object(settings, "OPENAI_API_KEY", ""):
        decision = route_query(question)

        assert isinstance(decision, RouterDecision)
        assert decision.intent == expected_intent

        # Verify target agents match (order independent)
        assert sorted(decision.target_agents) == sorted(expected_agents)

        if expected_intent == Intent.unknown:
            assert decision.confidence == 0.0
        else:
            assert decision.confidence >= 0.9
            assert len(decision.reasoning) > 0


def test_router_never_crashes_on_edge_cases():
    """Verify that route_query gracefully handles edge cases without raising exceptions."""
    with patch.object(settings, "OPENAI_API_KEY", ""):
        for bad_input in ["", "   ", "\n\t", "???!!!", "123456789"]:
            decision = route_query(bad_input)
            assert isinstance(decision, RouterDecision)
            assert decision.intent == Intent.unknown
            assert decision.confidence == 0.0


def test_router_openai_fallback_when_call_fails():
    """
    Test Step B: When OpenAI key exists but call raises an error,
    the router catches the exception, logs with logger.warning,
    and returns unknown with generic reasoning without crashing.
    """
    with patch.object(settings, "OPENAI_API_KEY", "mock-test-key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_client.beta.chat.completions.parse.side_effect = RuntimeError("OpenAI API rate limit exceeded")

            decision = route_query("Can someone assist me with arbitrary unknown task?")
            # Verify client was instantiated with timeout=10 and max_retries=1
            mock_openai_cls.assert_called_once_with(
                api_key="mock-test-key",
                timeout=10,
                max_retries=1,
            )
            assert isinstance(decision, RouterDecision)
            assert decision.intent == Intent.unknown
            assert decision.confidence == 0.0
            assert decision.reasoning == "AI classification was unavailable."


def test_costume_budget_does_not_route_to_expense():
    """Verify whole-word matching prevents 'costume' from falsely matching keyword 'cost'."""
    with patch.object(settings, "OPENAI_API_KEY", ""):
        decision = route_query("costume budget")
        assert decision.intent != Intent.expense
        assert "expense" not in decision.target_agents
        assert decision.intent == Intent.unknown


def test_unpaid_invoices_routes_to_invoice():
    """Verify 'which invoices are unpaid?' routes to invoice with plurals and extra keywords."""
    with patch.object(settings, "OPENAI_API_KEY", ""):
        decision = route_query("which invoices are unpaid?")
        assert decision.intent == Intent.invoice
        assert decision.target_agents == ["invoice"]
        assert decision.confidence >= 0.9


def test_router_openai_fallback_success():
    """
    Test Step B: When OpenAI key exists and returns structured RouterDecision.
    """
    from unittest.mock import MagicMock

    with patch.object(settings, "OPENAI_API_KEY", "mock-valid-key"):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_completion = MagicMock()
            mock_completion.choices[0].message.parsed = RouterDecision(
                intent=Intent.financial_intelligence,
                target_agents=["intelligence"],
                confidence=0.88,
                reasoning="LLM determined this relates to strategic financial outlook.",
            )
            mock_client.beta.chat.completions.parse.return_value = mock_completion

            decision = route_query("Should we raise venture capital or seek a bank credit line?")
            assert decision.intent == Intent.financial_intelligence
            assert decision.target_agents == ["intelligence"]
            assert decision.confidence == 0.88

