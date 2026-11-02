"""
Query Router for AgentFi.

Classifies incoming user questions into financial intents and selects
target domain agents.

RULE:
This router MUST NEVER answer financial questions or perform math.
It only inspects the question text to choose the execution path.
"""

import logging
import re
from typing import Any, Dict, List, Set, Tuple

from config.settings import settings
from graph.router.prompts import ROUTER_SYSTEM_PROMPT
from graph.state.models import Intent, RouterDecision

logger = logging.getLogger(__name__)

# Mapping each financial intent to its target agent identifier and defining keywords.
# As required:
# - cash_flow: cash, forecast, balance, liquidity, runway -> agent: 'cashflow'
# - invoice: invoice, overdue, payment due, payment is due, unpaid, receivable -> agent: 'invoice'
# - expense: expense, expenses, spending, spend, cost -> agent: 'expense'
# - reporting: report, summary -> agent: 'reporting'
# - financial_intelligence: "health", "overall" -> agent: 'intelligence'
KEYWORD_RULES: Dict[Intent, Dict[str, Any]] = {
    Intent.cash_flow: {
        "agent": "cashflow",
        "keywords": ["cash", "forecast", "balance", "liquidity", "runway"],
    },
    Intent.invoice: {
        "agent": "invoice",
        "keywords": [
            "invoice",
            "overdue",
            "payment due",
            "payment is due",
            "unpaid",
            "receivable",
        ],
    },
    Intent.expense: {
        "agent": "expense",
        "keywords": ["expense", "expenses", "spending", "spend", "cost"],
    },
    Intent.reporting: {
        "agent": "reporting",
        "keywords": ["report", "summary"],
    },
    Intent.financial_intelligence: {
        "agent": "intelligence",
        "keywords": ["health", "overall"],
    },
}


def _check_keyword_matches(text: str) -> Dict[Intent, Tuple[str, List[str]]]:
    """
    Step A helper: scans text against predefined keyword sets using regex.

    Enforces whole-word matching with word boundary at the start of each keyword
    while allowing plurals (so 'invoices' matches 'invoice', but 'costume' does NOT match 'cost').
    """
    normalized_text = text.lower()
    matches: Dict[Intent, Tuple[str, List[str]]] = {}

    for intent, rule in KEYWORD_RULES.items():
        agent_name = rule["agent"]
        matched_keywords: List[str] = []
        for kw in rule["keywords"]:
            # Word boundary at start; allow optional plural suffixes (s/es) followed by word boundary
            pattern = rf"\b{re.escape(kw)}(?:s|es)?\b"
            if re.search(pattern, normalized_text):
                matched_keywords.append(kw)

        if matched_keywords:
            matches[intent] = (agent_name, matched_keywords)

    return matches


def route_query(question: str) -> RouterDecision:
    """
    Route an incoming user question to appropriate financial agent(s).

    Process:
    Step A: Simple deterministic keyword rules.
      - If 2 or more areas match -> return Intent.multi_agent with all matching agents.
      - If exactly 1 area matches -> return that Intent with the designated agent.
    Step B: If no keywords match and an OpenAI API key is present:
      - Ask OpenAI to classify using structured output (RouterDecision).
      - If OpenAI is unavailable, unconfigured, or throws an error:
        return Intent.unknown with low confidence (0.0).

    GUARANTEE:
    This function will NEVER raise an exception / crash, and NEVER computes financial math.
    """
    if not question or not question.strip():
        return RouterDecision(
            intent=Intent.unknown,
            target_agents=[],
            confidence=0.0,
            reasoning="Empty or whitespace-only question received.",
        )

    # -------------------------------------------------------------
    # Step A: Deterministic Keyword Rules
    # -------------------------------------------------------------
    matches = _check_keyword_matches(question)

    if len(matches) >= 2:
        # Multi-agent match: spans two or more distinct financial domains
        target_agents: List[str] = [
            agent for agent, _ in matches.values()
        ]
        reason_parts = [
            f"{intent.value} ({', '.join(kws)})"
            for intent, (_, kws) in matches.items()
        ]
        return RouterDecision(
            intent=Intent.multi_agent,
            target_agents=target_agents,
            confidence=0.95,
            reasoning=f"Question spans multiple financial domains: {'; '.join(reason_parts)}.",
        )

    if len(matches) == 1:
        # Single domain match
        single_intent, (agent_name, matched_kws) = next(iter(matches.items()))
        return RouterDecision(
            intent=single_intent,
            target_agents=[agent_name],
            confidence=0.95,
            reasoning=f"Matched {single_intent.value} keywords: {', '.join(matched_kws)}.",
        )

    # -------------------------------------------------------------
    # Step B: LLM Fallback (OpenAI Structured Output)
    # -------------------------------------------------------------
    api_key = settings.OPENAI_API_KEY.strip() if settings.OPENAI_API_KEY else ""

    if api_key:
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=api_key,
                timeout=10,
                max_retries=1,
            )
            completion = client.beta.chat.completions.parse(
                model=settings.OPENAI_MODEL,
                messages=[
                    {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                response_format=RouterDecision,
            )
            decision = completion.choices[0].message.parsed
            if decision is not None:
                return decision
        except Exception as exc:
            # Log real error details for observability
            logger.warning(
                "OpenAI classification failed with error: %s. Falling back to unknown.",
                exc,
            )
            return RouterDecision(
                intent=Intent.unknown,
                target_agents=[],
                confidence=0.0,
                reasoning="AI classification was unavailable.",
            )

    # Fallback when no keywords matched and no API key exists (or LLM didn't return a decision)
    return RouterDecision(
        intent=Intent.unknown,
        target_agents=[],
        confidence=0.0,
        reasoning="No financial keywords matched and OpenAI classification was unavailable.",
    )
