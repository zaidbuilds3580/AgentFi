"""Tests for the full LangGraph workflow (Day 9). All agents are faked."""

from typing import Any, Dict

import pytest

import graph.workflow.full_graph as fg

SEVEN_KEYS = {
    "summary", "evidence", "caveats", "recommendations",
    "missing_parts", "report_type", "currency",
}
BROAD = "How is my business doing overall?"
CASH = "Show me cash flow for this month"
INVOICE = "What invoices are overdue?"
EXPENSE = "Any expense anomalies?"
UNCLEAR = "Blah blah unknown"


def build_state(question: str, business_id: str = "biz_001") -> Dict[str, Any]:
    return {
        "business_id": business_id,
        "request_id": "req-1",
        "user_id": "user-1",
        "messages": [("human", question)],
    }


class DummyAgentResult:
    def __init__(self, name: str):
        self.agent_name = name
        self.summary = f"{name} summary"
        self.metrics = {}
        self.findings = []
        self.recommendations = []
        self.caveats = []
        self.requires_approval = False


class DummyReportingResult:
    def __init__(self, report_type: str = "daily"):
        self.agent_name = "reporting"
        self.report_type = report_type

    def to_chat_response(self) -> Dict[str, Any]:
        return {
            "summary": "daily summary",
            "evidence": [],
            "caveats": [],
            "recommendations": [],
            "missing_parts": [],
            "report_type": self.report_type,
            "currency": "INR",
        }


@pytest.fixture(autouse=True)
def calls(monkeypatch):
    """Fake all five agents. Returns the list of (agent, business_id) calls."""
    log = []

    def make(name, factory):
        def _fake(state, *args, **kwargs):
            bid = state.get("business_id") if isinstance(state, dict) else None
            log.append((name, bid))
            return factory(name)
        return _fake

    monkeypatch.setattr(fg, "cash_flow_agent", make("cashflow", DummyAgentResult))
    monkeypatch.setattr(fg, "invoice_agent", make("invoice", DummyAgentResult))
    monkeypatch.setattr(fg, "expense_agent", make("expense", DummyAgentResult))
    monkeypatch.setattr(fg, "intelligence_agent", make("intelligence", DummyAgentResult))
    monkeypatch.setattr(fg, "reporting_agent", make("reporting", lambda _n: DummyReportingResult("daily")))
    return log


def run(question: str, business_id: str = "biz_001"):
    result = fg.full_graph.invoke(build_state(question, business_id))
    return result["final_response"], result


def names(log):
    return [n for n, _ in log]


def boom(*args, **kwargs):
    raise RuntimeError("boom")


# --- Routing ---------------------------------------------------------------

def test_cashflow_only(calls):
    final, _ = run(CASH)
    assert names(calls) == ["cashflow", "intelligence", "reporting"]
    assert final["summary"] == "daily summary"


def test_invoice_only(calls):
    run(INVOICE)
    assert names(calls) == ["invoice", "intelligence", "reporting"]


def test_expense_only(calls):
    run(EXPENSE)
    assert names(calls) == ["expense", "intelligence", "reporting"]


def test_broad_question_runs_all_in_order(calls):
    final, _ = run(BROAD)
    assert names(calls) == ["cashflow", "invoice", "expense", "intelligence", "reporting"]
    assert final["summary"] == "daily summary"


def test_unclear_question_runs_no_agents(calls):
    final, _ = run(UNCLEAR)
    assert calls == []
    assert "rephrase" in final["summary"].lower()
    assert set(final.keys()) == SEVEN_KEYS


# --- Failure paths ---------------------------------------------------------

def test_one_specialist_fails_gives_partial_answer(calls, monkeypatch):
    monkeypatch.setattr(fg, "cash_flow_agent", boom)
    final, _ = run(BROAD)
    assert names(calls) == ["invoice", "expense", "intelligence", "reporting"]
    assert "cashflow" in final["missing_parts"]
    assert final["caveats"]
    assert set(final.keys()) == SEVEN_KEYS


def test_all_specialists_fail_gives_safe_message(calls, monkeypatch):
    monkeypatch.setattr(fg, "cash_flow_agent", boom)
    monkeypatch.setattr(fg, "invoice_agent", boom)
    monkeypatch.setattr(fg, "expense_agent", boom)
    final, _ = run(BROAD)
    assert calls == []
    assert "could not complete" in final["summary"].lower()
    assert final["evidence"] == [] and final["recommendations"] == []
    assert "boom" not in str(final)
    assert set(final.keys()) == SEVEN_KEYS


def test_only_specialist_fails_gives_safe_message(calls, monkeypatch):
    monkeypatch.setattr(fg, "cash_flow_agent", boom)
    final, _ = run(CASH)
    assert calls == []
    assert "could not complete" in final["summary"].lower()


def test_intelligence_fails_still_reports(calls, monkeypatch):
    monkeypatch.setattr(fg, "intelligence_agent", boom)
    final, _ = run(BROAD)
    assert names(calls)[-1] == "reporting"
    assert "intelligence" in final["missing_parts"]
    assert set(final.keys()) == SEVEN_KEYS


def test_reporting_fails_still_returns_specialist_results(calls, monkeypatch):
    monkeypatch.setattr(fg, "reporting_agent", boom)
    final, _ = run(BROAD)
    assert final["summary"]
    assert "report" in final["missing_parts"]
    assert set(final.keys()) == SEVEN_KEYS


def test_empty_business_id_is_safe(calls):
    final, _ = run(CASH, business_id="")
    assert calls == []
    assert "could not complete" in final["summary"].lower()
    assert set(final.keys()) == SEVEN_KEYS


# --- Output shape, business_id, report type --------------------------------

def test_final_answer_always_has_seven_keys(calls):
    for q in (CASH, INVOICE, EXPENSE, BROAD, UNCLEAR):
        final, _ = run(q)
        assert set(final.keys()) == SEVEN_KEYS


def test_business_id_reaches_every_agent(calls):
    run(BROAD, business_id="biz_777")
    assert len(calls) == 5
    assert all(bid == "biz_777" for _, bid in calls)


def test_report_type_monthly_and_daily(calls):
    monthly, _ = run(CASH)       # message says "this month"
    daily, _ = run(INVOICE)      # no month in the message
    assert monthly["report_type"] == "monthly"
    assert daily["report_type"] == "daily"
