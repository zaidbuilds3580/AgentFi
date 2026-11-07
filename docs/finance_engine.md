# Deterministic Finance Engine

All AgentFi numbers come from here, never from an LLM. Agents call the tools, copy the figures,
and only *explain* them.

```bash
python -m scripts.run_etl --today 2026-11-05                                   # build processed data first
python -m scripts.finance_report biz_001 --period last_month --today 2026-11-05
python -m scripts.finance_report biz_001 --period 2026-10-01..2026-11-04 --granularity week
python -m scripts.finance_report biz_001 --period ytd --json
```

```python
from finance_engine.tools import get_revenue, get_expenses, get_net_cash_flow, get_financial_summary
report = get_financial_summary("biz_001", period="last_month")
report.net_cash_flow              # Decimal('-60161.50')
report.model_dump(mode="json")    # JSON-safe for agent state / API (Decimals as strings)
```

## Modules

| File | Responsibility |
|---|---|
| [periods.py](../finance_engine/periods.py) | Period labels → dates (Indian fiscal year by default), day/week/month buckets |
| [data.py](../finance_engine/data.py) | `FinanceData`: one tenant's validated records, sorted deterministically. Loaded from `data/processed/` or from in-memory models |
| [calculations.py](../finance_engine/calculations.py) | Pure functions: `calculate_revenue`, `calculate_expenses`, `calculate_net_cash_flow`, `calculate_receivables`, `financial_summary` |
| [receivables.py](../finance_engine/receivables.py) | Invoice aging by customer, DSO, payment behaviour, overdue detection |
| [anomalies.py](../finance_engine/anomalies.py) | Expense anomaly rules A1–A8 |
| [forecasting.py](../finance_engine/forecasting.py) | Cash-flow forecast |
| [tools.py](../finance_engine/tools.py) | Agent-facing wrappers: `get_*(business_id, period, today=None)` |
| [agents/cashflow/finance_tools.py](../agents/cashflow/finance_tools.py) | Drop-in replacement for the Cash Flow Agent's mock `get_financial_summary` |

## Guarantees

1. **Deterministic.** Pure functions of (data, period). No clock, randomness, LLM or I/O inside calculations.
   The input order doesn't matter (tested by shuffling). `today` is passed in explicitly.
2. **Exact.** `Decimal` everywhere, rounded once at output to 0.01 with ROUND_HALF_UP. Floats appear
   only in the agent adapter, because the agent schema uses them.
3. **Traceable.** Every breakdown row has `evidence_refs` (the transaction, invoice or expense IDs summed),
   and every result has `calculation_version`.
4. **Honest about gaps.** Each result lists `warnings` (missing bank data, foreign currency, uncategorized
   spend) and `notes` (what was excluded and why). Cash reports include `data_coverage` (0–1).
5. **Tenant-isolated.** `FinanceData` keeps only records with the requested `business_id`.
6. **Reconciled.** On the sample business, October inflows, outflows and closing balance equal the HDFC
   statement's own summary footer, and the 4 Nov closing balance equals the bank's (tested).

## Definitions

Two bases are reported side by side and are **never mixed**. Cash basis uses bank transactions, and
accrual basis uses invoices and bills, so an expense and the bank line that paid it are never counted twice.

### Revenue: `calculate_revenue`

| Field | Basis | Definition |
|---|---|---|
| `billed_net` | accrual | Σ line subtotals (qty × price, **excluding tax**) of invoices **issued** in the period with status `sent`, `partially_paid`, `paid` or `overdue` |
| `billed_tax` / `billed_gross` | accrual | GST/tax on those invoices / net + tax. Tax is a liability, not revenue |
| `collected_from_invoices` | cash | Bank inflows in the period **linked to an invoice** (the invoice can be from an earlier period) |
| `other_inflows` | cash | Inflows not linked to an invoice (interest, capital, loans, unmatched receipts). **Not** treated as revenue |
| `billed_by_customer`, `collected_by_customer` | | Breakdowns with share and evidence |

Excluded: `draft` and `void` invoices (listed in `notes`) and non-base-currency invoices (listed in `warnings`).

### Expenses: `calculate_expenses`

| Field | Basis | Definition |
|---|---|---|
| `paid_total` | cash | Σ bank **outflows** in the period |
| `operating_paid` | cash | `paid_total` minus `taxes` and `loan_repayment` (tax remittance and loan principal are not operating costs) |
| `paid_by_category` | cash | Grouped by transaction category; missing category → `uncategorized` (also a warning) |
| `incurred_total` | accrual | Σ expense records (bills) **dated** in the period, paid or not |
| `incurred_unpaid` | accrual | Part of `incurred_total` with `is_paid = False` |
| `incurred_by_category`, `incurred_by_vendor` | accrual | Breakdowns |

### Net cash flow: `calculate_net_cash_flow`

```
opening_balance  = account.opening_balance + Σ signed flows dated before period.start
net_cash_flow    = inflows − outflows                 (transactions dated in the period)
closing_balance  = opening_balance + net_cash_flow
```

- Computed per account (`by_account`) and in total, for base-currency accounts only.
- `series`: day / ISO-week (Mon–Sun) / calendar-month buckets clipped to the period. The buckets' net
  flows add up to the total, and the last bucket's closing balance equals the period's closing balance.
- `data_coverage` is the share of the period's days that fall between the first and last bank transaction.
- `account.opening_balance` means the balance **before the first transaction in the data**.
- With several accounts, transfers between them show up as both an inflow and an outflow (noted).

### Receivables: `calculate_receivables(as_of)`

Open `balance_due` on billed invoices issued on or before `as_of`, aged by days past due:
`current` (not yet due, including due today), `1-30`, `31-60`, `61-90`, `90+`. This uses the
**latest** `amount_paid` from the invoice export, so past `as_of` dates are an approximation (noted).

### Summary: `financial_summary`

Headline figures for one period, combining the four reports. Receivables are measured at `period.end`,
and warnings and notes are merged without duplicates.

## Periods

Labels (case-insensitive): `current_month`/`mtd`, `last_month`, `last_<N>_days`, `current_quarter`/`qtd`,
`last_quarter`, `Q1`–`Q4`, `ytd`/`current_fy`, `last_fy`, `FY2026` / `FY2026-27`, `2026-10`,
`2026-10-01..2026-10-31` (or `:` / ` to `).

Quarters and years follow `Business.fiscal_year_start_month` (default April):
Q1 Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec, Q4 Jan–Mar. A period that contains today is cut off at today,
with `is_partial = True`. Unknown labels raise `PeriodError` with the list of valid ones.

## Agent tool contracts (real tools for the mock contracts)

Each real tool has the **same name, arguments and return type** as its mock. Switching an agent is
one import change:

| Agent | Mock | Real tool | Switch in |
|---|---|---|---|
| Cash Flow | `get_financial_summary(business_id, period)` | `agents.cashflow.finance_tools.get_financial_summary` | `agents/cashflow/agent.py` |
| Cash Flow | `forecast_cash_flow(business_id, horizon_days)` | `agents.cashflow.finance_tools.forecast_cash_flow` | `agents/cashflow/agent.py` |
| Invoice | `get_overdue_invoices(business_id, as_of_date)` | `agents.invoice.finance_tools.get_overdue_invoices` | `agents/invoice/agent.py` |
| Expense | `get_expense_anomalies(business_id, start_date, end_date)` | `agents.expense.finance_tools.get_expense_anomalies` | `agents/expense/agent.py` |

```python
from agents.cashflow.finance_tools import forecast_cash_flow, get_financial_summary
from agents.invoice.finance_tools import get_overdue_invoices
from agents.expense.finance_tools import get_expense_anomalies
```

Behaviour that differs from the mocks:

- **Errors:** an unknown business raises `BusinessNotFoundError` (a `LookupError`). Bad periods or dates
  raise `ValueError`. The agents already catch these and return their "could not calculate" message (tested).
- **Defaults:** `today` replaces the mocks' hard-coded dates. Every real tool takes an optional
  `today=date(...)` keyword so tests and demos are reproducible.
- **`period`** in the summary is echoed with dates, e.g. `"last_month (2026-10-01 to 2026-10-31)"`.
- **`calculation_version`** is `"finance-engine-1.1.0"` (1.1.0 added the Day-5 aging, overdue and anomaly algorithms).
- **Overdue invoices:** `invoice_id` and `customer_id` are canonical IDs (`inv-1002`, `cust-greenleaf-organics`).
  All three bands (`1-30`, `31-60`, `61+`) are always returned. An invoice due on `as_of_date` is not overdue.
- **Anomalies:** the list can be **empty**. Categories use the canonical labels (`Software`, `Rent`, ...).
- **Currency:** amounts are in the business's base currency (INR for the sample), not dollars.
- **Agent tests:** the agents' existing tests assert the mocks' fixed numbers, so update them when switching.

Forecast method (`finance_engine/forecasting.py`, `run_rate_plus_receivables`):
`projected = current balance + expected collections (open invoices due within the horizon, weighted by
lateness) + other-inflow run rate × days − outflow run rate × days`, with run rates taken from the last
90 days of bank data. Risk is **high** if the projected balance is below 0 or the runway is shorter than
the horizon; **medium** if it is below 30 days of outflows or `data_coverage` < 0.5; otherwise **low**.

Anomaly rules: see "Expense anomaly detection" below.

## Invoice aging & overdue detection (`finance_engine/receivables.py`)

```bash
python -m scripts.receivables_report biz_001 --as-of 2026-11-05 --anomalies-from 2026-10-01 --anomalies-to 2026-10-31
```

| Function / tool | Output |
|---|---|
| `aging_report(data, as_of)` / `tools.get_aging_report` | Aging per customer × bucket (`current`, `1-30`, `31-60`, `61-90`, `90+`), totals, overdue share, balance-weighted average days overdue, **DSO**, payment behaviour |
| `detect_overdue(data, as_of, grace_days=0)` / `tools.get_overdue_report` | Overdue invoices in collection order with reminder stage and priority; invoices due in the next 3 days; status corrections; overdue bills (payables) |
| `payment_history(data)` | When each invoice was actually settled, from its linked bank inflows |
| `calculate_overdue_invoices` | The Invoice Agent contract (bands `1-30`, `31-60`, `61+`), unchanged |

**Definitions**
- **Outstanding** = invoice total incl. tax − `amount_paid`, for `sent`, `partially_paid`, `overdue` and `paid`
  invoices issued on/before `as_of`, in the base currency. An invoice due on `as_of` is `current`.
- **DSO** = outstanding ÷ gross billed in the last 90 days × 90.
- **Settlement date** = the day the linked bank inflows (cumulatively) reach the invoice total.
  `days_late` = settlement − due date (negative = early).
- **Payment behaviour:** **reliable** (average ≤ 5 days late and ≥ 70% paid within 3 days of the due date),
  **slow** (average ≤ 30), **unreliable** (> 30), **unknown** (no settled invoices in the bank data).
- **Reminder stage:** due in ≤ 3 days `due_soon` · 1–7 days `gentle_reminder` · 8–30 `firm_reminder` ·
  31–60 `final_notice` · 61+ `escalate`. Each stage has a `recommended_action`.
- **Priority:** **critical** if > 60 days overdue or ≥ 25% of all receivables; **high** if > 30 days,
  ≥ 10% of receivables, or the customer is a slow/unreliable payer; otherwise **normal**. `priority_reasons`
  says why. The list is ordered by priority, then days overdue, then balance.
- **Status corrections:** the status an invoice *should* have (`paid` if nothing is outstanding, `overdue` if
  past due + grace, `partially_paid`, otherwise `sent`) compared with the exported status.
- **Overdue bills:** unpaid expense records past their due date.

## Expense anomaly detection (`finance_engine/anomalies.py`)

Outflows in the window are compared with history **before** each payment, grouped by payee. The payee is
the vendor linked through the expense register (also inferred for older bank lines with the same narration),
otherwise the bank narration with reference numbers and month names removed.

| Rule | Fires when | Severity |
|---|---|---|
| **A1** unusual payment | ≥ 2× the median of ≥ 2 earlier payments to this payee (180 days), or, with ≥ 4 earlier payments, robust z-score (median/MAD) ≥ 3.5 and ≥ 1.5× median | ≥ 5× high, ≥ 3× medium, else low |
| **A2** possible duplicate | same amount, same payee, within 3 days | high |
| **A3** category spike | a **complete** calendar month's category spend ≥ 1.5× its monthly average over the previous 3 months (rise ≥ 1,000); skipped when A1/A6 already explain it | ≥ 3× high, ≥ 2× medium, else low |
| **A4** bill mismatch | payment ≠ linked bill amount | medium |
| **A5** uncategorized | outflow without a category | low |
| **A6** recurring change | a fixed monthly payment (≥ 3 earlier, amounts within 10%, interval 25–35 days) changed by ≥ 15% | ≥ 30% medium, else low |
| **A7** missing recurring | the next expected payment (last + usual interval, ± 7 days) did not happen and the bank data covers that date | low (amount 0, baseline = usual amount) |
| **A8** new payee, large | first payment to a payee in 180 days, ≥ 60 days of history, amount ≥ 5,000 and ≥ 2× the median outflow | medium |

On the sample business, October gives exactly four findings: the AWS bill jumped 3.3× (A1), GST was 20% above
its usual amount (A6), the uncategorized `MISC DR 5531` line (A5), and the October Airtel payment is missing (A7).

## Not yet covered

- FX conversion (foreign-currency records are excluded and flagged)
- Matching bank lines to counterparties (`transaction.counterparty_id` is not populated yet)
- The agent schemas have no `warnings` field, so data-gap warnings the engine produces don't reach the agents yet
