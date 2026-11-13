"""
Common and reusable Pydantic models for pagination envelopes and aggregate totals.
"""

from decimal import Decimal
from typing import Generic, List, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """
    Standard pagination envelope for all collection endpoints.
    """
    model_config = ConfigDict(from_attributes=True)

    items: List[T]
    total: int = Field(ge=0, description="Total number of items matching the query")
    limit: int = Field(ge=1, le=200, description="Page limit applied")
    offset: int = Field(ge=0, description="Page offset applied")
    has_more: bool = Field(description="True if more items exist beyond this page")


class TransactionTotals(BaseModel):
    """
    Display-only summary totals over the entire filtered transaction set.
    """
    count: int = Field(ge=0, description="Total number of matching transactions")
    inflow_total: Decimal = Field(default=Decimal("0.00"), description="Sum of positive inflow amounts")
    outflow_total: Decimal = Field(default=Decimal("0.00"), description="Sum of positive outflow amounts")
    net: Decimal = Field(default=Decimal("0.00"), description="Net cash flow (inflow_total - outflow_total)")


class TransactionPage(Page[T], Generic[T]):
    totals: Optional[TransactionTotals] = Field(
        default=None,
        description="Aggregate summary totals across filtered set (only when include_totals=true)",
    )


class ExpenseTotals(BaseModel):
    """
    Display-only summary totals over the entire filtered expense set.
    """
    count: int = Field(ge=0, description="Total number of matching expenses")
    total_amount: Decimal = Field(default=Decimal("0.00"), description="Sum of expense amounts")


class ExpensePage(Page[T], Generic[T]):
    totals: Optional[ExpenseTotals] = Field(
        default=None,
        description="Aggregate summary totals across filtered set (only when include_totals=true)",
    )
