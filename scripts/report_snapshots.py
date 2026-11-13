"""
Golden snapshots for the Reporting Agent, backed by the deterministic finance engine.

For every reporting scenario (finance_engine/fixtures.py) and both cadences, this runs the full
chain with the REAL finance tools and no OpenAI key (so every summary is the deterministic
template):

    finance tools -> cashflow / invoice / expense agents -> intelligence agent -> reporting agent

and records every layer in tests/snapshots/reporting/<business_id>.json:

    tools   the agent-facing tool outputs (the numbers)
    agents  the specialist + intelligence AgentResults built from them
    report  the ReportingResult and its chat response

Cadences on the demo date 2026-11-05:
    daily     period current_month (1-5 Nov),  anomalies 1-5 Nov
    monthly   period last_month    (October),  anomalies 1-31 Oct

    python -m scripts.report_snapshots            # check: exit 1 and list what changed
    python -m scripts.report_snapshots --update   # rewrite the snapshot files after an intended change
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from finance_engine.fixtures import AS_OF, FIXTURE_IDS, SCENARIOS, write_fixtures

SNAPSHOT_DIR = Path(__file__).resolve().parents[1] / "tests" / "snapshots" / "reporting"
SNAPSHOT_VERSION = 1

CADENCES: Dict[str, Dict[str, str]] = {
    "daily": {"period": "current_month", "start_date": "2026-11-01", "end_date": AS_OF.isoformat()},
    "monthly": {"period": "last_month", "start_date": "2026-10-01", "end_date": "2026-10-31"},
}


@contextlib.contextmanager
def fixture_environment(processed_root: Optional[Path] = None) -> Iterator[Path]:
    """
    Settings for reproducible reports: real finance tools, no OpenAI key, fixed demo date, and
    PROCESSED_DATA_DIR pointing at freshly written fixtures. Everything is restored afterwards.
    """
    from config.settings import settings
    from finance_engine.data import clear_cache

    with contextlib.ExitStack() as stack:
        if processed_root is None:
            processed_root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="agentfi_fixtures_")))
            write_fixtures(processed_root)
        overrides = {"USE_REAL_FINANCE_TOOLS": True, "OPENAI_API_KEY": "",
                     "PROCESSED_DATA_DIR": str(processed_root), "DEMO_AS_OF_DATE": AS_OF.isoformat()}
        saved = {k: getattr(settings, k) for k in overrides}
        try:
            for k, v in overrides.items():
                setattr(settings, k, v)
            clear_cache()
            yield Path(processed_root)
        finally:
            for k, v in saved.items():
                setattr(settings, k, v)
            clear_cache()


def _dump(obj: Any) -> Any:
    return obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj


def run_cadence(business_id: str, cadence: str) -> Dict[str, Any]:
    """Run one business through every layer for one cadence. Call inside fixture_environment()."""
    from agents.cashflow import finance_tools as cashflow_tools
    from agents.cashflow.agent import cash_flow_agent
    from agents.expense import finance_tools as expense_tools
    from agents.expense.agent import expense_agent
    from agents.intelligence.agent import intelligence_agent
    from agents.invoice import finance_tools as invoice_tools
    from agents.invoice.agent import invoice_agent
    from agents.reporting.agent import reporting_agent

    c = CADENCES[cadence]
    today = AS_OF.isoformat()
    tools = {
        "financial_summary": cashflow_tools.get_financial_summary(business_id, period=c["period"], today=today),
        "forecast": cashflow_tools.forecast_cash_flow(business_id, 30, today=today),
        "overdue_invoices": invoice_tools.get_overdue_invoices(business_id, as_of_date=today, today=today),
        "expense_anomalies": expense_tools.get_expense_anomalies(business_id, start_date=c["start_date"],
                                                                 end_date=c["end_date"], today=today),
    }
    state = {"business_id": business_id, "today": today, "as_of_date": today, "period": c["period"],
             "horizon_days": 30, "start_date": c["start_date"], "end_date": c["end_date"]}
    cf, inv, exp = cash_flow_agent(dict(state)), invoice_agent(dict(state)), expense_agent(dict(state))
    intel = intelligence_agent(business_id=business_id, cashflow_result=cf, invoice_result=inv, expense_result=exp)
    report = reporting_agent(business_id=business_id, report_type=cadence, as_of_date=today,
                             intelligence_result=intel, cashflow_result=cf, invoice_result=inv, expense_result=exp)
    return {
        "inputs": c,
        "tools": {k: _dump(v) for k, v in tools.items()},
        "agents": {"cashflow": _dump(cf), "invoice": _dump(inv), "expense": _dump(exp), "intelligence": _dump(intel)},
        "report": _dump(report),
        "chat_response": json.loads(json.dumps(report.to_chat_response(), default=_dump)),
    }


def build_snapshot(business_id: str) -> Dict[str, Any]:
    """Snapshot of one business (both cadences). Call inside fixture_environment()."""
    s = SCENARIOS[business_id]
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "business_id": business_id,
        "scenario": s.name,
        "description": s.description,
        "as_of": AS_OF.isoformat(),
        "source": "finance_engine.fixtures" if s.builder else f"data/samples/{business_id}",
        **{cadence: run_cadence(business_id, cadence) for cadence in CADENCES},
    }


def snapshot_path(business_id: str) -> Path:
    return SNAPSHOT_DIR / f"{business_id}.json"


def to_text(snapshot: Dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def diff_paths(expected: Any, actual: Any, path: str = "$", limit: int = 20) -> List[str]:
    """Human-readable list of JSON paths that differ (first `limit`)."""
    out: List[str] = []

    def walk(e: Any, a: Any, p: str) -> None:
        if len(out) >= limit:
            return
        if isinstance(e, dict) and isinstance(a, dict):
            for k in list(dict.fromkeys([*e, *a])):
                if k not in e:
                    out.append(f"{p}.{k}: added")
                elif k not in a:
                    out.append(f"{p}.{k}: removed")
                else:
                    walk(e[k], a[k], f"{p}.{k}")
        elif isinstance(e, list) and isinstance(a, list):
            if len(e) != len(a):
                out.append(f"{p}: {len(e)} item(s) -> {len(a)}")
            for i, (x, y) in enumerate(zip(e, a)):
                walk(x, y, f"{p}[{i}]")
        elif e != a:
            out.append(f"{p}: {e!r} -> {a!r}")

    walk(expected, actual, path)
    return out


def all_scenarios() -> List[str]:
    return list(SCENARIOS)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true", help="rewrite the snapshot files")
    ap.add_argument("business_ids", nargs="*", help=f"default: all ({', '.join(all_scenarios())})")
    args = ap.parse_args(argv)

    changed = 0
    with fixture_environment():
        for biz in args.business_ids or all_scenarios():
            text = to_text(build_snapshot(biz))
            path = snapshot_path(biz)
            old = path.read_text(encoding="utf-8") if path.exists() else None
            if old == text:
                print(f"  ok       {biz}")
                continue
            changed += 1
            if args.update:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
                print(f"  written  {biz} -> {path.relative_to(SNAPSHOT_DIR.parents[2])}")
            else:
                print(f"  CHANGED  {biz}" if old else f"  MISSING  {biz}")
                for line in diff_paths(json.loads(old), json.loads(text)) if old else []:
                    print(f"           {line}")
    if changed and not args.update:
        print("\nRun `python -m scripts.report_snapshots --update` if these changes are intended.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
