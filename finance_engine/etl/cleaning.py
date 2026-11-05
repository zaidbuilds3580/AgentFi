"""
Cleaning & normalisation helpers (ETL stage 2).

Raw exports from banks, Excel and accounting tools carry invisible junk that breaks
matching and de-duplication: non-breaking spaces, zero-width characters, smart quotes,
full-width digits, inconsistent casing and legal suffixes on company names.

- clean_text()       : applied to EVERY cell when a CSV is read (see mappers._read_csv)
- party_key()        : normalised company/person name used to build counterparty IDs,
                       so 'M/s Orange Realty L.L.P.' and 'ORANGE REALTY LLP' are one party
- description_key()  : normalised narration used for duplicate detection
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

# Characters Excel / bank portals leave behind that are invisible but not whitespace to Python
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)
_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", "−": "-"})
_WS = re.compile(r"\s+")


def clean_text(value: Optional[str]) -> str:
    """
    NFKC-normalise (full-width digits -> ASCII, NBSP -> space), drop zero-width chars,
    straighten smart quotes/dashes, collapse whitespace and strip.

    >>> clean_text("  1,000.00​ ")
    '1,000.00'
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.translate(_INVISIBLE).translate(_SMART)
    # Excel 'protect as text' prefix and stray control characters
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C" or ch in "\t\n")
    text = text.lstrip("'") if text.startswith("'") and text[1:2].isdigit() else text
    return _WS.sub(" ", text).strip()


# Legal-entity words that don't distinguish one business from another.
# Order: longest first so 'private limited' is removed before 'limited'.
_LEGAL_SUFFIXES = (
    "private limited", "pvt ltd", "pvt", "private", "limited", "ltd", "llp", "l l p",
    "inc", "incorporated", "corporation", "corp", "company", "co", "opc", "plc", "gmbh", "ab", "llc",
)
_PREFIXES = ("m/s", "ms", "messrs", "the")
_SUFFIX_RE = re.compile(r"(?:\s+(?:" + "|".join(re.escape(s) for s in _LEGAL_SUFFIXES) + r"))+$")


def party_key(name: Optional[str]) -> str:
    """
    Normalised key for a customer/vendor name.

    >>> party_key("M/s. Orange Realty L.L.P.")
    'orange realty'
    >>> party_key("BrightPath Learning Pvt. Ltd.")
    'brightpath learning'
    """
    text = clean_text(name).lower().replace("&", " and ")
    text = re.sub(r"[.,()\"']", " ", text)
    text = _WS.sub(" ", text).strip()
    for p in _PREFIXES:
        if text.startswith(p + " ") or text.startswith(p + "/"):
            text = text[len(p):].lstrip(" /")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = _WS.sub(" ", text).strip()
    stripped = _SUFFIX_RE.sub("", text).strip()
    return stripped or text  # never reduce a name to nothing ('The Company Ltd')


def display_name(*candidates: Optional[str]) -> str:
    """
    Pick the best human-readable spelling among several for the same party:
    prefer mixed case over ALL CAPS, then the longest (most complete) spelling.
    """
    names = [_strip_honorific(clean_text(c)) for c in candidates if c and clean_text(c)]
    if not names:
        return ""
    return max(names, key=lambda n: (not n.isupper(), len(n)))


def _strip_honorific(name: str) -> str:
    """'M/s. Orange Realty LLP' -> 'Orange Realty LLP' (M/s = Messrs, common on Indian bills)."""
    return re.sub(r"^(?:m/s\.?|messrs\.?)\s+", "", name, flags=re.IGNORECASE)


def description_key(text: Optional[str]) -> str:
    """Narration key for duplicate detection: case/space/punctuation-insensitive."""
    return re.sub(r"[^a-z0-9]", "", clean_text(text).lower())
