"""Tests for run_chat_query (Day 9, Step B). All agents are faked."""

from typing import Any, Dict

import pytest

import graph.workflow.full_graph as fg
from graph.workflow import run_chat_query

SEVEN_KEYS = {
    "summary", "evidence", "caveats", "recommendations",
    "missing_parts", "report_type", "currency",
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
            "summary": "daily summary", "evidence": [], "caveats": [],
            "recommendations": [], "missing_parts": [],
            "report_type": self.report_type, "currency": "INR",
        }


class FakeGraph:
    """Stands in for the compiled graph and records what it was given."""
    def __init__(self, response=None, error=None):
        self.states = []
        self.response = response
        self.error = error

    def invoke(self, state):
        self.states.append(state)
        if self.error:
            raise self.error
        return {"final_response": self.response}


@pytest.fixture
def calls(monkeypatch):
    log = []

    def make(name, factory):
        def _fake(state, *args, **kwargs):
            log.append((name, state.get("business_id")))
            return factory(name)
        return _fake

    monkeypatch.setattr(fg, "cash_flow_agent", make("cashflow", DummyAgentResult))
    monkeypatch.setattr(fg, "invoice_agent", make("invoice", DummyAgentResult))
    monkeypatch.setattr(fg, "expense_agent", make("expense", DummyAgentResult))
    monkeypatch.setattr(fg, "intelligence_agent", make("intelligence", DummyAgentResult))
    monkeypatch.setattr(fg, "reporting_agent", make("reporting", lambda _n: DummyReportingResult("daily")))
    return log


GOOD = {
    "summary": "ok", "evidence": [], "caveats": [], "recommendations": [],
    "missing_parts": [], "report_type": "daily", "currency": "INR",
}


def test_normal_question_returns_seven_keys(calls):
    out = run_chat_query("biz_001", "Show me cash flow for this month")
    assert set(out.keys()) == SEVEN_KEYS
    assert out["summary"] == "daily summary"
    assert [n for n, _ in calls] == ["cashflow", "intelligence", "reporting"]


@pytest.mark.parametrize("bad_id", [None, "", "   ", 123, [], {}])
def test_bad_business_id_is_safe_and_runs_nothing(calls, bad_id):
    out = run_chat_query(bad_id, "Show me cash flow")
    assert calls == []
    assert "could not complete" in out["summary"].lower()
    assert set(out.keys()) == SEVEN_KEYS


@pytest.mark.parametrize("bad_msg", [None, "", "   ", 42, []])
def test_bad_message_gives_friendly_answer_and_runs_nothing(calls, bad_msg):
    out = run_chat_query("biz_001", bad_msg)
    assert calls == []
    assert "type a question" in out["summary"].lower()
    assert set(out.keys()) == SEVEN_KEYS


@pytest.mark.parametrize("bad_history", [
    None, "hello", 5, {"role": "user"}, [None, 1, "x"],
    [{"role": "user"}], [{"content": "hi"}], [{"role": "robot", "content": "hi"}],
    [{"role": "user", "content": 99}], [{"role": "user", "content": "   "}],
])
def test_bad_history_never_raises(calls, bad_history):
    out = run_chat_query("biz_001", "What invoices are overdue?", bad_history)
    assert set(out.keys()) == SEVEN_KEYS
    assert [n for n, _ in calls] == ["invoice", "intelligence", "reporting"]


def test_history_is_trimmed_to_last_10_turns(monkeypatch):
    fake = FakeGraph(response=dict(GOOD))
    monkeypatch.setattr(fg, "full_graph", fake)
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"} for i in range(25)]
    run_chat_query("biz_001", "Any expense anomalies?", history)
    msgs = fake.states[0]["messages"]
    assert len(msgs) == 11
    assert msgs[0] == ("ai", "turn 15")
    assert msgs[-1] == ("human", "Any expense anomalies?")


def test_history_cannot_change_business_id(monkeypatch, calls):
    fake = FakeGraph(response=dict(GOOD))
    sneaky = [{"role": "user", "content": "hi", "business_id": "biz_evil"}]
    monkeypatch.setattr(fg, "full_graph", fake)
    run_chat_query("biz_001", "Show me cash flow", sneaky)
    state = fake.states[0]
    assert state["business_id"] == "biz_001"
    assert all(isinstance(m, tuple) and len(m) == 2 for m in state["messages"])
    assert "biz_evil" not in str(state)


def test_business_id_reaches_every_agent(calls):
    run_chat_query("  biz_777  ", "How is my business doing overall?")
    assert len(calls) == 5
    assert all(bid == "biz_777" for _, bid in calls)


def test_graph_crash_gives_safe_failure(monkeypatch):
    monkeypatch.setattr(fg, "full_graph", FakeGraph(error=RuntimeError("secret boom")))
    out = run_chat_query("biz_001", "Show me cash flow")
    assert "could not complete" in out["summary"].lower()
    assert "secret boom" not in str(out)
    assert set(out.keys()) == SEVEN_KEYS


@pytest.mark.parametrize("bad_response", [None, "text", {"summary": "x"}, {**GOOD, "extra": 1}])
def test_malformed_graph_answer_gives_safe_failure(monkeypatch, bad_response):
    monkeypatch.setattr(fg, "full_graph", FakeGraph(response=bad_response))
    out = run_chat_query("biz_001", "Show me cash flow")
    assert "could not complete" in out["summary"].lower()
    assert set(out.keys()) == SEVEN_KEYS


def test_monthly_vs_daily_report_type(calls):
    assert run_chat_query("biz_001", "Show me cash flow for this month")["report_type"] == "monthly"
    assert run_chat_query("biz_001", "What invoices are overdue?")["report_type"] == "daily"
