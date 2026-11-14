"""
Unit tests for Day 5 financial summary period resolution, adapter mapping, comparison math,
schema validation, and error translation (no DB required).
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import uuid
import pytest
from fastapi import HTTPException

from backend.schemas.financial_summary import (
    AccountSummaryItem,
    BusinessSummaryHeader,
    CashSummary,
    ComparisonSummary,
    ExpenseCategoryBreakdown,
    ExpensesSummary,
    FinancialSummaryResponse,
    KpisSummary,
    PeriodHeader,
    PreviousPeriodHeader,
    ReceivablesSummary,
    SummaryMeta,
)
from backend.services.finance_engine_adapter import (
    EngineSummary,
    FinanceSummaryProvider,
    InterimSummaryProvider,
    SummaryInputs,
)
from backend.services.financial_summary_service import get_financial_summary_service
from backend.utils.periods import (
    ALLOWED_PERIOD_PRESETS,
    PeriodRange,
    resolve_summary_period,
)


def test_resolve_period_presets():
    as_of = date(2026, 11, 14)

    # 1. last_30_days (default)
    curr, prev = resolve_summary_period("last_30_days", as_of=as_of, compare_previous=True)
    assert curr.date_to == as_of
    assert curr.date_from == as_of - timedelta(days=29)
    assert curr.days == 30
    assert prev is not None
    assert prev.days == 30
    assert prev.date_to == curr.date_from - timedelta(days=1)
    assert (prev.date_to - prev.date_from).days + 1 == 30

    # 2. this_month
    curr_m, prev_m = resolve_summary_period("this_month", as_of=as_of, compare_previous=True)
    assert curr_m.date_from == date(2026, 11, 1)
    assert curr_m.date_to == as_of
    assert curr_m.days == 14
    assert prev_m is not None
    assert prev_m.days == 14
    assert prev_m.date_to == date(2026, 10, 31)

    # 3. last_month
    curr_lm, prev_lm = resolve_summary_period("last_month", as_of=as_of, compare_previous=True)
    assert curr_lm.date_from == date(2026, 10, 1)
    assert curr_lm.date_to == date(2026, 10, 31)
    assert curr_lm.days == 31
    assert prev_lm is not None
    assert prev_lm.days == 31
    assert prev_lm.date_to == date(2026, 9, 30)

    # 4. this_quarter (Indian FY starts April = Month 4; Nov is in Q3: Oct-Dec)
    curr_q, prev_q = resolve_summary_period("this_quarter", as_of=as_of, fy_start_month=4)
    assert curr_q.date_from == date(2026, 10, 1)
    assert curr_q.date_to == as_of

    # 5. last_quarter (Q2: Jul-Sep)
    curr_lq, prev_lq = resolve_summary_period("last_quarter", as_of=as_of, fy_start_month=4)
    assert curr_lq.date_from == date(2026, 7, 1)
    assert curr_lq.date_to == date(2026, 9, 30)

    # 6. this_fy / ytd (Apr 1, 2026 to Nov 14, 2026)
    curr_fy, _ = resolve_summary_period("this_fy", as_of=as_of, fy_start_month=4)
    assert curr_fy.date_from == date(2026, 4, 1)
    assert curr_fy.date_to == as_of

    # 7. last_fy (Apr 1, 2025 to Mar 31, 2026)
    curr_lfy, _ = resolve_summary_period("last_fy", as_of=as_of, fy_start_month=4)
    assert curr_lfy.date_from == date(2025, 4, 1)
    assert curr_lfy.date_to == date(2026, 3, 31)


def test_resolve_period_leap_year_and_month_ends():
    # Leap year 2028: Feb has 29 days
    as_of = date(2028, 3, 15)
    curr, prev = resolve_summary_period("last_month", as_of=as_of)
    assert curr.date_from == date(2028, 2, 1)
    assert curr.date_to == date(2028, 2, 29)
    assert curr.days == 29

    # Non-leap year 2027: Feb has 28 days
    as_of_non_leap = date(2027, 3, 15)
    curr_nl, _ = resolve_summary_period("last_month", as_of=as_of_non_leap)
    assert curr_nl.date_from == date(2027, 2, 1)
    assert curr_nl.date_to == date(2027, 2, 28)
    assert curr_nl.days == 28


def test_resolve_period_custom_validations():
    # 1. Missing dates for custom
    with pytest.raises(HTTPException) as exc:
        resolve_summary_period("custom", date_from=None, date_to=None)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "MISSING_CUSTOM_DATES"

    # 2. Inverted dates
    with pytest.raises(HTTPException) as exc:
        resolve_summary_period("custom", date_from=date(2026, 12, 1), date_to=date(2026, 1, 1))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_DATE_RANGE"

    # 3. Span > 731 days
    with pytest.raises(HTTPException) as exc:
        resolve_summary_period("custom", date_from=date(2020, 1, 1), date_to=date(2024, 1, 1))
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "PERIOD_TOO_LARGE"

    # 4. Invalid preset name
    with pytest.raises(HTTPException) as exc:
        resolve_summary_period("invalid_preset")
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "INVALID_PERIOD_PRESET"


def test_interim_provider_computation():
    provider = InterimSummaryProvider()
    b_id = uuid.uuid4()

    class MockAccount:
        id = uuid.uuid4()
        name = "HDFC Bank"
        account_type = "bank"
        current_balance = Decimal("25000.00")

    class MockTxn:
        amount = Decimal("5000.00")
        transaction_type = "inflow"

    class MockExp:
        amount = Decimal("1500.00")
        category = "Cloud Hosting"

    class MockInv:
        amount_total = Decimal("8000.00")
        amount_paid = Decimal("3000.00")
        status = "sent"
        due_date = date(2026, 10, 1)

    inputs = SummaryInputs(
        business=None,
        accounts=[MockAccount()],
        transactions=[MockTxn()],
        invoices=[MockInv()],
        expenses=[MockExp()],
    )

    summary = provider.get_summary(
        business_id=b_id,
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 31),
        as_of=date(2026, 11, 1),
        currency="INR",
        data=inputs,
    )

    assert summary.computed_by == "interim_backend_sql"
    assert summary.kpis.total_inflow == Decimal("5000.00")
    assert summary.kpis.total_outflow == Decimal("0.00")
    assert summary.kpis.net_cash_flow == Decimal("5000.00")
    assert summary.kpis.total_expenses == Decimal("1500.00")
    assert summary.cash.total_cash_balance == Decimal("25000.00")
    assert summary.receivables.total_outstanding == Decimal("5000.00")  # 8000 - 3000
    assert summary.receivables.overdue_amount == Decimal("5000.00")
    assert summary.receivables.overdue_count == 1
    assert len(summary.expenses.top_categories) == 1
    assert "interim_provider_in_use" in summary.warnings


def test_financial_summary_schema_validation():
    # Validates full Pydantic response construction and JSON serialization
    response = FinancialSummaryResponse(
        business=BusinessSummaryHeader(
            id=uuid.uuid4(),
            name="Apex Enterprises",
            currency="INR",
        ),
        period=PeriodHeader(
            label="last_30_days",
            date_from=date(2026, 10, 16),
            date_to=date(2026, 11, 14),
            as_of=date(2026, 11, 14),
            days=30,
        ),
        previous_period=PreviousPeriodHeader(
            date_from=date(2026, 9, 16),
            date_to=date(2026, 10, 15),
        ),
        kpis=KpisSummary(
            total_inflow=Decimal("120000.00"),
            total_outflow=Decimal("80000.00"),
            net_cash_flow=Decimal("40000.00"),
            total_expenses=Decimal("75000.00"),
            transaction_count=25,
        ),
        cash=CashSummary(
            total_cash_balance=Decimal("150000.00"),
            accounts=[
                AccountSummaryItem(
                    id=uuid.uuid4(),
                    name="Current A/C",
                    account_type="bank",
                    current_balance=Decimal("150000.00"),
                )
            ],
        ),
        receivables=ReceivablesSummary(
            total_outstanding=Decimal("45000.00"),
            overdue_amount=Decimal("12000.00"),
            overdue_count=2,
            open_invoice_count=4,
        ),
        expenses=ExpensesSummary(
            top_categories=[
                ExpenseCategoryBreakdown(
                    category="Payroll",
                    amount=Decimal("50000.00"),
                    share_pct=Decimal("66.67"),
                )
            ]
        ),
        comparison=ComparisonSummary(
            net_cash_flow_change_abs=Decimal("10000.00"),
            net_cash_flow_change_pct=Decimal("33.33"),
            inflow_change_pct=Decimal("20.00"),
            outflow_change_pct=None,
        ),
        meta=SummaryMeta(
            computed_by="finance_engine",
            engine_version="finance-engine-1.1.0",
            generated_at=datetime.now(timezone.utc),
            data_coverage={"transactions": {"count": 25}},
            warnings=[],
        ),
    )

    dumped = response.model_dump(mode="json")
    assert dumped["business"]["name"] == "Apex Enterprises"
    assert dumped["kpis"]["net_cash_flow"] == "40000.00"
    assert dumped["comparison"]["outflow_change_pct"] is None


def test_engine_failure_simulation_502():
    class FailingProvider:
        def get_summary(self, **kwargs):
            raise RuntimeError("Engine calculation crash / unhandled exception in core math")

    from unittest.mock import MagicMock, patch
    from backend.schemas.canonical import DataRangeResponse, DataRangeSummary

    mock_db = MagicMock()
    mock_biz = MagicMock()
    mock_biz.id = uuid.uuid4()
    mock_biz.name = "Fail Test Biz"
    mock_biz.currency = "INR"
    mock_biz.timezone = "Asia/Kolkata"
    mock_biz.fiscal_year_start_month = 4
    mock_db.get.return_value = mock_biz

    dummy_coverage = DataRangeResponse(
        transactions=DataRangeSummary(count=1),
        invoices=DataRangeSummary(count=0),
        expenses=DataRangeSummary(count=0),
    )

    with patch("backend.services.financial_summary_service.get_accounts", return_value=[]), \
         patch("backend.services.financial_summary_service.get_customers", return_value=[]), \
         patch("backend.services.financial_summary_service.get_vendors", return_value=[]), \
         patch("backend.services.financial_summary_service.get_data_range", return_value=dummy_coverage), \
         patch("backend.services.financial_summary_service.fetch_invoices", return_value=[]), \
         patch("backend.services.financial_summary_service.fetch_transactions", return_value=[]), \
         patch("backend.services.financial_summary_service.fetch_expenses", return_value=[]):

        with pytest.raises(HTTPException) as exc:
            get_financial_summary_service(
                db=mock_db,
                business_id=mock_biz.id,
                period="last_30_days",
                provider=FailingProvider(),
            )

        assert exc.value.status_code == 502
        assert exc.value.detail["code"] == "ENGINE_UNAVAILABLE"
        assert "Engine calculation crash" not in exc.value.detail["message"]
        assert "Traceback" not in exc.value.detail["message"]


