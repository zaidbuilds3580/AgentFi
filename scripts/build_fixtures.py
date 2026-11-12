"""
Write the reporting fixture businesses as processed CSVs, so the app, the API and the agents
can use them like real data.

    python -m scripts.build_fixtures                     # -> data/processed/biz_fx_*/
    python -m scripts.build_fixtures --out /tmp/proc     # somewhere else
    python -m scripts.build_fixtures biz_fx_crunch       # just one

Then e.g.  python -m scripts.finance_report biz_fx_crunch --period last_month --today 2026-11-05
(with --out, set PROCESSED_DATA_DIR to the same folder so the tools find it)
"""

import argparse
import sys
from pathlib import Path

from finance_engine.fixtures import FIXTURE_IDS, SCENARIOS, write_fixtures


def main(argv=None) -> int:
    from config.settings import settings
    from finance_engine.data import _resolve_dir

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("business_ids", nargs="*", metavar="business_id",
                    help=f"default: all ({', '.join(FIXTURE_IDS)})")
    ap.add_argument("--out", help=f"processed data root (default: {settings.PROCESSED_DATA_DIR})")
    args = ap.parse_args(argv)

    root = _resolve_dir(args.out or settings.PROCESSED_DATA_DIR)
    ids = args.business_ids or FIXTURE_IDS
    unknown = [b for b in ids if b not in FIXTURE_IDS]
    if unknown:
        ap.error(f"unknown fixture(s): {', '.join(unknown)}. Choose from: {', '.join(FIXTURE_IDS)}")
    write_fixtures(root, ids)
    for biz in ids:
        print(f"  {biz:<16} {SCENARIOS[biz].description}  -> {root / biz}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
