"""
Pydantic response models and contract for the Financial Summary API.
Defines typed schemas consumed by Member 1 (Frontend Metric Cards) and Member 4 (Agent Tools).
All monetary values are strictly Decimals (never float).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, ConfigDict, Field


class BusinessSummaryHeader(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    currency: str


class PeriodHeader(BaseModel):
    label: str
    date_from: date
    date_to: date
    as_of: date
    days: int


class PreviousPeriodHeader(BaseModel):
    date_from: date
    date_to: date


class KpisSummary(BaseModel):
    total_inflow: Decimal = Field(default=Decimal("0.00"), description="Total cash inflows in period")
    total_outflow: Decimal = Field(default=Decimal("0.00"), description="Total cash outflows in period")
    net_cash_flow: Decimal = Field(default=Decimal("0.00"), description="Net cash flow (inflow - outflow)")
    total_expenses: Decimal = Field(default=Decimal("0.00"), description="Total expenses in period")
    transaction_count: int = Field(default=0, ge=0, description="Total transactions in period")


class AccountSummaryItem(BaseModel):
    id: uuid.UUID
    name: str
    account_type: str
    current_balance: Decimal


class CashSummary(BaseModel):
    total_cash_balance: Decimal = Field(
        default=Decimal("0.00"),
        description="Sum of active accounts' current_balance",
    )
    accounts: List[AccountSummaryItem] = Field(default_factory=list)


class ReceivablesSummary(BaseModel):
    total_outstanding: Decimal = Field(default=Decimal("0.00"), description="Total outstanding invoice balance")
    overdue_amount: Decimal = Field(default=Decimal("0.00"), description="Total overdue balance past due_date")
    overdue_count: int = Field(default=0, ge=0, description="Count of overdue open invoices")
    open_invoice_count: int = Field(default=0, ge=0, description="Count of open invoices")


class ExpenseCategoryBreakdown(BaseModel):
    category: str
    amount: Decimal
    share_pct: Decimal = Field(description="Percentage share of total expenses (0..100)")


class ExpensesSummary(BaseModel):
    top_categories: List[ExpenseCategoryBreakdown] = Field(
        default_factory=list,
        description="Top expense categories by spend (up to 5)",
    )


class ComparisonSummary(BaseModel):
    net_cash_flow_change_abs: Decimal = Field(description="Current net cash flow minus previous net cash flow")
    net_cash_flow_change_pct: Optional[Decimal] = Field(
        default=None,
        description="Percentage change in net cash flow (null if previous was zero)",
    )
    inflow_change_pct: Optional[Decimal] = Field(
        default=None,
        description="Percentage change in total inflow (null if previous was zero)",
    )
    outflow_change_pct: Optional[Decimal] = Field(
        default=None,
        description="Percentage change in total outflow (null if previous was zero)",
    )


class SummaryMeta(BaseModel):
    computed_by: str = Field(description="'finance_engine' or 'interim_backend_sql'")
    engine_version: Optional[str] = None
    generated_at: datetime
    data_coverage: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class FinancialSummaryResponse(BaseModel):
    business: BusinessSummaryHeader
    period: PeriodHeader
    previous_period: Optional[PreviousPeriodHeader] = None
    kpis: Optional[KpisSummary] = None
    cash: Optional[CashSummary] = None
    receivables: Optional[ReceivablesSummary] = None
    expenses: Optional[ExpensesSummary] = None
    comparison: Optional[ComparisonSummary] = None
    meta: SummaryMeta
