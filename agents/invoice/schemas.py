"""
Pydantic schemas for Invoice Agent and invoice finance tools.
"""

from typing import List
from pydantic import BaseModel, Field, field_validator


class OverdueInvoice(BaseModel):
    """
    Individual overdue invoice record.
    """
    invoice_id: str = Field(
        ...,
        description="Unique identifier for the invoice."
    )
    customer_id: str = Field(
        ...,
        description="Customer or client identifier."
    )
    due_date: str = Field(
        ...,
        description="Invoice payment due date (YYYY-MM-DD)."
    )
    amount: float = Field(
        ...,
        description="Outstanding balance amount on the invoice."
    )
    days_overdue: int = Field(
        ...,
        description="Number of days past the invoice due date."
    )


class AgeingBand(BaseModel):
    """
    Aggregated ageing bucket for overdue receivables.
    """
    band: str = Field(
        ...,
        description="Ageing band label (e.g. '1-30', '31-60', '61+')."
    )
    count: int = Field(
        ...,
        description="Number of overdue invoices falling within this band."
    )
    total_amount: float = Field(
        ...,
        description="Cumulative outstanding amount in this ageing band."
    )


class OverdueInvoicesResult(BaseModel):
    """
    Aggregated overdue invoice report for a business as of a specific date.
    """
    business_id: str = Field(
        ...,
        description="Tenant business identifier. Must not be empty."
    )
    as_of_date: str = Field(
        ...,
        description="Cut-off date for calculating overdue ageing (YYYY-MM-DD)."
    )
    count: int = Field(
        ...,
        description="Total count of overdue invoices across all bands."
    )
    total_amount: float = Field(
        ...,
        description="Total outstanding dollar amount across all overdue invoices."
    )
    ageing_bands: List[AgeingBand] = Field(
        default_factory=list,
        description="Breakdown of overdue invoices by ageing bands."
    )
    invoices: List[OverdueInvoice] = Field(
        default_factory=list,
        description="List of individual overdue invoice details."
    )
    calculation_version: str = Field(
        default="v1.0.0-mock",
        description="Version identifier of the calculation algorithm used."
    )
    currency: str = Field(
        default="INR",
        description="Base currency code for overdue invoice amounts."
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Data gaps or invoice exclusions affecting the figures."
    )

    @field_validator("business_id")
    @classmethod
    def validate_business_id_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("business_id must not be empty.")
        return v.strip()


class InvoiceExplanation(BaseModel):
    """
    Structured output from OpenAI explaining the overdue invoice status in plain English.
    """
    summary: str = Field(
        ...,
        description="High-level narrative summary explaining overdue receivables in plain words."
    )
    findings: List[str] = Field(
        default_factory=list,
        description="Observations based strictly and solely on the provided invoice numbers and IDs."
    )
    recommendations: List[str] = Field(
        default_factory=list,
        description="Practical recommendations based strictly and solely on the provided invoice data (e.g. payment reminders)."
    )
