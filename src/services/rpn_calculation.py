"""AgentCore Platform v1.0 — deterministic RPN calculation for MFG-C2-080.

Pure functions (no framework imports, unit-testable standalone; no LLM). RPN = S × O × D
from the SOD ratings, with a high-priority flag when RPN ≥ threshold. S/O/D are validated
against the rating-scale bound (a rating out of range is a data fault → fail closed).
"""

from __future__ import annotations

from typing import Any

from src.services.domain_errors import DomainComputeError

# rating_scale → inclusive max rating value (min is 1). Single source of truth for the
# recognized scales; an unrecognized scale is a fault, never silently coerced to a default.
_SCALE_MAX = {"iatf_16949": 10, "iso_9001_simplified": 5}
VALID_RATING_SCALES = tuple(_SCALE_MAX)


def _rating(item: dict[str, Any], key: str, scale_max: int, idx: int) -> int:
    val = item.get(key)
    if isinstance(val, bool) or not isinstance(val, int):
        raise DomainComputeError(f"sod_ratings[{idx}].{key} must be an integer 1..{scale_max}")
    if val < 1 or val > scale_max:
        raise DomainComputeError(f"sod_ratings[{idx}].{key} = {val} is out of the 1..{scale_max} scale range")
    return val


def calculate(sod_ratings: Any, *, rating_scale: str = "iatf_16949", high_priority_threshold: int = 100) -> list[Any]:
    """Return ``rpn_results``: one ``{severity, occurrence, detection, rpn, high_priority,
    source_failure_mode?}`` per SOD rating. Raises ``DomainComputeError`` on a missing / out-of-range
    rating or a malformed input."""
    if not isinstance(sod_ratings, list) or not sod_ratings:
        raise DomainComputeError("sod_ratings must be a non-empty list")
    if rating_scale not in _SCALE_MAX:
        # Fail closed: an unrecognized scale would silently pick a max and let ratings that are
        # out of the intended range through, defeating the out-of-range guarantee below.
        raise DomainComputeError(f"rating_scale '{rating_scale}' is not one of {VALID_RATING_SCALES}")
    scale_max = _SCALE_MAX[rating_scale]
    out: list[dict[str, Any]] = []
    for idx, item in enumerate(sod_ratings):
        if not isinstance(item, dict):
            raise DomainComputeError(f"sod_ratings[{idx}] must be an object")
        # Every LLM-proposed rating must carry the pending-qualified-engineer-approval marker
        # (docs/02 SOD contract). A missing / non-true value would let an unapproved rating flow
        # into the RPN result as if it were final — fail closed instead.
        if item.get("approval_required") is not True:
            raise DomainComputeError(
                f"sod_ratings[{idx}].approval_required must be true (rating pending engineer approval)"
            )
        s = _rating(item, "severity", scale_max, idx)
        o = _rating(item, "occurrence", scale_max, idx)
        d = _rating(item, "detection", scale_max, idx)
        rpn = s * o * d
        entry = {
            "severity": s,
            "occurrence": o,
            "detection": d,
            "rpn": rpn,
            "high_priority": rpn >= high_priority_threshold,
        }
        # Carry the stable fmea_id through so the renderer can join RPN rows to controls by identity,
        # never by array position.
        if isinstance(item.get("fmea_id"), int) and not isinstance(item.get("fmea_id"), bool):
            entry["fmea_id"] = item["fmea_id"]
        if isinstance(item.get("failure_mode"), str):
            entry["source_failure_mode"] = item["failure_mode"]
        out.append(entry)
    return out
