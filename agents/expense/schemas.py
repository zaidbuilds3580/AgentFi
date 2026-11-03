"""
Pydantic schemas for Expense Agent and expense finance tools.
"""

from typing import List, Literal
from pydantic import BaseModel, Field, field_validator


class ExpenseAnomaly(BaseModel):
    """
    Individual flagged expense anomaly record.
    """
    anomaly_id: str = Field(
        ...,
        description="Unique identifier for the flagged anomaly."
    )
    category: str = Field(
        ...,
        description="Expense category (e.g. 'Software & SaaS', 'Office Supplies', 'Travel & Entertainment')."
    )
    vendor: str = Field(
        ...,
        description="Merchant or vendor name associated with the expense."
    )
    amount: float = Field(
        ...,
        description="Actual observed expense amount."
    )
    expected_baseline: float = Field(
        ...,
        description="Expected baseline expenditure for this vendor/category."
    )
    severity: Literal["low", "medium", "high"] = Field(
        ...,
        description="Assessed severity level: 'low', 'medium', or 'high'."
    )
    rule: str = Field(
        ...,
        description="Short text describing why this expense was flagged as an anomaly."
    )
    evidence_refs: List[str] = Field(
        default_factory=list,
        description="List of transaction identifiers supporting this flagged anomaly."
    )


class ExpenseAnomaliesResult(BaseModel):
    """
    Aggregated result containing detected expense anomalies over a date range.
    """
    business_id: str = Field(
        ...,
        description="Tenant business identifier. Must not be empty."
    )
    start_date: str = Field(
        ...,
        description="Start date of the analyzed spending period (YYYY-MM-DD)."
    )
    end_date: str = Field(
        ...,
        description="End date of the analyzed spending period (YYYY-MM-DD)."
    )
    anomalies: List[ExpenseAnomaly] = Field(
        default_factory=list,
        description="List of detected expense anomalies."
    )
    calculation_version: str = Field(
        default="v1.0.0-mock",
        description="Version identifier of the calculation algorithm used."
    )

    @field_validator("business_id")
    @classmethod
    def validate_business_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("business_id must not be empty.")
        return v.strip()


class ExpenseExplanation(BaseModel):
    """
    Structured output from OpenAI explaining the expense anomalies in plain English.
    """
    summary: str = Field(
        ...,
        description="High-level narrative summary explaining detected expense anomalies in plain words."
    )
    findings: List[str] = Field(
        default_factory=list,
        description="Observations based strictly and solely on the provided anomaly details and numbers."
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Practical recommendations based strictly and solely on the provided anomaly data."
    )
