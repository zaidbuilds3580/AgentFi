"""
Print invoice aging, overdue actions and expense anomalies for a business.

Usage:
    python -m scripts.receivables_report biz_001 --as-of 2026-11-05
    python -m scripts.receivables_report biz_001 --as-of 2026-11-05 --anomalies-from 2026-10-01 --anomalies-to 2026-10-31
    python -m scripts.receivables_report biz_001 --json

Run `python -m scripts.run_etl` first to create data/processed/<business_id>/.
"""

import argparse
import json
import sys
from datetime import date

from finance_engine import tools
from finance_engine.data import BusinessNotFoundError


def _m(x) -> str:
    return f"{x:>12,.2f}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("business_id")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="reference date (default today)")
    parser.add_argument("--grace-days", type=int, default=0)
    parser.add_argument("--anomalies-from", default=None, help="YYYY-MM-DD (default: first of as-of month)")
    parser.add_argument("--anomalies-to", default=None, help="YYYY-MM-DD (default: as-of)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    as_of = args.as_of or date.today()
    try:
        aging = tools.get_aging_report(args.business_id, as_of)
        overdue = tools.get_overdue_report(args.business_id, as_of, grace_days=args.grace_days)
        anomalies = tools.get_expense_anomalies(args.business_id, args.anomalies_from, args.anomalies_to or as_of,
                                                today=as_of)
    except (BusinessNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"aging": aging.model_dump(mode="json"), "overdue": overdue.model_dump(mode="json"),
                          "anomalies": anomalies.model_dump(mode="json")}, indent=2))
        return 0

    print(f"{args.business_id} | as of {as_of} | {aging.currency} | {aging.calculation_version}\n")
    print("RECEIVABLES AGING")
    print(f"  {'customer':<28}" + "".join(f"{b:>12}" for b in aging.bucket_names) + f"{'total':>12}  payer")
    for c in aging.customers:
        print(f"  {c.customer_name[:27]:<28}" + "".join(_m(c.buckets[b]) for b in aging.bucket_names)
              + _m(c.total) + f"  {c.reliability}")
    print(f"  {'TOTAL':<28}" + "".join(_m(aging.totals[b]) for b in aging.bucket_names) + _m(aging.total_outstanding))
    dso = f"{aging.dso} days" if aging.dso is not None else "n/a"
    print(f"  overdue {aging.total_overdue:,.2f} ({aging.overdue_share * 100:.1f}%) | "
          f"avg {aging.weighted_avg_days_overdue} days late | DSO {dso}")

    print("\nCUSTOMER PAYMENT BEHAVIOUR")
    for b in aging.payment_behaviour:
        if b.avg_days_late is None:
            avg = "no payment history"
        elif b.avg_days_late < 0:
            avg = f"{-b.avg_days_late} days early on average"
        else:
            avg = f"{b.avg_days_late} days late on average"
        print(f"  {b.customer_name[:27]:<28}{b.reliability:<11} {b.invoices_settled} settled, {avg}")

    print("\nOVERDUE INVOICES (collection order)")
    for i in overdue.overdue:
        why = f" ({'; '.join(i.priority_reasons)})" if i.priority_reasons else ""
        print(f"  [{i.priority.upper():8}] {i.invoice_number:<10} {i.customer_name[:24]:<25}{_m(i.balance)} "
              f"{i.days_overdue:>4} days  {i.stage}{why}")
        print(f"             -> {i.recommended_action}")
    if not overdue.overdue:
        print("  none")
    if overdue.due_soon:
        print("\nDUE SOON")
        for i in overdue.due_soon:
            print(f"  {i.invoice_number:<10} {i.customer_name[:24]:<25}{_m(i.balance)} due {i.due_date}")
    if overdue.status_corrections:
        print("\nSTATUS CORRECTIONS")
        for c in overdue.status_corrections:
            print(f"  {c.invoice_number:<10} {c.recorded_status} -> {c.derived_status} ({c.reason})")
    if overdue.overdue_bills:
        print("\nOVERDUE BILLS (payables)")
        for b in overdue.overdue_bills:
            print(f"  {b.vendor_name[:28]:<29}{_m(b.amount)} {b.days_overdue:>4} days  {b.description}")

    print(f"\nEXPENSE ANOMALIES {anomalies.period.start} to {anomalies.period.end} "
          f"({anomalies.outflows_checked} outflows checked)")
    for a in anomalies.anomalies:
        print(f"  [{a.severity.upper():6}] {a.rule_id} {a.category:<14} {a.vendor[:30]:<31}"
              f"{_m(a.amount)} vs {a.expected_baseline:>10,.2f}")
        print(f"           {a.rule}")
    if not anomalies.anomalies:
        print("  none")

    warnings = list(dict.fromkeys(aging.warnings + overdue.warnings + anomalies.warnings))
    if warnings:
        print("\nWARNINGS")
        for w in warnings:
            print(f"  ! {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
