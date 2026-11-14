# AgentFi Backend API Reference

This reference documents the complete suite of RESTful API endpoints exposed by the AgentFi backend, including the Day 4 Read APIs, Day 3 Ingestion & Imports API, and Authentication endpoints.

---

## 1. Authentication & Tenant Context

All endpoints under `/api` (except `/api/health`, `/api/auth/register`, `/api/auth/login`, and `/api/imports/templates/{import_type}`) require an authenticated JWT Bearer token:

```http
Authorization: Bearer <access_token>
```

- **Tenant Isolation**: The user's `business_id` is cryptographically decoded from the JWT token and applied to every database query. Callers can never supply or override the tenant context via URL or request bodies.
- **Cross-Tenant Privacy**: Looking up a record (`/transactions/{id}`, `/invoices/{id}`, `/expenses/{id}`) belonging to another tenant returns `404 Not Found` (never 403), preventing ID enumeration.
- **Roles**: All authenticated roles (`owner`, `admin`, `viewer`) can execute read operations.

---

## 2. Common Query & Response Conventions

### Pagination Envelope: `Page[T]`
All collection endpoints return the standard envelope:
```json
{
  "items": [...],
  "total": 42,
  "limit": 50,
  "offset": 0,
  "has_more": false
}
```
- `limit`: Integer between 1 and 200 (default: 50). Values outside this range return `422 Unprocessable Entity`.
- `offset`: Integer >= 0 (default: 0). Negative values return `422 Unprocessable Entity`.

### Sorting
- `sort_by`: Column name whitelisted per endpoint.
- `sort_order`: `'asc'` or `'desc'` (default: endpoint specific).
- Stable Tie-Breaker: Primary key (`id`) is always appended to the SQL `ORDER BY` clause to guarantee deterministic pagination.

### Date & Amount Filters
- Dates: ISO format `YYYY-MM-DD`. Inverted ranges (`date_from > date_to`) return `422`.
- Amounts: Decimal >= 0. Inverted ranges (`min_amount > max_amount`) return `422`.
- Text Search (`q`): Case-insensitive substring match on key fields with wildcard escaping (`%`, `_`, `\`). Capped at 100 characters.

---

## 3. Financial Read Endpoints

### 3.1 Transactions

#### `GET /api/transactions`
List financial transactions with filtering, pagination, and optional summary totals.

**Query Parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `date_from` | Date (`YYYY-MM-DD`) | null | Filter transactions on or after date |
| `date_to` | Date (`YYYY-MM-DD`) | null | Filter transactions on or before date |
| `transaction_type` | string | null | `'inflow'` or `'outflow'` |
| `account_id` | UUID | null | Filter by financial account |
| `category` | string (repeatable) | null | Filter by categories (e.g. `?category=Sales&category=Services`) |
| `counterparty` | string | null | Exact match on counterparty name (case-insensitive) |
| `min_amount` | Decimal | null | Minimum amount (>= 0) |
| `max_amount` | Decimal | null | Maximum amount (>= 0) |
| `q` | string | null | Substring search on description, counterparty, reference |
| `source_dataset` | string | null | Filter by origin dataset / batch |
| `sort_by` | string | `transaction_date` | `transaction_date`, `amount`, `created_at`, `category` |
| `sort_order` | string | `desc` | `asc` or `desc` |
| `limit` | int | 50 | 1..200 |
| `offset` | int | 0 | >= 0 |
| `include_totals`| boolean | false | Include `totals` object in response envelope |

**Response (with `include_totals=true`):**
```json
{
  "items": [
    {
      "id": "c001fa4d-...",
      "business_id": "9b1deb4d-...",
      "account_id": "...",
      "transaction_date": "2026-10-15",
      "transaction_type": "inflow",
      "amount": "125000.00",
      "currency": "INR",
      "description": "Consulting retainers",
      "category": "Consulting Revenue",
      "counterparty": "Apex Corp",
      "reference": "INV-1001",
      "source_dataset": "csv_upload",
      "source_record_id": "row_1",
      "created_at": "2026-11-13T12:00:00Z",
      "updated_at": "2026-11-13T12:00:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "has_more": false,
  "totals": {
    "count": 1,
    "inflow_total": "125000.00",
    "outflow_total": "0.00",
    "net": "125000.00"
  }
}
```

#### `GET /api/transactions/{id}`
Fetch a single transaction by UUID. Returns `404 Not Found` if missing or owned by another tenant.

---

### 3.2 Invoices

#### `GET /api/invoices`
List sales invoices enriched with customer name and outstanding balance.

**Query Parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `status` | string (repeatable) | null | `draft`, `sent`, `partially_paid`, `paid`, `overdue`, `void` |
| `customer_id` | UUID | null | Filter by customer |
| `invoice_date_from` | Date | null | Issued on or after date |
| `invoice_date_to` | Date | null | Issued on or before date |
| `due_date_from` | Date | null | Due on or after date |
| `due_date_to` | Date | null | Due on or before date |
| `min_amount` | Decimal | null | Minimum amount_total |
| `max_amount` | Decimal | null | Maximum amount_total |
| `overdue` | boolean | null | Overdue relative to business timezone (`due_date < today` & unpaid) |
| `unpaid` | boolean | null | `amount_paid < amount_total` and status not void/paid |
| `q` | string | null | Substring search on invoice_number and customer name |
| `sort_by` | string | `due_date` | `due_date`, `invoice_date`, `amount_total`, `created_at`, `invoice_number` |
| `sort_order` | string | `asc` | `asc` or `desc` |
| `limit` | int | 50 | 1..200 |
| `offset` | int | 0 | >= 0 |

**Response Item Schema (`InvoiceListItemRead`):**
```json
{
  "id": "e456...",
  "business_id": "...",
  "customer_id": "...",
  "customer_name": "Apex Corp",
  "invoice_number": "INV-2026-001",
  "invoice_date": "2026-10-01",
  "due_date": "2026-10-31",
  "amount_total": "50000.00",
  "amount_paid": "20000.00",
  "outstanding_amount": "30000.00",
  "currency": "INR",
  "status": "sent",
  "payment_date": null,
  "notes": null,
  "source_dataset": "csv_upload",
  "source_record_id": "row_1",
  "created_at": "2026-11-13T12:00:00Z",
  "updated_at": "2026-11-13T12:00:00Z"
}
```

#### `GET /api/invoices/{id}`
Fetch single invoice by UUID. Returns `404 Not Found` if missing or owned by another tenant.

---

### 3.3 Expenses

#### `GET /api/expenses`
List business expenses enriched with vendor name and optional totals.

**Query Parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `date_from` | Date | null | Expense date on or after |
| `date_to` | Date | null | Expense date on or before |
| `category` | string (repeatable) | null | Filter by categories |
| `vendor_id` | UUID | null | Filter by vendor |
| `account_id` | UUID | null | Filter by financial account |
| `payment_method`| string (repeatable) | null | `cash`, `bank_transfer`, `card`, `upi`, `cheque`, `other` |
| `is_recurring` | boolean | null | Filter recurring expenses |
| `min_amount` | Decimal | null | Minimum amount |
| `max_amount` | Decimal | null | Maximum amount |
| `q` | string | null | Substring search on description, category, vendor name |
| `sort_by` | string | `expense_date` | `expense_date`, `amount`, `category`, `created_at` |
| `sort_order` | string | `desc` | `asc` or `desc` |
| `limit` | int | 50 | 1..200 |
| `offset` | int | 0 | >= 0 |
| `include_totals`| boolean | false | Include `totals` object |

**Response (with `include_totals=true`):**
```json
{
  "items": [
    {
      "id": "...",
      "business_id": "...",
      "vendor_id": "...",
      "vendor_name": "AWS Cloud Services",
      "account_id": "...",
      "transaction_id": null,
      "expense_date": "2026-10-15",
      "category": "Cloud Infrastructure",
      "amount": "14200.00",
      "currency": "INR",
      "description": "Monthly AWS hosting bill",
      "payment_method": "card",
      "is_recurring": true,
      "source_dataset": "csv_upload",
      "source_record_id": "row_1",
      "created_at": "2026-11-13T12:00:00Z",
      "updated_at": "2026-11-13T12:00:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0,
  "has_more": false,
  "totals": {
    "count": 1,
    "total_amount": "14200.00"
  }
}
```

#### `GET /api/expenses/{id}`
Fetch single expense by UUID. Returns `404 Not Found` if missing or owned by another tenant.

---

## 4. UI Lookup & Metadata Endpoints

### 4.1 Accounts Dropdown
`GET /api/accounts`
- Returns active accounts for the business (`id`, `name`, `account_type`, `currency`, `current_balance`, `is_active`).
- Ordered by name ascending, limit 500.

### 4.2 Customers Dropdown
`GET /api/customers?q=`
- Returns customers (`id`, `name`) with optional substring filter on `q`.
- Ordered by name ascending, limit 500.

### 4.3 Vendors Dropdown
`GET /api/vendors?q=`
- Returns vendors (`id`, `name`) with optional substring filter on `q`.
- Ordered by name ascending, limit 500.

### 4.4 Categories Dropdown
`GET /api/categories?kind=transaction|expense`
- Returns sorted list of distinct strings populated for the business.
- Returns `422` if `kind` is invalid.

### 4.5 Data Range Metadata
`GET /api/meta/data-range`
- Returns date range (`min_date`, `max_date`) and total count across transactions, invoices, and expenses for dashboard initialization.
```json
{
  "transactions": { "min_date": "2026-01-01", "max_date": "2026-10-31", "count": 120 },
  "invoices": { "min_date": "2026-02-01", "max_date": "2026-10-25", "count": 45 },
  "expenses": { "min_date": "2026-01-05", "max_date": "2026-10-30", "count": 80 }
}
```

---

## 5. Financial Summary Endpoints (Day 5)

### 5.1 Primary Endpoint: `GET /api/businesses/{business_id}/financial-summary`
Fetch headline financial KPIs, cash balances, receivables aging, top spend categories, and preceding period comparisons backed deterministically by Member 3's Finance Engine.

**Query Parameters:**
| Parameter | Type | Default | Description |
|---|---|---|---|
| `period` | string | `last_30_days` | Preset: `last_30_days`, `this_month`, `last_month`, `this_quarter`, `last_quarter`, `this_fy`, `last_fy`, `ytd`, `custom` |
| `date_from` | Date (`YYYY-MM-DD`) | null | Required when `period=custom` |
| `date_to` | Date (`YYYY-MM-DD`) | null | Required when `period=custom` |
| `as_of` | Date (`YYYY-MM-DD`) | null | Anchor date (default: today in business timezone) |
| `compare_previous`| boolean | `true` | Compute immediately preceding period of equal length |
| `include` | string | null | Comma-separated list to trim payload: `kpis,receivables,expenses,cash,comparison` |

**Multi-Tenant Security:**
Path `business_id` must match the caller's JWT token claim. Mismatches return `403 Forbidden`.

### 5.2 Convenience Alias: `GET /api/financial-summary`
Same response contract and query parameters, automatically scoped to the authenticated caller's `business_id`.

