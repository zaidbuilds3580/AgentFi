# Financial Summary API Contract (Day 5)

> **Contract Freeze Notice**: API schemas and tool contracts freeze on **Day 6**. All team members (M1, M2, M3, M4) must align on field shapes and source functions before the freeze.

---

## Notes for Teammates

### For Member 1 (Frontend)
- **Metric Cards**: Map headline figures from `kpis` (`total_inflow`, `total_outflow`, `net_cash_flow`, `total_expenses`) and `cash.total_cash_balance`.
- **Decimal Strings**: In JSON responses, all currency numbers are formatted as fixed-point decimal strings (e.g. `"125000.00"`). Parse with your chosen decimal library or format directly for display.
- **Handling Data States**: Check `meta.warnings`:
  - `no_data`: The business currently has zero ingested records. Show a clean empty state with an upload CTA.
  - `period_outside_data_coverage`: The business has data in the system, but none exists in the selected date range. Use `meta.data_coverage` or `GET /api/meta/data-range` to pick a sensible default period.
- **Payload Trimming**: You can pass `?include=kpis,cash` to avoid fetching unused sections on lightweight dashboards.

### For Member 3 (Finance Engine)
- **Adapter Backing**: The backend adapter calls your pure calculation functions:
  - `financial_summary(data, period)`: Backs headline KPIs and cash flow figures.
  - `calculate_expenses(data, period)`: Backs `expenses.top_categories` (from `incurred_by_category`).
  - `calculate_receivables(data, as_of, period)`: Backs `receivables` (outstanding, overdue amount, open invoice count, and overdue invoice count).
- **In-Memory `FinanceData`**: The backend constructs an in-memory `FinanceData` container using Pydantic canonical models populated directly from PostgreSQL via `read_api`. No file I/O occurs on disk during execution.
- **Pure Arithmetic**: The backend does NOT compute finance formulas; 100% of arithmetic, rounding, and cash vs accrual rules are delegated to your engine.

### For Member 4 (LangGraph Agents)
- **Direct Service vs HTTP**: You can call `get_financial_summary_service(db, business_id, ...)` directly in Python inside your agent tool functions without HTTP overhead, or call `GET /api/financial-summary` via REST.
- **Error Handling**: If the engine fails or is unreachable, the API returns a controlled `502 Bad Gateway` with error code `ENGINE_UNAVAILABLE` and zero stack traces.

---

## 1. REST Endpoints

### 1. Primary Endpoint
`GET /api/businesses/{business_id}/financial-summary`
- **Tenant Security**: `business_id` in the URL path must match the `business_id` claim in the caller's JWT token. Mismatches return `403 Forbidden`.
- **Role Permission**: Any authenticated role (`owner`, `admin`, `viewer`) can read.

### 2. Convenience Alias
`GET /api/financial-summary`
- Automatically uses the `business_id` embedded in the JWT token.
- Returns the identical schema and data.

---

## 2. Query Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | string | `last_30_days` | Preset: `last_30_days`, `this_month`, `last_month`, `this_quarter`, `last_quarter`, `this_fy`, `last_fy`, `ytd`, `custom` |
| `date_from` | Date (`YYYY-MM-DD`) | null | Required only when `period=custom` |
| `date_to` | Date (`YYYY-MM-DD`) | null | Required only when `period=custom` |
| `as_of` | Date (`YYYY-MM-DD`) | null | Reference anchor date (default: today in the business timezone from `businesses.timezone`) |
| `compare_previous` | boolean | `true` | Compute the immediately preceding period of equal length |
| `include` | string | null | Comma-separated sections to include: `kpis,receivables,expenses,cash,comparison` (default all) |

---

## 3. Response Schema (Full JSON Example)

```json
{
  "business": {
    "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "name": "Apex Manufacturing Ltd",
    "currency": "INR"
  },
  "period": {
    "label": "last_30_days",
    "date_from": "2026-10-16",
    "date_to": "2026-11-14",
    "as_of": "2026-11-14",
    "days": 30
  },
  "previous_period": {
    "date_from": "2026-09-16",
    "date_to": "2026-10-15"
  },
  "kpis": {
    "total_inflow": "150000.00",
    "total_outflow": "95000.00",
    "net_cash_flow": "55000.00",
    "total_expenses": "92000.00",
    "transaction_count": 42
  },
  "cash": {
    "total_cash_balance": "285000.00",
    "accounts": [
      {
        "id": "e2a4a32a-5b12-4c28-912f-98124976cda1",
        "name": "HDFC Current Account",
        "account_type": "bank",
        "current_balance": "250000.00"
      },
      {
        "id": "f3b5b43b-6c23-5d39-a23e-09235087deb2",
        "name": "Petty Cash Wallet",
        "account_type": "cash",
        "current_balance": "35000.00"
      }
    ]
  },
  "receivables": {
    "total_outstanding": "75000.00",
    "overdue_amount": "25000.00",
    "overdue_count": 2,
    "open_invoice_count": 5
  },
  "expenses": {
    "top_categories": [
      {
        "category": "Payroll",
        "amount": "50000.00",
        "share_pct": "54.35"
      },
      {
        "category": "Rent",
        "amount": "25000.00",
        "share_pct": "27.17"
      },
      {
        "category": "Software",
        "amount": "12000.00",
        "share_pct": "13.04"
      }
    ]
  },
  "comparison": {
    "net_cash_flow_change_abs": "15000.00",
    "net_cash_flow_change_pct": "37.50",
    "inflow_change_pct": "25.00",
    "outflow_change_pct": "18.75"
  },
  "meta": {
    "computed_by": "finance_engine",
    "engine_version": "finance-engine-1.1.0",
    "generated_at": "2026-11-14T12:00:00Z",
    "data_coverage": {
      "transactions": { "min_date": "2026-06-01", "max_date": "2026-11-14", "count": 148 },
      "invoices": { "min_date": "2026-06-15", "max_date": "2026-11-10", "count": 74 },
      "expenses": { "min_date": "2026-06-01", "max_date": "2026-11-12", "count": 65 }
    },
    "warnings": []
  }
}
```

---

## 4. Field-by-Field Source Mapping

| Output Field | Engine Source Function | Model Attribute |
|---|---|---|
| `kpis.total_inflow` | `finance_engine.calculations.financial_summary` | `summary.inflows` |
| `kpis.total_outflow` | `finance_engine.calculations.financial_summary` | `summary.outflows` |
| `kpis.net_cash_flow` | `finance_engine.calculations.financial_summary` | `summary.net_cash_flow` |
| `kpis.total_expenses` | `finance_engine.calculations.calculate_expenses` | `exp_rep.incurred_total` |
| `cash.total_cash_balance`| `backend.services.read_api.get_accounts` | `sum(a.current_balance)` |
| `receivables.total_outstanding` | `finance_engine.calculations.calculate_receivables` | `ar_rep.total_outstanding` |
| `receivables.overdue_amount` | `finance_engine.calculations.calculate_receivables` | `ar_rep.overdue_amount` |
| `receivables.overdue_count` | `finance_engine.calculations.calculate_receivables` | `sum(b.count for b in ar_rep.aging if b.key != "current")` |
| `receivables.open_invoice_count` | `finance_engine.calculations.calculate_receivables` | `ar_rep.invoice_count` |
| `expenses.top_categories` | `finance_engine.calculations.calculate_expenses` | `exp_rep.incurred_by_category[:5]` |
| `comparison.*` | Computed across current & previous engine runs | Mathematical delta and percentage |

---

## 5. Warning Codes & Null Semantics

### Warnings (`meta.warnings`)
- `no_data`: Business has no records in the database.
- `period_outside_data_coverage`: No records exist in the selected date window, but data exists in other periods.
- `interim_provider_in_use`: Fallback interim provider was invoked instead of `finance_engine`.
- `partial_engine_output`: Previous period comparison could not be calculated.

### Null Semantics
- `comparison`: Null if `compare_previous=false`, or if previous period data cannot be resolved.
- `*_change_pct`: Individual percentage change fields are `null` if the baseline denominator value in the previous period was `0.00`.
- Trimmed Sections: Any section omitted via `?include=` evaluates to `null`.

---

## 6. Contracts to Confirm Before Day 6 Freeze

1. **Member 1 (Frontend)**:
   - Confirm metric card field names (`total_inflow`, `total_outflow`, `net_cash_flow`, `total_cash_balance`).
   - Confirm handling of `meta.warnings` for empty states.
2. **Member 3 (Finance Engine)**:
   - Confirm that `calculate_expenses` uses `incurred_by_category` for top spend categories.
   - Confirm receivables aging bucket labels (`current`, `1-30`, `31-60`, `61-90`, `90+`).
3. **Member 4 (LangGraph Agents)**:
   - Confirm if `CashFlowAgent` tool will invoke `get_financial_summary_service` directly or use the HTTP endpoint `/api/financial-summary`.
