"""
Engine adapter bridging AgentFi Backend read services with Member 3's deterministic Finance Engine.
Provides a typed Protocol (FinanceSummaryProvider) with both a real engine provider (Mode A)
and a fallback interim SQL provider (Mode C).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Protocol
import uuid

from backend.core.config import settings
from backend.schemas.financial_summary import (
    AccountSummaryItem,
    CashSummary,
    ExpenseCategoryBreakdown,
    ExpensesSummary,
    KpisSummary,
    ReceivablesSummary,
)

logger = logging.getLogger(__name__)


@dataclass
class SummaryInputs:
    """
    In-memory data transfer container holding tenant-scoped records fetched from read_api.
    """
    business: Any
    accounts: List[Any]
    transactions: List[Any]
    invoices: List[Any]
    expenses: List[Any]
    customers: List[Any] = field(default_factory=list)
    vendors: List[Any] = field(default_factory=list)


@dataclass
class EngineSummary:
    """
    Standardized payload produced by an engine provider before final response assembly.
    """
    computed_by: str
    engine_version: Optional[str]
    kpis: KpisSummary
    cash: CashSummary
    receivables: ReceivablesSummary
    expenses: ExpensesSummary
    warnings: List[str] = field(default_factory=list)


class FinanceSummaryProvider(Protocol):
    """
    Protocol defining the contract for financial summary computation providers.
    """
    def get_summary(
        self,
        *,
        business_id: uuid.UUID,
        date_from: date,
        date_to: date,
        as_of: date,
        currency: str,
        data: SummaryInputs,
    ) -> EngineSummary:
        ...


class RealEngineSummaryProvider:
    """
    Mode A Provider: Adapts Member 3's deterministic finance engine (finance_engine).
    Constructs an in-memory FinanceData model and runs pure calculations.
    """

    def get_summary(
        self,
        *,
        business_id: uuid.UUID,
        date_from: date,
        date_to: date,
        as_of: date,
        currency: str,
        data: SummaryInputs,
    ) -> EngineSummary:
        # Lazy import of finance_engine modules to avoid hard startup coupling
        from finance_engine.calculations import (
            CALCULATION_VERSION,
            calculate_expenses,
            calculate_receivables,
            financial_summary,
        )
        from finance_engine.data import FinanceData
        from finance_engine.periods import Period
        from finance_engine.schema.canonical import (
            Account as M3Account,
            AccountType,
            Business as M3Business,
            Counterparty as M3Counterparty,
            CounterpartyType,
            Expense as M3Expense,
            ExpenseCategory,
            Invoice as M3Invoice,
            InvoiceLineItem,
            InvoiceStatus,
            Transaction as M3Transaction,
            TransactionDirection,
        )

        biz_str = str(business_id)
        biz_name = getattr(data.business, "name", "Business")
        fy_month = getattr(data.business, "fiscal_year_start_month", 4) or 4

        # 1. Map Business
        m3_biz = M3Business(
            business_id=biz_str,
            name=biz_name,
            base_currency=currency,
            fiscal_year_start_month=fy_month,
        )

        # 2. Map Accounts & build CashSummary
        m3_accounts = []
        account_summary_items = []
        total_cash_bal = Decimal("0.00")

        for acc in data.accounts:
            acc_id_str = str(acc.id)
            acc_type_val = getattr(acc, "account_type", "bank")
            m3_acc_type = (
                AccountType(acc_type_val)
                if acc_type_val in AccountType._value2member_map_
                else AccountType.bank
            )
            curr_bal = Decimal(str(getattr(acc, "current_balance", Decimal("0.00"))))
            total_cash_bal += curr_bal

            m3_accounts.append(
                M3Account(
                    account_id=acc_id_str,
                    business_id=biz_str,
                    name=acc.name,
                    account_type=m3_acc_type,
                    currency=getattr(acc, "currency", currency) or currency,
                    opening_balance=Decimal("0.00"),
                )
            )
            account_summary_items.append(
                AccountSummaryItem(
                    id=acc.id,
                    name=acc.name,
                    account_type=acc_type_val,
                    current_balance=curr_bal,
                )
            )

        if not m3_accounts:
            # Provide default primary account so unlinked bank transactions can be evaluated by the engine
            default_acc_id = "default-primary-account"
            m3_accounts.append(
                M3Account(
                    account_id=default_acc_id,
                    business_id=biz_str,
                    name="Primary Account",
                    account_type=AccountType.bank,
                    currency=currency,
                    opening_balance=Decimal("0.00"),
                )
            )
        else:
            default_acc_id = m3_accounts[0].account_id

        # 3. Map Counterparties (Customers & Vendors)
        m3_counterparties = []
        for cust in data.customers:
            m3_counterparties.append(
                M3Counterparty(
                    counterparty_id=str(cust.id),
                    business_id=biz_str,
                    name=cust.name,
                    counterparty_type=CounterpartyType.customer,
                    email=getattr(cust, "email", None),
                )
            )
        for vend in data.vendors:
            m3_counterparties.append(
                M3Counterparty(
                    counterparty_id=str(vend.id),
                    business_id=biz_str,
                    name=vend.name,
                    counterparty_type=CounterpartyType.vendor,
                    email=getattr(vend, "email", None),
                )
            )

        # 4. Map Transactions
        m3_transactions = []
        known_acc_ids = {a.account_id for a in m3_accounts}
        for txn in data.transactions:
            direction_str = getattr(txn, "transaction_type", "inflow")
            direction = (
                TransactionDirection.inflow
                if direction_str == "inflow"
                else TransactionDirection.outflow
            )
            t_acc_id = (
                str(txn.account_id)
                if txn.account_id and str(txn.account_id) in known_acc_ids
                else default_acc_id
            )

            m3_transactions.append(
                M3Transaction(
                    transaction_id=str(txn.id),
                    business_id=biz_str,
                    account_id=t_acc_id,
                    transaction_date=txn.transaction_date,
                    amount=Decimal(str(txn.amount)),
                    currency=getattr(txn, "currency", currency) or currency,
                    direction=direction,
                    description=txn.description or "",
                    category=None,
                    counterparty_id=None,
                    invoice_id=None,
                )
            )

        # 5. Map Invoices
        m3_invoices = []
        for inv in data.invoices:
            inv_total = Decimal(str(inv.amount_total))
            inv_paid = Decimal(str(inv.amount_paid))
            inv_paid_clamped = min(inv_paid, inv_total)

            # Determine valid status matching payment amount to satisfy Invoice validators
            if inv_paid_clamped >= inv_total:
                st = InvoiceStatus.paid
                inv_paid_clamped = inv_total
            elif Decimal("0.00") < inv_paid_clamped < inv_total:
                st = InvoiceStatus.partially_paid
            else:
                raw_st = (inv.status or "sent").lower()
                st = (
                    InvoiceStatus(raw_st)
                    if raw_st in (InvoiceStatus.sent.value, InvoiceStatus.draft.value, InvoiceStatus.void.value)
                    else InvoiceStatus.sent
                )

            issue_d = inv.invoice_date
            due_d = inv.due_date if inv.due_date >= issue_d else issue_d
            cust_id_str = str(inv.customer_id) if inv.customer_id else str(uuid.uuid4())

            m3_invoices.append(
                M3Invoice(
                    invoice_id=str(inv.id),
                    business_id=biz_str,
                    customer_id=cust_id_str,
                    invoice_number=inv.invoice_number,
                    issue_date=issue_d,
                    due_date=due_d,
                    currency=getattr(inv, "currency", currency) or currency,
                    line_items=[
                        InvoiceLineItem(
                            description="Invoice Total",
                            quantity=Decimal("1"),
                            unit_price=inv_total,
                            tax_rate=Decimal("0"),
                        )
                    ],
                    amount_paid=inv_paid_clamped,
                    status=st,
                )
            )

        # 6. Map Expenses
        m3_expenses = []
        for exp in data.expenses:
            exp_amt = Decimal(str(exp.amount))
            cat_raw = (exp.category or "other").strip().lower().replace(" ", "_")
            cat_enum = (
                ExpenseCategory(cat_raw)
                if cat_raw in ExpenseCategory._value2member_map_
                else ExpenseCategory.other
            )

            m3_expenses.append(
                M3Expense(
                    expense_id=str(exp.id),
                    business_id=biz_str,
                    vendor_id=str(exp.vendor_id) if exp.vendor_id else None,
                    expense_date=exp.expense_date,
                    amount=exp_amt,
                    currency=getattr(exp, "currency", currency) or currency,
                    category=cat_enum,
                    description=exp.description or "",
                    is_paid=True,
                )
            )

        # 7. Construct in-memory FinanceData and target Period
        fdata = FinanceData(
            business=m3_biz,
            accounts=tuple(m3_accounts),
            counterparties=tuple(m3_counterparties),
            transactions=tuple(m3_transactions),
            invoices=tuple(m3_invoices),
            expenses=tuple(m3_expenses),
        )
        period = Period(label="summary_period", start=date_from, end=date_to)

        # 8. Execute Member 3's pure calculations
        summary_rep = financial_summary(fdata, period)
        exp_rep = calculate_expenses(fdata, period)
        ar_rep = calculate_receivables(fdata, as_of=as_of, period=period)

        # Extract top categories (max 5)
        top_cats: List[ExpenseCategoryBreakdown] = []
        cat_breakdowns = exp_rep.incurred_by_category or exp_rep.paid_by_category or []
        for b in cat_breakdowns[:5]:
            top_cats.append(
                ExpenseCategoryBreakdown(
                    category=b.label,
                    amount=b.amount,
                    share_pct=(b.share * Decimal("100.00")).quantize(Decimal("0.01")),
                )
            )

        # Compute overdue count
        overdue_cnt = sum(b.count for b in ar_rep.aging if b.key != "current")

        warnings = list(dict.fromkeys(summary_rep.warnings + exp_rep.warnings + ar_rep.warnings))

        kpis = KpisSummary(
            total_inflow=summary_rep.inflows,
            total_outflow=summary_rep.outflows,
            net_cash_flow=summary_rep.net_cash_flow,
            total_expenses=exp_rep.incurred_total or summary_rep.expenses_paid,
            transaction_count=len(data.transactions),
        )
        cash = CashSummary(
            total_cash_balance=total_cash_bal,
            accounts=account_summary_items,
        )
        receivables = ReceivablesSummary(
            total_outstanding=ar_rep.total_outstanding,
            overdue_amount=ar_rep.overdue_amount,
            overdue_count=overdue_cnt,
            open_invoice_count=ar_rep.invoice_count,
        )
        expenses = ExpensesSummary(top_categories=top_cats)

        return EngineSummary(
            computed_by="finance_engine",
            engine_version=CALCULATION_VERSION,
            kpis=kpis,
            cash=cash,
            receivables=receivables,
            expenses=expenses,
            warnings=warnings,
        )


class InterimSummaryProvider:
    """
    Mode C Provider (Fallback): Computes basic headline aggregates directly.
    Clearly tagged with computed_by='interim_backend_sql' and warning 'interim_provider_in_use'.
    """

    def get_summary(
        self,
        *,
        business_id: uuid.UUID,
        date_from: date,
        date_to: date,
        as_of: date,
        currency: str,
        data: SummaryInputs,
    ) -> EngineSummary:
        total_inflow = Decimal("0.00")
        total_outflow = Decimal("0.00")
        for txn in data.transactions:
            amt = Decimal(str(txn.amount))
            if getattr(txn, "transaction_type", "inflow") == "inflow":
                total_inflow += amt
            else:
                total_outflow += amt

        net_cash_flow = total_inflow - total_outflow

        total_expenses = Decimal("0.00")
        cat_totals: Dict[str, Decimal] = {}
        for exp in data.expenses:
            amt = Decimal(str(exp.amount))
            total_expenses += amt
            cat = exp.category or "other"
            cat_totals[cat] = cat_totals.get(cat, Decimal("0.00")) + amt

        top_cats = []
        if total_expenses > Decimal("0.00"):
            sorted_cats = sorted(cat_totals.items(), key=lambda x: -x[1])[:5]
            for cat, amt in sorted_cats:
                share_pct = ((amt / total_expenses) * Decimal("100.00")).quantize(Decimal("0.01"))
                top_cats.append(
                    ExpenseCategoryBreakdown(
                        category=cat.title(),
                        amount=amt,
                        share_pct=share_pct,
                    )
                )

        total_cash_bal = Decimal("0.00")
        acc_items = []
        for acc in data.accounts:
            c_bal = Decimal(str(getattr(acc, "current_balance", Decimal("0.00"))))
            total_cash_bal += c_bal
            acc_items.append(
                AccountSummaryItem(
                    id=acc.id,
                    name=acc.name,
                    account_type=acc.account_type,
                    current_balance=c_bal,
                )
            )

        total_outstanding = Decimal("0.00")
        overdue_amt = Decimal("0.00")
        overdue_cnt = 0
        open_cnt = 0

        for inv in data.invoices:
            if getattr(inv, "status", "") in ("paid", "void"):
                continue
            balance = Decimal(str(inv.amount_total)) - Decimal(str(inv.amount_paid))
            if balance > Decimal("0.00"):
                total_outstanding += balance
                open_cnt += 1
                if inv.due_date and inv.due_date < as_of:
                    overdue_amt += balance
                    overdue_cnt += 1

        kpis = KpisSummary(
            total_inflow=total_inflow,
            total_outflow=total_outflow,
            net_cash_flow=net_cash_flow,
            total_expenses=total_expenses,
            transaction_count=len(data.transactions),
        )
        cash = CashSummary(
            total_cash_balance=total_cash_bal,
            accounts=acc_items,
        )
        receivables = ReceivablesSummary(
            total_outstanding=total_outstanding,
            overdue_amount=overdue_amt,
            overdue_count=overdue_cnt,
            open_invoice_count=open_cnt,
        )
        expenses = ExpensesSummary(top_categories=top_cats)

        return EngineSummary(
            computed_by="interim_backend_sql",
            engine_version=None,
            kpis=kpis,
            cash=cash,
            receivables=receivables,
            expenses=expenses,
            warnings=["interim_provider_in_use"],
        )


def get_summary_provider(mode: Optional[str] = None) -> FinanceSummaryProvider:
    """
    Factory creating the appropriate FinanceSummaryProvider based on configuration.
    """
    selected_mode = (mode or getattr(settings, "FINANCE_ENGINE_MODE", "auto") or "auto").strip().lower()

    if selected_mode == "interim":
        return InterimSummaryProvider()

    if selected_mode in ("auto", "engine"):
        try:
            return RealEngineSummaryProvider()
        except Exception as exc:
            if selected_mode == "engine":
                raise exc
            logger.warning(
                f"RealEngineSummaryProvider could not be loaded; falling back to InterimSummaryProvider: {exc}"
            )
            return InterimSummaryProvider()

    return RealEngineSummaryProvider()
