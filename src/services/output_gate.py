"""AgentCore Platform v1.0 — S-3 output-gate helpers for MFG-C2-080.

Pure functions (no framework imports; unit-testable standalone). Screens the FMEA output for
proprietary design data (IP) that must not leave in an AI-assisted draft. The provisional
"pending qualified-engineer approval" disclaimer and the safety-review banner are already carried
in the rendered FMEA table (fmea_rendering); this gate is the last check that the draft carries no
proprietary markings before release.
"""

from __future__ import annotations

import json
from typing import Any

# Markers that indicate proprietary / confidential design IP in the output — case-insensitive.
_PROPRIETARY_MARKERS = (
    "proprietary",
    "confidential",
    "trade secret",
    "company confidential",
    "not for distribution",
    "internal use only",
    "機密",
    "社外秘",
    "極秘",
    "部外秘",
    "取扱注意",
)


def _blob(result: Any) -> str:
    if isinstance(result, str):
        return result.lower()
    try:
        return json.dumps(result, ensure_ascii=False, default=str).lower()
    except Exception:
        return str(result).lower()


def detect_proprietary(result: Any) -> str | None:
    """Return the first proprietary-IP marker found in the output, or None when the output is clean."""
    low = _blob(result)
    for m in _PROPRIETARY_MARKERS:
        if m in low:
            return m
    return None
