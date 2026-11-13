"""
run_chat_query: the ONE function the backend calls for POST /api/chat.

How Member 2 uses it (in simple words)
--------------------------------------
    from graph.workflow import run_chat_query

    @router.post("/api/chat")
    def chat(body: ChatRequest, user = Depends(get_current_user)):
        return run_chat_query(
            business_id=user.business_id,        # from the LOGIN, never from the request body
            message=body.message,
            conversation_history=body.history,   # optional
        )

- business_id MUST come from the logged-in user, not from anything the user types.
- conversation_history is a list like [{"role": "user", "content": "..."},
  {"role": "assistant", "content": "..."}]. Only the last 10 turns are used.
  History can never change the business_id.
- It always returns a dict with exactly 7 keys: summary, evidence, caveats,
  recommendations, missing_parts, report_type, currency.
- It NEVER raises an error. Problems become a safe, plain message.
- It can take a few seconds (the AI step), so use a normal "def" endpoint
  (not "async def") or run it in a thread.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional, Tuple

import graph.workflow.full_graph as fg

logger = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 10
MAX_TEXT_CHARS = 2000
EMPTY_MESSAGE_TEXT = "Please type a question about your finances and I will help."
SEVEN_KEYS = {
    "summary", "evidence", "caveats", "recommendations",
    "missing_parts", "report_type", "currency",
}
_ROLE_MAP = {"user": "human", "human": "human", "assistant": "ai", "ai": "ai"}


def _clean_history(history: Any) -> List[Tuple[str, str]]:
    """Keep only valid (role, text) pairs, at most the last 10. Ignore everything else."""
    if not isinstance(history, (list, tuple)):
        return []
    cleaned: List[Tuple[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = _ROLE_MAP.get(str(item.get("role", "")).strip().lower())
        content = item.get("content")
        if role is None or not isinstance(content, str):
            continue
        content = content.strip()[:MAX_TEXT_CHARS]
        if content:
            cleaned.append((role, content))
    return cleaned[-MAX_HISTORY_TURNS:]


def _is_valid_response(resp: Any) -> bool:
    return (
        isinstance(resp, dict)
        and set(resp.keys()) == SEVEN_KEYS
        and isinstance(resp.get("summary"), str)
    )


def _safe_failure(report_type: str = "daily") -> Dict[str, Any]:
    return fg._seven_keys(fg.SAFE_FAILURE_MESSAGE, report_type=report_type)


def run_chat_query(
    business_id: str,
    message: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Run one chat question through the full agent graph. Never raises."""
    try:
        bid = business_id.strip() if isinstance(business_id, str) else ""
        text = message.strip() if isinstance(message, str) else ""

        if not bid:
            return _safe_failure()
        if not text:
            return fg._seven_keys(EMPTY_MESSAGE_TEXT)

        text = text[:MAX_TEXT_CHARS]
        report_type = fg._detect_report_type(text)
        messages = _clean_history(conversation_history) + [("human", text)]

        state = {
            "business_id": bid,  # only ever taken from the function argument
            "request_id": uuid.uuid4().hex,
            "messages": messages,
        }
        result = fg.full_graph.invoke(state)
        final = result.get("final_response") if isinstance(result, dict) else None
        if _is_valid_response(final):
            return dict(final)
        return _safe_failure(report_type)
    except Exception as exc:
        logger.warning("run_chat_query failed: %s", type(exc).__name__)
        return _safe_failure()
