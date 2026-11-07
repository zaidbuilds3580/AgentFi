"""
Pydantic schemas for Financial Intelligence Agent.
"""

from typing import Any, Dict, List, Literal
from pydantic import BaseModel, Field, model_validator

from graph.state.models import AgentResult


class IntelligenceRecommendation(BaseModel):
    """
    Actionable financial intelligence recommendation.

    Adheres to ActionProposal human-in-the-loop governance:
    Any external action recommendation must have requires_approval=True.
    """
    recommendation: str = Field(
        ...,
        description="Actionable advice or recommendation text for the business owner."
    )
    priority: Literal["high", "medium", "low"] = Field(
        ...,
        description="Priority level: 'high', 'medium', or 'low'."
    )
    reason: str = Field(
        ...,
        description="Short rationale explaining why this recommendation is suggested."
    )
    requires_approval: bool = Field(
        default=False,
        description="True if this recommendation suggests an external business action requiring human sign-off."
    )

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    @property
    def text(self) -> str:
        return self.recommendation

    @property
    def action(self) -> str:
        return self.recommendation

    @property
    def short_reason(self) -> str:
        return self.reason


class FinancialIntelligenceExplanation(BaseModel):
    """
    Structured output from OpenAI synthesizing cash flow, overdue invoices, and expenses.
    """
    overall_summary: str = Field(
        ...,
        description="Short overall financial health picture synthesized from specialist outputs."
    )
    links: List[str] = Field(
        default_factory=list,
        description="How cash, expenses, and receivables affect each other."
    )
    key_risks: List[str] = Field(
        default_factory=list,
        description="Key financial risks identified across cash flow, invoices, and expenses."
    )
    recommendations: List[IntelligenceRecommendation] = Field(
        default_factory=list,
        description="Prioritized recommendations with reasons and requires_approval flags."
    )


class FinancialIntelligenceResult(AgentResult):
    """
    Combined financial intelligence result aggregating Cash Flow, Invoice, and Expense findings.

    Subclasses AgentResult so it integrates directly into AgentState and multi-agent workflows.
    """
    agent_name: str = Field(
        default="intelligence",
        description="Name of the agent that produced this result."
    )
    overall_summary: str = Field(
        default="",
        description="Short overall health picture of the business."
    )
    links: List[str] = Field(
        default_factory=list,
        description="Cross-domain links showing how cash, expenses, and receivables affect each other."
    )
    key_risks: List[str] = Field(
        default_factory=list,
        description="Consolidated list of key financial risks."
    )
    recommendations: List[IntelligenceRecommendation] = Field(  # type: ignore[assignment]
        default_factory=list,
        description="Recommendations with priority, reason, and requires_approval."
    )
    caveats: List[str] = Field(
        default_factory=list,
        description="Specialist caveats copied word-for-word."
    )
    metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregated specialist metrics copied directly from tool results."
    )
    missing_parts: List[str] = Field(
        default_factory=list,
        description="Names of specialists that returned a limitation or were not provided."
    )
    requires_approval: bool = Field(
        default=False,
        description="True if any recommendation requires human sign-off."
    )

    @model_validator(mode="before")
    @classmethod
    def sync_summary_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "overall_summary" in data and not data.get("summary"):
                data["summary"] = data["overall_summary"]
            elif "summary" in data and not data.get("overall_summary"):
                data["overall_summary"] = data["summary"]
        return data

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)
