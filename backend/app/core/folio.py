"""
Folio normalization — canonical form is what HCPA returns: "121923.0000".

Different systems represent the same parcel differently:
  HCPA layer:    "121923.0000"   (canonical)
  User input:    "121923", "121923-0000", "121923.0"
  Tax display:   "121923-0000"
  Old legacy:    "000008-0000"   → "8.0000"

Functions here accept any of these and normalize consistently.
"""
from __future__ import annotations

import re


def normalize_folio(raw: str) -> str:
    """
    Normalize any folio representation to HCPA canonical form: "NNNNN.0000".

    Examples:
      "121923"       → "121923.0000"
      "121923-0000"  → "121923.0000"
      "8.0000"       → "8.0000"   (already canonical)
      "000008-0000"  → "8.0000"
      "000121923"    → "121923.0000"
    """
    s = raw.strip()
    # Already has decimal point — trust it
    if "." in s:
        parts = s.split(".")
        integer_part = parts[0].lstrip("0") or "0"
        decimal_part = parts[1].ljust(4, "0")[:4]
        return f"{integer_part}.{decimal_part}"
    # Hyphenated: "121923-0000" or "000008-0000"
    if "-" in s:
        parts = s.split("-", 1)
        integer_part = parts[0].lstrip("0") or "0"
        decimal_part = parts[1].ljust(4, "0")[:4]
        return f"{integer_part}.{decimal_part}"
    # Pure numeric string
    clean = re.sub(r"[^0-9]", "", s)
    if not clean:
        return raw
    return f"{int(clean)}.0000"


def format_folio_display(raw: str) -> str:
    """Human-readable display format: "121923-0000"."""
    canon = normalize_folio(raw)
    if "." in canon:
        parts = canon.split(".")
        return f"{parts[0]}-{parts[1]}"
    return canon


def folios_match(a: str, b: str) -> bool:
    """True if two folio strings refer to the same parcel."""
    try:
        return normalize_folio(a) == normalize_folio(b)
    except Exception:
        return False
