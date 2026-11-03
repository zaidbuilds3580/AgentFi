"""
Pydantic models for AgentFi state schema and router decisions.

IMPORTANT ARCHITECTURAL RULE:
State models must NEVER store raw financial records (such as complete banking ledgers,
raw transaction lists, or raw journal entries). Instead, store aggregated metrics,
summaries, findings, and actionable proposals. Raw data processing belongs exclusively
in the finance engine or isolated tool execution contexts.
"""

from enum import Enum
from typing import Any, Dict, List, Literal
from pydantic import BaseModel, Field, field_validator


class Intent(str, Enum):
    """
    Classifies the user's intent into specific financial domains.
    """
    cash_flow = "cash_flow"
    invoice = "invoice"
    expense = "expense"
    financial_intelligence = "financial_intelligence"
    reporting = "reporting"
    multi_agent = "multi_agent"
    unknown = "unknown"


class RouterDecision(BaseModel):
    """
    The output produced by the query router.
    
    Determines which specialized agent(s) should handle the incoming user request.
    The router NEVER calculates financial metrics or solves math problems; it only
    directs the query to the proper domain agent(s).
    """
    intent: Intent = Field(
        ...,
        description="The primary classified financial intent."
    )
    target_agents: List[str] = Field(
        default_factory=list,
        description="List of agent names required to address the query (e.g., ['cashflow', 'invoice'])."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for this routing decision, between 0.0 and 1.0."
    )
    reasoning: str = Field(
        ...,
        description="Brief explanation of why this routing path was selected."
    )


class AgentResult(BaseModel):
    """
    Standardized result returned by any domain-specific financial agent.
    
    Contains summaries, metrics, and insights rather than raw database records.
    """
    agent_name: str = Field(
        ...,
        description="Name of the agent that produced this result (e.g., 'cashflow_agent')."
    )
    summary: str = Field(
        ...,
        description="High-level narrative summary of findings suitable for SME business owners."
    )
    metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregated key performance indicators or computed figures (e.g., {'burn_rate': 4500.0})."
    )
    findings: List[str] = Field(
        default_factory=list,
        description="Specific observations or detected patterns."
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Actionable advice suggested for the business owner."
    )
    requires_approval: bool = Field(
        default=False,
        description="Flag indicating whether this result suggests an external action requiring human sign-off."
    )


class ActionProposal(BaseModel):
    """
    A proposed business action (e.g., sending payment reminders, drafting an invoice).
    
    RULE: requires_approval is ALWAYS True to enforce human-in-the-loop control for any
    external or consequential action.
    """
    proposal_id: str = Field(
        ...,
        description="Unique identifier for this action proposal."
    )
    action_type: str = Field(
        ...,
        description="Type of action (e.g., 'send_payment_reminder', 'adjust_budget_cap')."
    )
    target: str = Field(
        ...,
        description="Target entity or recipient (e.g., 'customer_inv_1092', 'vendor_acme')."
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters required to execute the action if approved."
    )
    rationale: str = Field(
        ...,
        description="Clear business justification for why this action is recommended."
    )
    requires_approval: bool = Field(
        default=True,
        description="Always True. SME financial actions must have explicit human approval."
    )
    status: Literal["pending", "approved", "rejected"] = Field(
        default="pending",
        description="Current workflow status of the proposal."
    )

    @field_validator("requires_approval")
    @classmethod
    def enforce_approval_always_true(cls, v: bool) -> bool:
        """Enforce that human approval cannot be disabled for action proposals."""
        if not v:
            raise ValueError("requires_approval must always be True for ActionProposal.")
        return True


class FinalResponse(BaseModel):
    """
    Structured final response synthesized for the user.
    """
    summary: str = Field(
        ...,
        description="Clear narrative summary addressing the user's financial inquiry."
    )
    evidence: List[str] = Field(
        default_factory=list,
        description="Key supporting facts, data points, or metrics backing the summary."
    )
    caveats: List[str] = Field(
        default_factory=list,
        description="Important limitations, assumptions, data gaps, or uncertainties."
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Actionable next steps or guidance for the business owner."
    )


