"""AgentCore Platform v1.0 — deterministic safety-gate classification for MFG-C2-080.

Pure functions (no framework imports, unit-testable standalone; no LLM). The MANDATORY
safety gate keys on the structured equipment_category TOKEN — never a substring of the
free-text equipment_name, which the framework @final S-2 baseline can mask to "[MASKED]"
(docs/02 ADR-017). In V1 every FMEA is review-required (auto-approval is blocked for every
category — no caller-declared category grants auto-approval); a safety-critical category
additionally carries the explicit mandatory safety-review warning.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


_REVIEW_REQUIRED_WARNING = (
    "This FMEA is review-required: it MUST be reviewed and approved by a qualified engineer "
    "before use; auto-approval is blocked."
)
_SAFETY_CRITICAL_WARNING = (
    "Equipment category is safety-critical: this FMEA MUST be reviewed and approved by a "
    "qualified safety engineer before use; auto-approval is blocked."
)


def is_safety_critical(equipment_category: Any, critical_categories: Iterable[Any]) -> bool:
    """Exact token membership — NEVER a substring of the maskable free-text equipment_name."""
    return str(equipment_category).strip() in {str(c).strip() for c in critical_categories}


def build_safety_gate(equipment_category: Any, critical_categories: Iterable[Any]) -> dict[str, Any]:
    """Return ``{safety_critical, warning, blocked}``. In V1 ``blocked`` is True for EVERY
    category (every FMEA is review-required — no caller-declared category grants auto-approval);
    a safety-critical category additionally sets ``safety_critical`` and the explicit warning."""
    if is_safety_critical(equipment_category, critical_categories):
        return {"safety_critical": True, "warning": _SAFETY_CRITICAL_WARNING, "blocked": True}
    return {"safety_critical": False, "warning": _REVIEW_REQUIRED_WARNING, "blocked": True}
