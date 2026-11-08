"""
Pydantic schemas for the Reporting Agent.
"""

from enum import Enum
from typing import Any, Dict, List, Literal
from pydantic import BaseModel, Field

from graph.state.models import AgentResult
from agents.intelligence.schemas import IntelligenceRecommendation


class ReportType(str, Enum):
    """
    Supported report cadences.
    """
    DAILY = "daily"
    MONTHLY = "monthly"
    daily = "daily"
    monthly = "monthly"


class ReportEvidenceItem(BaseModel):
    """
    Supporting evidence item for the executive summary report.

    Copied directly from specialist and intelligence results.
    """
    label: str = Field(
        ...,
        description="Descriptive label for the evidence point."
    )
    value: str = Field(
        ...,
        description="Copied text representation of the metric or finding."
    )
    source: str = Field(
        ...,
        description="Agent or domain from which this evidence originated."
    )

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


# Alias for domain clarity and backwards compatibility
ReportingRecommendation = IntelligenceRecommendation


class ReportingExplanation(BaseModel):
    """
    Structured output from OpenAI for the reporting executive summary.
    """
    summary: str = Field(
        ...,
        description="Executive summary text for the report (2-3 sentences for daily, 4-6 sentences for monthly)."
    )


class ReportingResult(AgentResult):
    """
    Executive summary report aggregating financial intelligence and specialist results.

    Subclasses AgentResult so it integrates directly into AgentState and multi-agent workflows.
    """
    agent_name: str = Field(
        default="reporting",
        description="Name of the agent that produced this result."
    )
    report_type: ReportType = Field(
        ...,
        description="Report type: daily or monthly."
    )
    title: str = Field(
        ...,
        description="Title of the executive report."
    )
    summary: str = Field(
        ...,
        description="Executive narrative summary text."
    )
    evidence: List[ReportEvidenceItem] = Field(
        default_factory=list,
        description="List of supporting evidence items copied from inputs."
    )
    caveats: List[str] = Field(
        default_factory=list,
        description="Caveats and warnings copied word-for-word."
    )
    recommendations: List[IntelligenceRecommendation] = Field(  # type: ignore[assignment]
        default_factory=list,
        description="Actionable recommendations copied from the intelligence result."
    )
    missing_parts: List[str] = Field(
        default_factory=list,
        description="List of inputs or specialists that were missing or had limitations."
    )
    metrics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregated financial metrics copied directly from inputs."
    )
    currency: str = Field(
        default="INR",
        description="Reporting currency code (default INR, shown as ₹)."
    )
    requires_approval: bool = Field(
        default=False,
        description="True if any recommendation requires human sign-off."
    )

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_chat_response(self) -> Dict[str, Any]:
        """
        Format the reporting result as a plain dictionary for the Chat API.

        This dictionary conforms to the payload shape expected by POST /api/chat
        and rendered by the Chat UI. It contains exactly seven keys:
        summary, evidence, caveats, recommendations, missing_parts, report_type,
        and currency.

        Returns:
            Dict[str, Any]: Plain serializable dictionary for chat consumption.
        """
        return {
            "summary": self.summary,
            "evidence": [
                item.model_dump() if hasattr(item, "model_dump") else (dict(item) if isinstance(item, dict) else item)
                for item in self.evidence
            ],
            "caveats": list(self.caveats),
            "recommendations": [
                rec.model_dump() if hasattr(rec, "model_dump") else (dict(rec) if isinstance(rec, dict) else rec)
                for rec in self.recommendations
            ],
            "missing_parts": list(self.missing_parts),
            "report_type": self.report_type.value if hasattr(self.report_type, "value") else str(self.report_type),
            "currency": self.currency,
        }

