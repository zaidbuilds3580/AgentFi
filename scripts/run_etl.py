"""
AgentFi ETL: clean -> normalise -> dedupe -> validate -> load, for one business.

Usage:
    python -m scripts.run_etl                                  # sample business data/samples/biz_001
    python -m scripts.run_etl data/raw/biz_002                 # any folder with a manifest.json
    python -m scripts.run_etl --today 2026-11-05               # fixed reference date (reproducible)
    python -m scripts.run_etl --full-refresh                   # ignore previously processed data
    python -m scripts.run_etl --dry-run -v                     # write nothing, also list info messages

Outputs (data/processed/<business_id>/ unless --out is given):
    <entity>.csv              clean data, free of validation errors
    _quarantine/<entity>.csv  rejected rows with an _errors column (fix the source, re-run)
    _duplicates.csv           records removed by de-duplication and why
    _etl_report.json          stage statistics and every issue

Exit codes: 0 = clean run, 2 = some rows quarantined (clean data still written), 1 = fatal.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from finance_engine.etl import run_business, write_run_outputs
from finance_engine.schema.validators import Severity

ROOT = Path(__file__).resolve().parents[1]
ENTITIES = ["businesses", "accounts", "counterparties", "transactions", "invoices", "expenses"]
STAGES = ["existing", "extracted", "duplicates_removed", "quarantined", "loaded"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", nargs="?", default=str(ROOT / "data" / "samples" / "biz_001"),
                        help="business folder containing manifest.json")
    parser.add_argument("--out", help="output folder (default: data/processed/<business_id>)")
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="reference date YYYY-MM-DD")
    parser.add_argument("--full-refresh", action="store_true", help="do not merge with previously processed data")
    parser.add_argument("--dry-run", action="store_true", help="run all stages but write nothing")
    parser.add_argument("-v", "--verbose", action="store_true", help="also list info messages")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    manifest_path = folder / "manifest.json"
    if not manifest_path.exists():
        print(f"error: {manifest_path} not found", file=sys.stderr)
        return 1
    biz_id = json.loads(manifest_path.read_text(encoding="utf-8"))["business"]["business_id"]
    out = Path(args.out) if args.out else ROOT / "data" / "processed" / biz_id

    result = run_business(folder, today=args.today, processed_dir=out, incremental=not args.full_refresh)

    print(f"AgentFi ETL - {biz_id}\n")
    print(f"{'stage':<20}" + "".join(f"{e[:13]:>15}" for e in ENTITIES))
    for stage in STAGES:
        row = result.stats.get(stage)
        if row is not None:
            print(f"{stage:<20}" + "".join(f"{row.get(e, 0):>15}" for e in ENTITIES))

    r = result.report
    print(f"\n{len(r.errors)} error(s), {len(r.warnings)} warning(s), {len(r.infos)} info"
          + ("" if args.verbose else "  (use -v to list info)"))
    for issue in r.issues:
        if args.verbose or issue.severity != Severity.info:
            print(" ", issue)

    if not args.dry_run:
        print()
        for path in write_run_outputs(result, out):
            try:
                print(f"wrote {path.relative_to(ROOT)}")
            except ValueError:
                print(f"wrote {path}")

    if not result.clean:
        return 1
    return 2 if result.quarantined_count() else 0


if __name__ == "__main__":
    raise SystemExit(main())
