"""
Errors raised by the finance engine tools.

Every tool error is a FinanceToolError, so callers can catch one type. Each class also
subclasses the matching built-in (LookupError / ValueError), so code written against the
earlier tools (`except ValueError`, `except LookupError`) keeps working.

    FinanceToolError
    ├── BusinessNotFoundError   (LookupError)  unknown business_id, or no data for it
    └── InvalidInputError       (ValueError)   bad period, date, window or horizon
        └── PeriodError                          period label that can't be understood
"""


class FinanceToolError(Exception):
    """Base class for all finance tool errors. `code` is a stable machine-readable identifier."""
    code = "finance_tool_error"


class BusinessNotFoundError(FinanceToolError, LookupError):
    """No data (processed, raw or sample) exists for the requested business."""
    code = "business_not_found"


class InvalidInputError(FinanceToolError, ValueError):
    """A tool argument is missing, malformed or out of range."""
    code = "invalid_input"


class PeriodError(InvalidInputError):
    """A period label could not be understood."""
    code = "invalid_period"
