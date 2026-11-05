"""
Print revenue, expense and net-cash-flow figures for a business from processed data.

Usage:
    python -m scripts.finance_report biz_001                          # current month
    python -m scripts.finance_report biz_001 --period last_month --today 2026-11-05
    python -m scripts.finance_report biz_001 --period 2026-10-01..2026-11-04 --granularity week
    python -m scripts.finance_report biz_001 --period ytd --json       # full JSON (all reports)

Run `python -m scripts.run_etl` first to create data/processed/<business_id>/.
"""

import argparse
import json
import sys
from datetime import date

from finance_engine.data import BusinessNotFoundError
from finance_engine.periods import PeriodError
from finance_engine import tools


def _fmt(x) -> str:
    return f"{x:>15,.2f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("business_id")
    parser.add_argument("--period", default="current_month")
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    parser.add_argument("--granularity", default="month", choices=["day", "week", "month"])
    parser.add_argument("--json", action="store_true", help="print all reports as JSON")
    args = parser.parse_args(argv)

    kw = dict(period=args.period, today=args.today)
    try:
        summary = tools.get_financial_summary(args.business_id, **kw)
        revenue = tools.get_revenue(args.business_id, **kw)
        expenses = tools.get_expenses(args.business_id, **kw)
        cash = tools.get_net_cash_flow(args.business_id, granularity=args.granularity, **kw)
    except (BusinessNotFoundError, PeriodError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({name: r.model_dump(mode="json") for name, r in [
            ("summary", summary), ("revenue", revenue), ("expenses", expenses), ("cash_flow", cash)]}, indent=2))
        return 0

    cur = summary.currency
    print(f"{args.business_id} | {summary.period} | {cur} | {summary.calculation_version}\n")
    rows = [
        ("Revenue billed (net of tax)", revenue.billed_net),
        ("Revenue collected", revenue.collected_from_invoices),
        ("Other inflows", revenue.other_inflows),
        ("Expenses paid", expenses.paid_total),
        ("  of which operating", expenses.operating_paid),
        ("Expenses incurred (bills)", expenses.incurred_total),
        ("Opening cash", cash.opening_balance),
        ("Inflows", cash.inflows),
        ("Outflows", cash.outflows),
        ("Net cash flow", cash.net_cash_flow),
        ("Closing cash", cash.closing_balance),
        ("Receivables (end of period)", summary.receivables),
        ("  of which overdue", summary.overdue_receivables),
    ]
    for label, value in rows:
        print(f"{label:<30}{_fmt(value)}")

    print("\nExpenses paid by category")
    for b in expenses.paid_by_category:
        print(f"  {b.label:<28}{_fmt(b.amount)}  {b.share * 100:5.1f}%")

    print(f"\nCash flow by {cash.granularity}")
    print(f"  {'bucket':<12}{'inflows':>15}{'outflows':>15}{'net':>15}{'closing':>15}")
    for p in cash.series:
        print(f"  {p.label:<12}{_fmt(p.inflows)}{_fmt(p.outflows)}{_fmt(p.net_cash_flow)}{_fmt(p.closing_balance)}")

    if summary.warnings:
        print("\nWarnings")
        for w in summary.warnings:
            print(f"  ! {w}")
    if summary.notes:
        print("\nNotes")
        for n in summary.notes:
            print(f"  - {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
