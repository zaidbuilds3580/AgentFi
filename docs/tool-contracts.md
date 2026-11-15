# AgentFi Tool Contracts Specification (Day 6 Freeze)

This document standardizes programmatic data access contracts for **Member 3 (Finance Engine)** and **Member 4 (LangGraph Agents)**. It defines server-side context injection, tool schemas, data sources, and the concrete action items needed to unify the system across all team members.

---

## 1. The Core Rule: Server-Injected Tenant Context

> **SECURITY INVARIANT**: The LLM **NEVER** chooses the tenant, and `business_id` is **NEVER** exposed as a tool parameter for the model to generate or pass.

1. When a user sends a prompt to the chat workflow, the server authenticates the JWT and creates an immutable `ToolContext`:
   ```python
   from backend.services.tool_context import build_tool_context

   # Created exclusively on the server from verified JWT claims
   ctx = build_tool_context(db=db, token_payload=user_token_payload)
   ```
2. The agent runtime passes `ToolContext` (or `ctx.business_id`) into tool invocations behind the scenes.
3. If an agent tool requires data, it queries `backend.services.read_api` passing `ctx`.

---

## 2. Standardized Tool Specifications

### 2.1 `get_financial_summary`
- **Owner**: Member 3 (Finance Engine)
- **Data Source**: `backend.services.read_api.fetch_transactions`, `fetch_invoices`, `fetch_accounts` (or HTTP `GET /api/financial-summary`)
- **Error Handling**: Returns `{ "error": { "code": "SUMMARY_FAILED", "message": "..." } }`. Never leaks stack traces.

| Parameter | Type | Injected By | Description |
|---|---|---|---|
| `tool_context` | `ToolContext` | **Server (Always)** | Verified tenant identity (`business_id`, `currency`, `as_of`). |
| `period` | `str` | LLM (Optional) | Preset: `last_30_days`, `this_month`, `last_month`, `this_quarter`, `ytd`, `custom`. |
| `date_from` | `date` | LLM (Optional) | Start date when `period="custom"`. |
| `date_to` | `date` | LLM (Optional) | End date when `period="custom"`. |
| `compare_previous` | `bool` | LLM (Optional) | Whether to compute previous equal period comparison (default: `True`). |
| `include` | `str` | LLM (Optional) | Comma-separated sections: `kpis,receivables,expenses,cash,comparison`. |

---

### 2.2 `get_overdue_invoices`
- **Owner**: Member 3 (Finance Engine)
- **Data Source**: `backend.services.read_api.fetch_invoices(db, ctx, status="sent")`
- **Error Handling**: Returns `{ "error": { "code": "INVOICE_QUERY_FAILED", "message": "..." } }`.

| Parameter | Type | Injected By | Description |
|---|---|---|---|
| `tool_context` | `ToolContext` | **Server (Always)** | Verified tenant identity and reference anchor date `as_of`. |
| `min_days_overdue` | `int` | LLM (Optional) | Minimum days past due date (default: `1`). |
| `sort_by` | `str` | LLM (Optional) | Sort order: `days_overdue`, `amount_due`, `due_date`. |
| `limit` | `int` | LLM (Optional) | Maximum invoices to return (default: `50`, max: `100`). |

---

### 2.3 `get_expense_anomalies`
- **Owner**: Member 3 (Finance Engine)
- **Data Source**: `backend.services.read_api.fetch_expenses(db, ctx, start_date=lookback_start)`
- **Error Handling**: Returns `{ "error": { "code": "ANOMALY_DETECTION_FAILED", "message": "..." } }`.

| Parameter | Type | Injected By | Description |
|---|---|---|---|
| `tool_context` | `ToolContext` | **Server (Always)** | Verified tenant identity and reference anchor date `as_of`. |
| `lookback_days` | `int` | LLM (Optional) | Historical baseline window in days (default: `90`). |
| `threshold_sigma` | `float` | LLM (Optional) | Z-score threshold for anomaly flag (default: `2.0`). |
| `category` | `str` | LLM (Optional) | Filter by specific expense category. |
| `min_amount` | `Decimal` | LLM (Optional) | Filter out small expenses below this amount. |

---

### 2.4 `forecast_cash_flow`
- **Owner**: Member 3 (Finance Engine)
- **Data Source**: `backend.services.read_api.fetch_transactions`, `fetch_invoices`, `fetch_expenses`
- **Error Handling**: Returns `{ "error": { "code": "FORECAST_FAILED", "message": "..." } }`.

| Parameter | Type | Injected By | Description |
|---|---|---|---|
| `tool_context` | `ToolContext` | **Server (Always)** | Verified tenant identity, account balances, anchor `as_of`. |
| `horizon_days` | `int` | LLM (Optional) | Forecast projection window (e.g. `30`, `60`, `90` days). |
| `scenario` | `str` | LLM (Optional) | Scenario model: `expected`, `optimistic`, `conservative`. |

---

## 3. Discovered Teammate Mismatches & Action Items

During the Day 6 discovery audit, the following concrete differences were identified between teammate folders and the backend service layer:

### Mismatch 1: Money Precision (`float` vs `Decimal`)
- **Found In**: `agents/*/schemas.py` and some tools in `finance_engine/` cast monetary values to Python `float`.
- **Backend Standard**: The database and backend schemas strictly use `Decimal(18, 2)` to eliminate floating-point rounding errors in financial ledgers.
- **Action for Member 4**: In `agents/cashflow/schemas.py`, `agents/invoice/schemas.py`, and `agents/expense/schemas.py`, change monetary field types from `float` to `Union[Decimal, float]` or accept `Decimal` directly from tools.

### Mismatch 2: FinancialSummary Schema Shape
- **Found In**: `agents/cashflow/schemas.py:FinancialSummary` expects flat properties (`inflows`, `outflows`, `net_cash_flow`, `cash_balance`, `receivables`).
- **Backend Standard**: `FinancialSummaryResponse` in `backend/schemas/financial_summary.py` returns structured nested dictionaries (`kpis`, `cash`, `receivables`, `expenses`, `comparison`, `meta`).
- **Action for Member 3 & 4**: Standardize on `FinancialSummaryResponse` or have Member 3's tool flatten the KPIs into `kpis.net_cash_flow`, etc., when feeding agent state.

### Mismatch 3: Offline File Reads vs Live Database
- **Found In**: `finance_engine/tools.py` reads offline CSVs from `data/processed/<business_id>/`.
- **Backend Standard**: `backend/services/read_api.py` provides `fetch_transactions`, `fetch_invoices`, `fetch_expenses`, `fetch_accounts`, `fetch_customers`, `fetch_vendors` directly from Supabase PostgreSQL.
- **Action for Member 3**: In `finance_engine/tools.py`, accept `ToolContext` and call `backend.services.read_api` when running inside the web application, retaining file reads only for offline benchmark tests.

### Mismatch 4: Invoice Field Names
- **Found In**: `agents/invoice/schemas.py:OverdueInvoice` expects `amount: float` (representing outstanding balance) and `days_overdue: int`.
- **Backend Standard**: Canonical `InvoiceRead` has `amount_total: Decimal`, `amount_paid: Decimal`, `due_date: date`. The enriched `InvoiceListItemRead` exposes `outstanding_amount: Decimal`.
- **Action for Member 4**: Map `InvoiceListItemRead.outstanding_amount` to `OverdueInvoice.amount` in the agent tool wrapper.
