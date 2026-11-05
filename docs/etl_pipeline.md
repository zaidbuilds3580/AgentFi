# AgentFi ETL Pipeline

One command turns a business's raw exports into clean, validated, de-duplicated data:

```bash
python -m scripts.run_etl                         # sample business (data/samples/biz_001)
python -m scripts.run_etl data/raw/biz_002        # a real business folder with a manifest.json
python -m scripts.run_etl --today 2026-11-05      # fixed reference date (reproducible reports)
python -m scripts.run_etl --full-refresh          # rebuild from raw files only
python -m scripts.run_etl --dry-run -v            # run every stage, write nothing, list info messages
```

Exit codes: `0` clean run, `2` some rows quarantined (clean data still written), `1` fatal error.

## Stages

| # | Stage | What happens | Code |
|---|---|---|---|
| 1 | **Extract** | Each file in `manifest.json` goes through its mapper (bank / invoice / expense) | [mappers.py](../finance_engine/etl/mappers.py) |
| 2 | **Clean** | Every cell: unicode NFKC, NBSP and zero-width characters removed, smart quotes and dashes straightened, whitespace collapsed, Excel `'` prefix and BOM removed; cp1252 fallback for old Excel files | [cleaning.py](../finance_engine/etl/cleaning.py) `clean_text` |
| 3 | **Normalize** | Dates → ISO; amounts → Decimal (lakh commas, ₹, Dr/Cr, parentheses); statuses and categories → enums; currency upper-cased; **customer/vendor names → one ID** (`M/s. Orange Realty L.L.P.` = `Orange Realty LLP` → `vend-orange-realty`) | `parsing.py`, `cleaning.party_key` |
| 4 | **Dedupe** | Removes duplicates across files **and** against already-processed data (rules D-01…D-06) | [dedup.py](../finance_engine/etl/dedup.py) |
| 5 | **Link** | Bank inflow `INV-1001` → invoice; expense `Payment Ref` → bank outflow (also categorizes that bank line) | [pipeline.py](../finance_engine/etl/pipeline.py) `_link` |
| 6 | **Validate + quarantine** | Pydantic rules (F/R) and dataset rules (X/W). Error rows move to quarantine; re-check until error-free | `pipeline._validate_and_quarantine` |
| 7 | **Load** | Atomic CSV writes + quarantine + duplicate log + JSON report | `pipeline.write_run_outputs` |

Rule IDs: [validation_rules.md](validation_rules.md). Column mappings: [data_mapping.md](data_mapping.md).

## Outputs: `data/processed/<business_id>/`

| File | Contents | Used by |
|---|---|---|
| `<entity>.csv` | Clean canonical data with zero validation errors | Finance engine, agents, DB load |
| `_quarantine/<entity>.csv` | Rejected rows with an `_errors` column | A human fixes the source file and re-runs |
| `_duplicates.csv` | Every record removed by dedupe, its source file and the reason | Audit trail |
| `_etl_report.json` | Stage counts, issue counts, every issue | Dashboard / data-quality view |

## Deduplication design

**Bank transactions are events.** They never change, so the **first** copy wins.
- **D-01:** the same bank reference on the same account is a duplicate.
- **D-02:** if there's no reference, a row is a duplicate when the same date, amount, direction and narration
  appears in **another file** (overlapping statement downloads, re-running the same file).
- Identical rows **inside one file** are kept, because two ₹250 Uber rides on one day are real. The validator flags them as W-03.
- Matching counts copies: if file A has 2 identical rows and file B has 3, one row from B is kept.

**Invoices, expenses and counterparties are documents.** They get updated, so the **newest** copy wins.
- Sources are read in manifest order (list them oldest → newest).
- Empty fields in the newer copy are filled from the older one.
- The original `ingested_at` and IDs are preserved.

**Incremental runs:** existing processed data is loaded first, so re-running the pipeline, or adding next
month's overlapping statement, never creates duplicates. Running the pipeline twice gives identical output
(tested). IDs are deterministic hashes, so the same source row always gets the same ID.

## Sample run

`data/samples/biz_001` contains planted problems. A fresh run (`--today 2026-11-05`) gives:

```
stage                    businesses   accounts  counterparties  transactions  invoices  expenses
extracted                         0          0              14            17         8         9
duplicates_removed                0          0               2             2         1         1
quarantined                       0          0               0             0         1         1
loaded                            1          1              12            15         6         8
```

| Planted problem | Handled by |
|---|---|
| Second bank statement overlaps the first by 3 rows (25–30 Oct) | D-01 (salary, has a ref), D-02 (`MISC  DR 5531 `, no ref, extra spaces), M-04 (zero-amount interest row) |
| Customer `BRIGHTPATH LEARNING PVT. LTD.` vs `BrightPath Learning` | merged into `cust-brightpath-learning` |
| Customer `GreenLeaf Organics` vs `Greenleaf Organics Pvt Ltd` across two exports | D-05 merged |
| Newer invoice export: INV-1002 now ₹50,000 paid, `Overdue` | D-03 newest wins; reconciles with 30k + 20k bank receipts (no W-06) |
| Rent bill typed twice, second time as `M/s. Orange Realty L.L.P.` | D-04 **warning** "entered twice" |
| Airtel row with NBSP, smart quotes, en dash, `₹`, currency `inr` | cleaned → ₹1,180.00 INR, linked to its bank payment |
| Expense dated `31/02/2026` | M-02 → quarantine |
| INV-1007 due before its issue date | R-I01 → quarantine |
| Inflow mentions `INV-1099`, which isn't in any export | W-11 link dropped |
| `MISC DR 5531` uncategorized; INV-1003 past due; USD invoice; unpaid CA bill; paid expenses without bank ref | W-02, W-04, W-07, W-10, W-08 |

## Adding a new business or source

1. Put the raw files under `data/raw/<business_id>/...` (git-ignored).
2. Write `data/raw/<business_id>/manifest.json`, copying the sample's format. List sources oldest → newest.
3. If the bank isn't HDFC or SBI, add a `BankLayout` in `mappers.py` with that bank's column names.
4. Run `python -m scripts.run_etl data/raw/<business_id>`, then check `_quarantine/` and the warnings.

## Loading into PostgreSQL (handoff to the DB migrations)

The processed CSVs match the canonical models column for column, so they can be bulk-loaded with
`COPY <table> FROM '<entity>.csv' CSV HEADER` once the tables exist. `line_items` is a JSON column
(`JSONB`). For upserts, use `(business_id, source, source_ref)` / primary keys as conflict targets, with the
same semantics as D-01…D-06.
