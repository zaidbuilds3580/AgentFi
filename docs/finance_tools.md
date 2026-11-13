# Finance Tools Reference (final)

The four deterministic tools the agents use. They are Member 3's final contract with the agents and
the backend. Every number an agent reports comes from one of these tools, never from the LLM.

```bash
python -m scripts.check_tools biz_001 --today 2026-11-05     # health check: all four tools, no setup needed
```

| Tool | Agent | Import |
|---|---|---|
| `get_financial_summary(business_id, period, today=None)` | Cash Flow | `agents.cashflow.finance_tools` |
| `forecast_cash_flow(business_id, horizon_days, today=None)` | Cash Flow | `agents.cashflow.finance_tools` |
| `get_overdue_invoices(business_id, as_of_date, today=None)` | Invoice | `agents.invoice.finance_tools` |
| `get_expense_anomalies(business_id, start_date, end_date, today=None)` | Expense | `agents.expense.finance_tools` |

Agents reach them through `agents/tool_source.py`, which switches between these and the mocks with
`USE_REAL_FINANCE_TOOLS` (default `True`). The richer engine versions with `Decimal` amounts, notes and
evidence live in `finance_engine.tools`.

## Behaviour shared by all four

| | |
|---|---|
| **Data** | `data/processed/<id>/` if present. Otherwise the ETL runs **in memory** on `data/raw/<id>/` or `data/samples/<id>/` (whichever has a `manifest.json`), so a fresh clone works with no setup. Nothing is written to disk. |
| **Caching** | Loaded data is cached per folder and reloaded automatically when any file changes. Thread-safe. After the first call each tool takes ~1–3 ms on the sample. |
| **`business_id`** | Trimmed. Must match `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`; anything else (e.g. `../x`) is rejected. |
| **`today`** | `date`, `datetime` or `'YYYY-MM-DD'`. Defaults to the real date. Pass it for reproducible results. |
| **Determinism** | Same data + same arguments + same `today` → identical result (tested, including 8 threads in parallel). |
| **Money** | Calculated in `Decimal`, rounded half-up to 2 dp, converted to `float` only in the agent schema. |
| **Currency** | `currency` = the business's base currency (`INR` for the sample). Foreign-currency records are excluded and named in `warnings`. |
| **`warnings`** | Data gaps that affect the numbers (missing bank days, FX exclusions, uncategorized spend). Agents show them word for word as caveats. |
| **`calculation_version`** | `finance-engine-1.1.0`. Changes only when a calculation changes. |

## Errors

All errors are `finance_engine.errors.FinanceToolError` with a stable `.code`. They also subclass the
built-ins, so `except ValueError` / `except LookupError` keeps working.

| Error | `.code` | Also a | When |
|---|---|---|---|
| `BusinessNotFoundError` | `business_not_found` | `LookupError` | no processed, raw or sample data for the business |
| `InvalidInputError` | `invalid_input` | `ValueError` | bad `business_id`, date, window, horizon or granularity |
| `PeriodError` | `invalid_period` | `ValueError` | period label not understood (subclass of `InvalidInputError`) |

Messages are safe to show to users (no server paths). The agents already catch every tool error and reply
"could not calculate right now" with no numbers.

---

## 1. `get_financial_summary(business_id, period="current_month", today=None) → FinancialSummary`

Cash-basis figures for one period, from bank transactions, plus receivables at the end of the period.

**`period`**: `current_month`/`mtd`, `last_month`, `last_<N>_days` (also "last 30 days"), `current_quarter`,
`last_quarter`, `Q1`–`Q4`, `ytd`, `last_fy`, `FY2026`, `2026-10`, `October 2026`, `oct-2026`, `october`,
or `2026-10-01..2026-10-31`. Quarters and years follow the Indian financial year (April start). A period
that contains `today` ends at `today`.

| Field | Meaning |
|---|---|
| `inflows` / `outflows` | Σ bank money in / out in the period |
| `net_cash_flow` | inflows − outflows |
| `cash_balance` | closing bank balance at the end of the period |
| `receivables` | unpaid invoice balances (incl. tax) at the end of the period |
| `period` | label with dates, e.g. `last_month (2026-10-01 to 2026-10-31)` |

Sample (`biz_001`, `last_month`, today 2026-11-05): in 1,01,000.00 · out 1,61,161.50 · net −60,161.50 ·
cash 39,838.50 · receivables 50,300.00. These match the HDFC statement's own summary.

## 2. `forecast_cash_flow(business_id, horizon_days=30, today=None) → CashFlowForecast`

`horizon_days`: 1–365. Numeric strings such as `"30"` are accepted.

Method `run_rate_plus_receivables`:
```
projected = current balance + expected collections + other-inflow rate × days − outflow rate × days
```
- **Rates** come from the last 90 days of bank data.
- **Expected collections** are open invoices due within the horizon, weighted by how late they already are:
  not yet due or up to 30 days late 100%, 31–60 days 50%, 61–90 days 25%, more than 90 days 0%.

| `risk_level` | when |
|---|---|
| `high` | projected < 0, or the cash runway is shorter than the horizon |
| `medium` | projected < 30 days of outflows, or `data_coverage` < 0.5 |
| `low` | otherwise |

`data_coverage` = share of the 90-day lookback that has bank data (0–1).

## 3. `get_overdue_invoices(business_id, as_of_date=None, today=None) → OverdueInvoicesResult`

Open invoices **past** their due date on `as_of_date` (default `today`; an invoice due that day is not
overdue), most overdue first. All three bands (`1-30`, `31-60`, `61+`) are always present.
`invoice_id`/`customer_id` are canonical IDs (`inv-1002`, `cust-greenleaf-organics`); `amount` is the
outstanding balance.

Sample (as of 2026-11-05): 2 invoices, 50,300.00 (INV-1002 is 16 days late with 32,600.00 outstanding;
INV-1003 is 5 days late with 17,700.00).
For reminder stages, collection priority and customer payment behaviour, use
`finance_engine.tools.get_overdue_report` / `get_aging_report`.

## 4. `get_expense_anomalies(business_id, start_date=None, end_date=None, today=None) → ExpenseAnomaliesResult`

Window default: first of the month of `end_date` → `end_date` (default `today`). Maximum 366 days.
**The list can be empty.** Sorted by severity (`high` → `low`), then amount.

| Rule | Detects | Severity |
|---|---|---|
| A1 | payment unusually large for this payee (≥ 2× median, or robust z ≥ 3.5) | by size of the jump |
| A2 | same amount to the same payee twice within 3 days | high |
| A3 | a complete month's category spend ≥ 1.5× its 3-month average | by ratio |
| A4 | payment ≠ linked bill | medium |
| A5 | outflow without a category | low |
| A6 | a fixed monthly payment changed ≥ 15% | low / medium |
| A7 | an expected monthly payment is missing | low |
| A8 | large first payment to a new payee | medium |

`amount` is what was paid (0 for A7). `expected_baseline` is what the rule compared it with.
`evidence_refs` holds the transaction/expense IDs involved. Full definitions: [finance_engine.md](finance_engine.md).

Sample (October 2026): A1 AWS bill 3.3× its usual amount · A6 GST +20% · A5 uncategorized ₹999 · A7 Airtel payment missing.
