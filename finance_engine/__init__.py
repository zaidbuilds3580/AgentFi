"""
Deterministic finance engine for AgentFi.

Holds the canonical data model, the ETL pipeline and the finance calculations.
All financial calculations happen here, never inside LLM agents.

    finance_engine.schema        canonical models + validation rules
    finance_engine.etl           raw files -> clean, validated, de-duplicated data
    finance_engine.periods       period labels -> dates
    finance_engine.calculations  revenue / expenses / net cash flow / receivables
    finance_engine.tools         agent-facing wrappers (see docs/finance_engine.md)
"""
