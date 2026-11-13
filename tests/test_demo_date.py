"""
Tests for DEMO_AS_OF_DATE configuration, tool adapter propagation, and reporting agent title formatting.

Verifies:
1. Setting defaults to 2026-11-05.
2. Setting can be overridden (including empty fallback to system date).
3. Cash Flow adapter passes the date to financial engine tools.
4. Invoice adapter passes the date to financial engine tools.
5. Expense adapter passes the date to financial engine tools.
6. Reporting Agent title uses the demo date and respects overrides.
"""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest

from config.settings import Settings, get_as_of_date, settings
from agents.cashflow import finance_tools as cf_tools
from agents.invoice import finance_tools as inv_tools
from agents.expense import finance_tools as exp_tools
from agents.reporting.agent import reporting_agent
from agents.reporting.schemas import ReportType


def test_demo_as_of_date_setting_defaults_to_2026_11_05():
    """Verify DEMO_AS_OF_DATE setting defaults to 2026-11-05."""
    assert settings.DEMO_AS_OF_DATE == "2026-11-05"
    assert settings.get_as_of_date() == date(2026, 11, 5)
    assert settings.as_of_date == date(2026, 11, 5)
    assert get_as_of_date() == date(2026, 11, 5)


def test_demo_as_of_date_override_and_fallback(monkeypatch):
    """Verify DEMO_AS_OF_DATE can be overridden, and empty string falls back to system date."""
    # 1. Custom override
    monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", "2026-12-15")
    assert settings.get_as_of_date() == date(2026, 12, 15)

    # 2. Empty fallback to system date
    monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", "")
    assert settings.get_as_of_date() == date.today()

    # 3. None fallback to system date
    monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", None)
    assert settings.get_as_of_date() == date.today()

    # 4. Instantiation with empty string via Settings
    custom_settings = Settings(DEMO_AS_OF_DATE="")
    assert custom_settings.get_as_of_date() == date.today()


def test_cashflow_adapter_passes_demo_date_to_tools(monkeypatch):
    """Verify Cash Flow adapter passes demo date to financial summary and forecast tools."""
    mock_summary_report = MagicMock(
        business_id="biz_001",
        period="2026-11",
        inflows=100.0,
        outflows=50.0,
        net_cash_flow=50.0,
        cash_balance=500.0,
        receivables=200.0,
        calculation_version="v1",
        currency="INR",
        warnings=[],
    )
    mock_forecast_report = MagicMock(
        business_id="biz_001",
        horizon_days=30,
        projected_balance=600.0,
        method="linear",
        risk_level="low",
        data_coverage=1.0,
        currency="INR",
        warnings=[],
    )

    with patch("agents.cashflow.finance_tools._engine_summary", return_value=mock_summary_report) as mock_sum:
        with patch("agents.cashflow.finance_tools._engine_forecast", return_value=mock_forecast_report) as mock_fc:
            # 1. Default demo date (2026-11-05)
            cf_tools.get_financial_summary("biz_001")
            mock_sum.assert_called_once_with("biz_001", period="current_month", today=date(2026, 11, 5))

            cf_tools.forecast_cash_flow("biz_001")
            mock_fc.assert_called_once_with("biz_001", horizon_days=30, today=date(2026, 11, 5))

            # 2. Overridden date
            mock_sum.reset_mock()
            mock_fc.reset_mock()
            monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", "2026-12-01")

            cf_tools.get_financial_summary("biz_001")
            mock_sum.assert_called_once_with("biz_001", period="current_month", today=date(2026, 12, 1))

            cf_tools.forecast_cash_flow("biz_001")
            mock_fc.assert_called_once_with("biz_001", horizon_days=30, today=date(2026, 12, 1))


def test_invoice_adapter_passes_demo_date_to_tools(monkeypatch):
    """Verify Invoice adapter passes demo date to overdue invoices tool."""
    mock_overdue_report = MagicMock(
        business_id="biz_001",
        as_of=date(2026, 11, 5),
        count=1,
        total_amount=1000.0,
        ageing_bands=[],
        invoices=[],
        calculation_version="v1",
        currency="INR",
        warnings=[],
    )

    with patch("agents.invoice.finance_tools._engine_overdue", return_value=mock_overdue_report) as mock_inv:
        # 1. Default demo date
        inv_tools.get_overdue_invoices("biz_001")
        mock_inv.assert_called_once_with("biz_001", as_of_date=None, today=date(2026, 11, 5))

        # 2. Overridden date
        mock_inv.reset_mock()
        monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", "2026-12-01")

        inv_tools.get_overdue_invoices("biz_001")
        mock_inv.assert_called_once_with("biz_001", as_of_date=None, today=date(2026, 12, 1))


def test_expense_adapter_passes_demo_date_to_tools(monkeypatch):
    """Verify Expense adapter passes demo date to expense anomalies tool."""
    mock_anomalies_report = MagicMock(
        business_id="biz_001",
        period=MagicMock(start=date(2026, 11, 1), end=date(2026, 11, 5)),
        anomalies=[],
        calculation_version="v1",
        currency="INR",
        warnings=[],
    )

    with patch("agents.expense.finance_tools._engine_anomalies", return_value=mock_anomalies_report) as mock_exp:
        # 1. Default demo date
        exp_tools.get_expense_anomalies("biz_001")
        mock_exp.assert_called_once_with("biz_001", start_date=None, end_date=None, today=date(2026, 11, 5))

        # 2. Overridden date
        mock_exp.reset_mock()
        monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", "2026-12-01")

        exp_tools.get_expense_anomalies("biz_001")
        mock_exp.assert_called_once_with("biz_001", start_date=None, end_date=None, today=date(2026, 12, 1))


def test_reporting_agent_title_uses_demo_date(monkeypatch):
    """Verify Reporting Agent title formats with demo date and responds to overrides."""
    # 1. Default demo date (2026-11-05)
    daily_res = reporting_agent(business_id="biz_001", report_type="daily")
    assert daily_res.title == "Daily report - 5 November 2026"

    monthly_res = reporting_agent(business_id="biz_001", report_type="monthly")
    assert monthly_res.title == "Monthly report - 5 November 2026"

    # 2. Overridden demo date
    monkeypatch.setattr(settings, "DEMO_AS_OF_DATE", "2026-12-01")
    override_res = reporting_agent(business_id="biz_001", report_type="daily")
    assert override_res.title == "Daily report - 1 December 2026"

    # 3. Explicit as_of_date in kwargs
    explicit_res = reporting_agent(business_id="biz_001", report_type="daily", as_of_date="2027-01-15")
    assert explicit_res.title == "Daily report - 15 January 2027"

