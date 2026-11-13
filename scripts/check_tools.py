"""
Health check for the four deterministic finance tools used by the agents.

Calls each tool exactly as the agents do (agents/*/finance_tools.py), checks the result
against the agent schema, and prints the key figures, timing and warnings.

Usage:
    python -m scripts.check_tools                              # biz_001, today
    python -m scripts.check_tools biz_001 --today 2026-11-05   # reproducible
    python -m scripts.check_tools biz_001 --json

Works on a fresh clone: if data/processed/<id> is missing, the tools build the data in
memory from data/raw/<id> or data/samples/<id>.

Exit code 0 = all four tools OK, 1 = at least one failed.
"""

import argparse
import json
import sys
import time
from datetime import date

from finance_engine.data import clear_cache


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("business_id", nargs="?", default="biz_001")
    parser.add_argument("--today", type=date.fromisoformat, default=None)
    parser.add_argument("--period", default="last_month")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from agents.cashflow import finance_tools as cf
    from agents.cashflow.schemas import CashFlowForecast, FinancialSummary
    from agents.expense import finance_tools as ex
    from agents.expense.schemas import ExpenseAnomaliesResult
    from agents.invoice import finance_tools as inv
    from agents.invoice.schemas import OverdueInvoicesResult

    today = args.today or date.today()
    b = args.business_id
    checks = [
        ("get_financial_summary", FinancialSummary, lambda: cf.get_financial_summary(b, args.period, today=today),
         lambda r: f"{r.period} | in {r.inflows:,.2f} out {r.outflows:,.2f} net {r.net_cash_flow:,.2f} "
                   f"cash {r.cash_balance:,.2f} AR {r.receivables:,.2f}"),
        ("forecast_cash_flow", CashFlowForecast, lambda: cf.forecast_cash_flow(b, 30, today=today),
         lambda r: f"{r.horizon_days} days -> {r.projected_balance:,.2f} | risk {r.risk_level} | "
                   f"coverage {r.data_coverage:.0%}"),
        ("get_overdue_invoices", OverdueInvoicesResult, lambda: inv.get_overdue_invoices(b, today.isoformat(), today=today),
         lambda r: f"{r.count} overdue, {r.total_amount:,.2f} | "
                   + ", ".join(f"{x.band}: {x.count}" for x in r.ageing_bands)),
        ("get_expense_anomalies", ExpenseAnomaliesResult,
         lambda: ex.get_expense_anomalies(b, None, today.isoformat(), today=today),
         lambda r: f"{r.start_date}..{r.end_date} | {len(r.anomalies)} anomalies "
                   + "(" + ", ".join(f"{a.severity}" for a in r.anomalies) + ")" if r.anomalies
                   else f"{r.start_date}..{r.end_date} | no anomalies"),
    ]

    clear_cache()
    results, ok = {}, True
    if not args.json:
        print(f"AgentFi finance tools check | business {b} | today {today}\n")
    for name, schema, call, describe in checks:
        start = time.perf_counter()
        try:
            r = call()
            assert isinstance(r, schema), f"returned {type(r).__name__}, expected {schema.__name__}"
            ms = (time.perf_counter() - start) * 1000
            results[name] = {"ok": True, "ms": round(ms, 1), "result": r.model_dump(mode="json")}
            if not args.json:
                print(f"  OK    {name:<24}{ms:7.1f} ms  {describe(r)}")
                for w in getattr(r, "warnings", []):
                    print(f"        ! {w}")
        except Exception as exc:  # report every failure, keep checking the others
            ok = False
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            if not args.json:
                print(f"  FAIL  {name:<24}{type(exc).__name__}: {exc}")

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\nall tools OK" if ok else "\nsome tools FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
