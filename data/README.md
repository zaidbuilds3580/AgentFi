# AgentFi Data Directory

| Folder        | Contents                                                              |
|---------------|-----------------------------------------------------------------------|
| `raw/`        | Source files exactly as received: bank CSVs, invoice sheets, exports. Never edited by hand. |
| `processed/`  | Cleaned output of the ETL layer, one file per canonical entity.       |

## Conventions

- Raw files go under `raw/<business_id>/<source>/`, e.g. `raw/biz_001/bank_csv/hdfc_2026_10.csv`.
- Processed files go under `processed/<business_id>/<entity>.csv`, where `<entity>` is a key of
  `CANONICAL_ENTITIES` in [finance_engine/schema/canonical.py](../finance_engine/schema/canonical.py):
  `businesses`, `accounts`, `counterparties`, `transactions`, `invoices`, `expenses`.
- Every processed row must validate against its canonical Pydantic model.
- Amounts are positive decimals; `direction` says whether money came in or went out.
- Dates are ISO 8601 (`YYYY-MM-DD`). Currency codes are ISO 4217 (`INR`, `USD`).
- Real financial data is git-ignored. Only `.gitkeep` files are committed.
