# Real-World Data → Canonical Schema Mapping

How raw SME exports become canonical records ([canonical.py](../finance_engine/schema/canonical.py)).
Code: [finance_engine/etl/](../finance_engine/etl/). Rules referenced (W-xx, M-xx) are in
[validation_rules.md](validation_rules.md).

## Pipeline

```
data/raw/<biz>/manifest.json ─┐
  bank_csv/*.csv   ──map_bank_statement──┐
  invoices/*.csv   ──map_invoice_export──┼─► merge ─► link ─► validate_rows (F/R) ─► validate_dataset (X/W) ─► data/processed/<biz>/<entity>.csv
  expenses/*.csv   ──map_expense_sheet───┘
```

A fake but realistic sample business lives in [data/samples/biz_001/](../data/samples/biz_001/).
It is committed on purpose; real data goes in `data/raw/` (git-ignored).

```bash
python -m scripts.run_etl --today 2026-11-05
```

## Source inventory

| Source | Typical origin | Canonical entities | Mapper |
|---|---|---|---|
| Bank statement CSV (HDFC layout) | Netbanking → *Download statement* | `transactions` | `map_bank_statement(layout=HDFC_LAYOUT)` |
| Bank statement CSV (SBI layout) | YONO / OnlineSBI export | `transactions` | `layout=SBI_LAYOUT` |
| Bank CSV with one signed amount column | Other banks, fintech exports | `transactions` | `layout=GENERIC_SIGNED_LAYOUT` |
| Invoice export (one row per line item) | Zoho Books / Vyapar / QuickBooks | `invoices`, `counterparties` (customers) | `map_invoice_export` |
| Expense / bill register | Spreadsheet kept by the owner or accountant | `expenses`, `counterparties` (vendors) | `map_expense_sheet` |
| `manifest.json` | Written once per business | `businesses`, `accounts` | `run_business` |

New bank? Add a `BankLayout(...)` with its column names. No new code is needed.

---

## 1. Bank statement → `Transaction`

HDFC export columns: `Date, Narration, Chq./Ref.No., Value Dt, Withdrawal Amt., Deposit Amt., Closing Balance`

| Source column | Canonical field | Transform |
|---|---|---|
| `Date` (`DD/MM/YY`) | `transaction_date` | day-first parse (`parse_date`) |
| `Withdrawal Amt.` | `amount`, `direction = outflow` | strip `₹`, `,` (lakh format `1,00,000.00`) → Decimal(2dp) |
| `Deposit Amt.` | `amount`, `direction = inflow` | same; both filled → M-03 error |
| `Narration` | `description` | collapse whitespace |
| `Narration` (outflow) | `category` | keyword rules (`categorize`): SALARY→payroll, GST/TDS→taxes, RENT→rent, AWS→software… |
| `Narration` (inflow) | `invoice_id` | regex `INV-\d+` → `inv-1001`; kept only if that invoice exists (else W-11) |
| `Chq./Ref.No.` | `source_ref` | leading zeros stripped; used for de-dup (X-03) and expense linking |
| `Closing Balance` | *not stored* | running-balance check against account `opening_balance` (W-12) |
| `Value Dt` | *not stored* | – |
| *(constant)* | `source` | layout name, e.g. `hdfc_csv` |
| *(manifest)* | `account_id`, `currency`, `business_id` | from `manifest.json` |
| *(derived)* | `transaction_id` | `txn_` + SHA1(business, account, date, amount, direction, ref): the same file gives the same IDs on every run |

**Messy cases handled:** bank preamble lines before the header, `*****` separator rows, the
*STATEMENT SUMMARY* footer, blank rows, zero-amount rows (M-04), `Dr`/`Cr` suffixes, `(750.00)`
negatives, Excel UTF-8 BOM, header punctuation differences (`Chq./Ref.No.` = `chq ref no`).

SBI: `Txn Date, Value Date, Description, Ref No./Cheque No., Debit, Credit, Balance`, with dates like `05 Oct 2026`.
Generic: `Date, Description, Reference, Amount, Type, Balance`. A negative amount, `Dr` suffix or `Type=DR` means outflow.

## 2. Invoice export → `Invoice` + `InvoiceLineItem` + `Counterparty`

Columns: `Invoice Number, Invoice Date, Due Date, Customer Name, Customer Email, Payment Terms, Item Name, Quantity, Item Price, Tax %, Amount Paid, Invoice Status, Currency Code`

| Source column | Canonical field | Transform |
|---|---|---|
| `Invoice Number` | `invoice_number`, `source_ref` | as-is; rows grouped by this |
| `Invoice Number` | `invoice_id` | slug: `INV-1001` → `inv-1001` (same key bank narrations resolve to) |
| `Invoice Date` / `Due Date` | `issue_date` / `due_date` | day-first parse |
| `Customer Name` | `customer_id` + new `Counterparty` | `cust-` + slug(name), `counterparty_type = customer` |
| `Customer Email` | `Counterparty.email` | validated, lower-cased |
| `Payment Terms` (`Net 30`) | `Counterparty.payment_terms_days` | digits → `30` |
| `Item Name, Quantity, Item Price, Tax %` | one `InvoiceLineItem` per row | `18%` → `0.18`; blank qty → 1 |
| `Amount Paid` | `amount_paid` | first row wins; differing values → M-06 |
| `Invoice Status` | `status` | `Partially Paid`→`partially_paid`, `Open/Unpaid`→`sent`, `Closed`→`paid`, `Cancelled`→`void`; unknown → `sent` + M-05 |
| `Currency Code` | `currency` | default `INR`; ≠ base currency → W-07 |

The **total is not imported**. It is recomputed from line items (R-I05), so a wrong total in the
source is caught by R-I03/R-I04 or W-05.

## 3. Expense register → `Expense` + `Counterparty`

Columns: `Date, Bill No, Vendor, Category, Description, Amount, Currency, Paid, Due Date, Payment Ref`

| Source column | Canonical field | Transform |
|---|---|---|
| `Date` | `expense_date` | day-first parse |
| `Bill No` | `source_ref` | used in `expense_id` hash |
| `Vendor` | `vendor_id` + new `Counterparty` | `vend-` + slug(name), `counterparty_type = vendor` |
| `Category` (free text) | `category` | `categorize(Category)`, then `categorize(Description, Vendor)`, else `other` + M-07 |
| `Description` | `description` | as-is |
| `Amount` | `amount` | Decimal(2dp) |
| `Paid` (`Yes/No`) | `is_paid` | yes/true/1/paid → True |
| `Due Date` | `due_date` | optional |
| `Payment Ref` | `transaction_id` | matched to a bank **outflow** with the same `source_ref`; sets `is_paid=True` and copies the category onto the bank line. No match → W-11 |

## 4. `manifest.json` → `Business`, `Account`

Fields map 1:1 to the models. The W-12 running-balance check runs inside each statement file; the
first row of each file sets the baseline, because exports can start partway through a period.

---

## Sample dataset: planted problems

`data/samples/biz_001` is built to exercise every stage. The expected outcome is listed in
[etl_pipeline.md](etl_pipeline.md#sample-run).

## Open questions for the team

1. **DB tables vs schema:** the Day-2 table list has `users` but no `counterparties`. The canonical
   schema has `counterparties` (customers/vendors, which invoices and expenses reference) and no `users`.
   Proposal: add a `counterparties` table, and keep `users` (login/role/business membership) as an
   app-level table outside the canonical finance schema.
2. **Multi-currency:** W-07 only flags non-INR invoices. Do we need an FX-rate table?
3. **Bank → counterparty resolution:** `transaction.counterparty_id` is not filled yet (narrations are
   fuzzy). Fuzzy name matching could be a later ETL step.
