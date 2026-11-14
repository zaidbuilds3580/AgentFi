# Programmatic Data Access for Finance Engine & Agents

This document explains how **Member 3 (Finance Engine)** and **Member 4 (LangGraph Agents)** read financial records directly in Python without going through HTTP requests.

---

## 1. Overview & Location

The internal read layer is located in:
`backend.services.read_api`

It provides typed, performant, tenant-scoped query functions that execute directly against PostgreSQL via SQLAlchemy 2.0 sessions and return validated Pydantic schemas.

### Key Characteristics:
- **Tenant Isolation**: Every function requires a `business_id: uuid.UUID` parameter. Queries are strictly scoped to the tenant.
- **Safety Upper Bound**: Enforces `MAX_INTERNAL_ROWS = 100000` to prevent unbounded memory consumption or OOM. If more rows match without pagination, a `ValueError` is raised asking the caller to narrow the date window.
- **Read-Only**: Queries are purely reads and do not mutate database state.
- **Pydantic DTOs**: Results are parsed into standard canonical schemas (`TransactionRead`, `InvoiceRead`, `ExpenseRead`). All monetary fields are Python `Decimal`.

---

## 2. API Reference & Signatures

### 2.1 `fetch_transactions`

```python
from datetime import date
from typing import List, Optional
import uuid
from sqlalchemy.orm import Session
from backend.schemas.canonical import TransactionRead

def fetch_transactions(
    db: Session,
    business_id: uuid.UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    account_id: Optional[uuid.UUID] = None,
    categories: Optional[List[str]] = None,
    transaction_type: Optional[str] = None,  # 'inflow' or 'outflow'
    limit: Optional[int] = None,
) -> List[TransactionRead]:
    ...
```

**Ordering**: Default ordering is `transaction_date ASC, id ASC`.

---

### 2.2 `fetch_invoices`

```python
from datetime import date
from typing import List, Optional
import uuid
from sqlalchemy.orm import Session
from backend.schemas.canonical import InvoiceRead

def fetch_invoices(
    db: Session,
    business_id: uuid.UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    customer_id: Optional[uuid.UUID] = None,
    status: Optional[str] = None,  # 'draft', 'sent', 'paid', 'overdue', 'void'
    limit: Optional[int] = None,
) -> List[InvoiceRead]:
    ...
```

**Ordering**: Default ordering is `due_date ASC, id ASC`.

---

### 2.3 `fetch_expenses`

```python
from datetime import date
from typing import List, Optional
import uuid
from sqlalchemy.orm import Session
from backend.schemas.canonical import ExpenseRead

def fetch_expenses(
    db: Session,
    business_id: uuid.UUID,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    vendor_id: Optional[uuid.UUID] = None,
    limit: Optional[int] = None,
) -> List[ExpenseRead]:
    ...
```

**Ordering**: Default ordering is `expense_date ASC, id ASC`.

---

## 3. Usage Examples

### Example 1: Cash Flow Calculation (Member 3)

```python
from datetime import date
from decimal import Decimal
import uuid
from backend.database.session import get_db
from backend.services.read_api import fetch_transactions

def compute_monthly_net_cash_flow(business_id: uuid.UUID, year: int, month: int) -> Decimal:
    start = date(year, month, 1)
    # End of month helper
    end = date(year, month, 28) # e.g. calculated end of month

    with next(get_db()) as session:
        txns = fetch_transactions(
            db=session,
            business_id=business_id,
            start_date=start,
            end_date=end,
        )
        
        inflow = sum((t.amount for t in txns if t.transaction_type == "inflow"), Decimal("0.00"))
        outflow = sum((t.amount for t in txns if t.transaction_type == "outflow"), Decimal("0.00"))
        return inflow - outflow
```

### Example 2: Agent Tool for Outstanding Receivables (Member 4)

```python
import uuid
from decimal import Decimal
from backend.database.session import get_db
from backend.services.read_api import fetch_invoices

def get_outstanding_receivables(business_id: str) -> dict:
    biz_uuid = uuid.UUID(business_id)
    with next(get_db()) as session:
        invoices = fetch_invoices(db=session, business_id=biz_uuid)
        unpaid = [
            inv for inv in invoices 
            if inv.status not in ("paid", "void") and inv.amount_paid < inv.amount_total
        ]
        total_outstanding = sum((inv.amount_total - inv.amount_paid for inv in unpaid), Decimal("0.00"))
        return {
            "unpaid_invoice_count": len(unpaid),
            "total_outstanding_amount": str(total_outstanding),
        }
```

---

## 4. Financial Summary Engine Adapter & Provider (Day 5)

Located in:
`backend.services.finance_engine_adapter` and `backend.services.financial_summary_service`

### 4.1 Architecture
To preserve clean separation of concerns and avoid financial formula duplication:
- **`read_api`** fetches tenant-isolated records from Supabase PostgreSQL.
- **`SummaryInputs`** bundles the business, accounts, transactions, invoices, expenses, customers, and vendors in memory.
- **`FinanceSummaryProvider` (Protocol)** defines the contract `get_summary(...) -> EngineSummary`.
  - **`RealEngineSummaryProvider` (Mode A)**: Converts inputs into `finance_engine.data.FinanceData` and runs Member 3's pure calculations (`financial_summary`, `calculate_expenses`, `calculate_receivables`).
  - **`InterimSummaryProvider` (Mode C)**: Fallback provider calculating basic aggregates directly, marked with `computed_by = "interim_backend_sql"`.
- Configuration: `FINANCE_ENGINE_MODE=auto|engine|interim` (default `auto`).

### 4.2 Programmatic Usage for Member 4 Agents
Agents in `agents/` can call `get_financial_summary_service` directly without making HTTP requests:
```python
from backend.database.session import get_db
from backend.services.financial_summary_service import get_financial_summary_service

with next(get_db()) as session:
    summary = get_financial_summary_service(
        db=session,
        business_id=biz_uuid,
        period="last_30_days",
    )
    print("Inflows:", summary.kpis.total_inflow)
    print("Net Cash Flow:", summary.kpis.net_cash_flow)
    print("Cash Balance:", summary.cash.total_cash_balance)
```

