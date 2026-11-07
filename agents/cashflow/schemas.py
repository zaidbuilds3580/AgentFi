"""
Pydantic schemas for Cash Flow Agent and finance tools.
"""

from typing import List, Literal, Union
from pydantic import BaseModel, Field, field_validator


class FinancialSummary(BaseModel):
    """
    Aggregated financial summary for a business over a given reporting period.
    """
    business_id: str = Field(
        ...,
        description="Tenant business identifier. Must not be empty."
    )
    period: str = Field(
        ...,
        description="Reporting period (e.g. 'current_month', 'last_30_days', 'Q1')."
    )
    inflows: float = Field(
        ...,
        description="Total cash inflows received during the period."
    )
    outflows: float = Field(
        ...,
        description="Total cash outflows disbursed during the period."
    )
    net_cash_flow: float = Field(
        ...,
        description="Net cash flow (inflows minus outflows)."
    )
    cash_balance: float = Field(
        ...,
        description="Current liquid cash balance at the end of the period."
    )
    receivables: float = Field(
        ...,
        description="Total outstanding customer receivables."
    )
    calculation_version: str = Field(
        default="v1.0.0-mock",
        description="Version identifier of the calculation algorithm used."
    )
    currency: str = Field(
        default="INR",
        description="Base currency code for all financial amounts (e.g. 'INR', 'USD')."
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Data gaps or validation warnings affecting the figures."
    )

    @field_validator("business_id")
    @classmethod
    def validate_business_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("business_id must not be empty.")
        return v.strip()


class CashFlowForecast(BaseModel):
    """
    Cash flow forecast metrics over a forward-looking horizon.
    """
    business_id: str = Field(
        ...,
        description="Tenant business identifier. Must not be empty."
    )
    horizon_days: int = Field(
        ...,
        description="Forecast horizon in days (e.g. 30, 60, 90)."
    )
    projected_balance: float = Field(
        ...,
        description="Projected liquid cash balance at the end of the horizon."
    )
    method: str = Field(
        ...,
        description="Forecasting methodology employed (e.g. 'linear_runway', 'arima', 'cash_burn')."
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        ...,
        description="Assessed liquidity risk level: 'low', 'medium', or 'high'."
    )
    data_coverage: float = Field(
        ...,
        description="Data completeness/coverage ratio (e.g. 0.95 for 95% coverage)."
    )
    currency: str = Field(
        default="INR",
        description="Base currency code for all forecast amounts."
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Data gaps or forecast assumptions affecting the figures."
    )

    @field_validator("business_id")
    @classmethod
    def validate_business_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("business_id must not be empty.")
        return v.strip()


class CashFlowExplanation(BaseModel):
    """
    Structured output from OpenAI explaining the cash flow figures in plain English.
    """
    summary: str = Field(
        ...,
        description="High-level narrative summary explaining the cash flow numbers in plain words."
    )
    findings: List[str] = Field(
        default_factory=list,
        description="Observations based strictly and solely on the provided numbers."
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Practical recommendations based strictly and solely on the provided numbers."
    )
