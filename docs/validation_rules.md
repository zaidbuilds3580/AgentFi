# AgentFi Validation Rules

Every record that enters AgentFi passes three layers of checks. Each rule has a stable ID that
appears in validation reports, tests and this document.

| Layer | Where | On failure |
|---|---|---|
| **F / R** – field & single-record rules | Pydantic models in [canonical.py](../finance_engine/schema/canonical.py) | Record cannot be built → **error** `F-00` in the report, row dropped |
| **X** – cross-record rules | [validators.py](../finance_engine/schema/validators.py) `validate_dataset()` | **error** – dataset must be fixed before agents use it |
| **W** – data-quality rules | `validate_dataset()` | **warning** – usable, but shown to the user / agent for review |
| **M** – mapping / parsing rules | [etl/mappers.py](../finance_engine/etl/mappers.py) | error or warning while reading a raw file |

Rule of thumb: **error** = the number would be wrong or ambiguous; **warning** = the number is right but
something about it needs a human look.

---

## 1. Field rules (F) – every entity

| ID | Rule | Example rejected |
|---|---|---|
| F-01 | `business_id` is required and non-empty on every record (tenant isolation) | expense without `business_id` |
| F-02 | Unknown columns are rejected (`extra="forbid"`) | `random_column="x"` |
| F-03 | Money is `Decimal`, never float; at most 2 decimal places | `10.555` |
| F-04 | Transaction / expense amounts are `> 0`; direction carries the sign | `amount=-100` |
| F-05 | Currency is a 3-letter ISO 4217 code, upper-cased on input | `"RUPEES"` |
| F-06 | Text is whitespace-stripped | `"  Acme "` → `"Acme"` |
| F-07 | Business-event dates are on/after `2000-01-01` (catches `1900-01-01` placeholders, 2-digit-year bugs) | `transaction_date=1900-01-01` |
| F-08 | Emails must look like `name@domain.tld`; stored lower-case | `"not-an-email"` |
| F-09 | `fiscal_year_start_month` is 1–12; `payment_terms_days ≥ 0`; `tax_rate` is a fraction 0–1 | `tax_rate=18` |
| F-00 | (report code) Any of the above failed while building a record from a raw row | – |

## 2. Single-record rules (R)

| ID | Entity | Rule |
|---|---|---|
| R-T01 | Transaction | `category` may only be set when `direction = outflow` |
| R-T02 | Transaction | `invoice_id` may only be set when `direction = inflow` |
| R-I01 | Invoice | `due_date ≥ issue_date` |
| R-I02 | Invoice | `amount_paid ≤ total`; at least one line item |
| R-I03 | Invoice | `status = paid` ⇒ `amount_paid = total` |
| R-I04 | Invoice | `status = partially_paid` ⇒ `0 < amount_paid < total` |
| R-I05 | InvoiceLineItem | line total = `qty × unit_price × (1 + tax_rate)`, rounded **per line** to 0.01 (ROUND_HALF_UP), as on GST invoices |
| R-E01 | Expense | `due_date ≥ expense_date` |
| R-E02 | Expense | `transaction_id` set ⇒ `is_paid = True` |

## 3. Cross-record rules (X) – errors

| ID | Rule |
|---|---|
| X-01 | Primary key unique per `(business_id, entity)` |
| X-02 | Every record's `business_id` is a known business, and references never cross tenants |
| X-03 | `(business_id, source, source_ref)` unique → the same bank line / invoice is never ingested twice |
| X-04 | Foreign keys resolve: `transaction.account_id/counterparty_id/invoice_id`, `invoice.customer_id`, `expense.vendor_id/transaction_id` |
| X-05 | *(warning)* `invoice.customer_id` should be a counterparty of type `customer` |
| X-06 | *(warning)* `expense.vendor_id` should not be a `customer` |
| X-07 | Transaction currency = its account's currency |
| X-08 | An expense can only be paid by an **outflow** transaction |

## 4. Data-quality rules (W) – warnings

| ID | Rule | Why it matters |
|---|---|---|
| W-01 | Transaction / expense date in the future | wrong date format or typo |
| W-02 | Outflow without a category (and not an invoice refund) | expense agent can't analyse it |
| W-03 | Probable duplicate: same account, date, amount, direction, description | overlapping statement exports |
| W-04 | `sent`/`partially_paid` invoice past due with balance → should be `overdue`; or `overdue` before due date | invoice agent reminders |
| W-05 | Fully paid invoice not marked `paid`; or `draft` with payments | stale status in source system |
| W-06 | `invoice.amount_paid` ≠ sum of bank inflows linked to it | reconciliation gap / double payment |
| W-07 | Invoice currency ≠ business base currency | FX conversion needed for cash-flow |
| W-08 | Expense `is_paid` but no linked transaction | cash payment or not reconciled |
| W-09 | Expense amount ≠ linked transaction amount | partial payment / bank charges |
| W-10 | Unpaid bill past its due date | payables alert |
| W-11 | Link candidate (invoice no. in narration, payment ref) not found → link dropped | missing invoice export |
| W-12 | Bank running balance doesn't match `previous ± amount` (checked within each statement file) | rows missing, reordered or edited |

## 4b. De-duplication rules (D) – Day 3, see [etl_pipeline.md](etl_pipeline.md)

Duplicates are **removed** before validation and logged in `_duplicates.csv`. Expected removals
(overlapping exports, re-runs) are `info`; suspicious ones (same file) are `warning`.

| ID | Entity | Duplicate when | Which copy is kept |
|---|---|---|---|
| D-01 | Transaction | same account and bank reference | first (bank lines never change) |
| D-02 | Transaction | same account, date, amount, direction and narration, **from a different file**; count-aware | first |
| D-03 | Invoice | same `invoice_id` | newest export, gaps filled from older |
| D-04 | Expense | same vendor and bill no. (or vendor, date, amount, description) | newest, gaps filled |
| D-05 | Counterparty | same normalised name (`party_key`) | merged, best display name |
| D-06 | Business / account | same ID | manifest (newest) |

## 4c. Quarantine (Q)

Rows rejected by an **M** (mapping), **F/R** (model) or **X** (dataset) error are moved to
`_quarantine/<entity>.csv` with an `_errors` column; they are never silently dropped. After each
quarantine pass the dataset is re-validated, because removing a record can break references to it.
This repeats until the loaded data has **zero errors**.

## 5. Mapping rules (M)

| ID | Severity | Rule |
|---|---|---|
| M-00 | error | Unknown source type in `manifest.json` |
| M-01 | error | Required header/columns not found in the file |
| M-02 | error | Unparseable date in a row that has an amount |
| M-03 | error | Unparseable amount, or both debit and credit filled on one row |
| M-04 | warning | Row has no amount (e.g. zero-value interest line) → skipped |
| M-05 | warning | Unknown invoice status label → defaults to `sent` |
| M-06 | warning | Invoice-level field differs between its line-item rows |
| M-07 | warning | Expense category text could not be mapped → `other` |

---

## 6. Suggested database constraints (for the PostgreSQL migrations)

These mirror the rules above so the DB rejects bad data even if it bypasses the ETL.

```sql
-- F-03 / F-04
amount        NUMERIC(14,2) NOT NULL CHECK (amount > 0),
amount_paid   NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK (amount_paid >= 0),
-- F-05
currency      CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
-- F-07
transaction_date DATE NOT NULL CHECK (transaction_date >= DATE '2000-01-01'),
-- enums (direction, status, category, account_type, counterparty_type)
direction     TEXT NOT NULL CHECK (direction IN ('inflow','outflow')),
-- R-T01 / R-T02
CHECK (category IS NULL OR direction = 'outflow'),
CHECK (invoice_id IS NULL OR direction = 'inflow'),
-- R-I01 / R-E01
CHECK (due_date >= issue_date),
-- X-01 / X-02: composite keys include business_id so FKs can never cross tenants
PRIMARY KEY (business_id, transaction_id),
FOREIGN KEY (business_id, account_id) REFERENCES accounts (business_id, account_id),
-- X-03
UNIQUE (business_id, source, source_ref)
```

R-I03/R-I04 (status vs. total) need the invoice total, which lives in line items. Either store a
`total` column, or keep these checks in the application layer.

## 7. Running the checks

```bash
python -m scripts.run_etl --today 2026-11-05           # sample business, prints the report
python -m pytest tests/test_validation_rules.py tests/test_etl.py
```

```python
from finance_engine.schema.validators import validate_dataset
report = validate_dataset({"accounts": [...], "transactions": [...]})
report.ok          # False if any error
report.summary()   # human-readable list
```
