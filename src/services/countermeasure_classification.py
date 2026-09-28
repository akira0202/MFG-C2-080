"""AgentCore Platform v1.0 — deterministic countermeasure classification for MFG-C2-080.

Pure functions (no framework imports, unit-testable standalone; no LLM). Classifies each
failure mode's recommended control as Preventive / Detective / Corrective by keyword match
against the ``countermeasure_taxonomy`` (config-overridable). A control with no taxonomy
match is classified ``Corrective`` (the conservative default — act on the realized failure);
a failure mode with no recommended control is carried as ``Unclassified``.
"""

from __future__ import annotations

from typing import Any

from src.services.domain_errors import DomainComputeError

_DEFAULT_TAXONOMY = {
    "Preventive": (
        "prevent",
        "avoid",
        "design out",
        "poka-yoke",
        "mistake-proof",
        "eliminate",
        "redundan",
        "interlock",
        "training",
        "procedure",
        "maintenance",
        "predictive",
    ),
    "Detective": ("detect", "inspect", "monitor", "sensor", "alarm", "test", "audit", "measure", "check"),
    "Corrective": ("correct", "repair", "rework", "replace", "recover", "contain", "shutdown"),
}
_ORDER = ("Preventive", "Detective", "Corrective")


def classify(failure_modes: Any, taxonomy: dict[str, Any] | None = None) -> list[Any]:
    """Return ``countermeasures``: ``[{fmea_id, control, type, approval_required?, source_failure_mode?}]`` — EXACTLY one entry
    per failure mode, in order. Raises ``DomainComputeError`` on a malformed member so a bad record
    can never be silently dropped (which would shift the positional join to RPN rows and associate a
    control with the wrong failure mode). A missing control is the valid ``Unclassified`` case; a
    control that is present but not text is malformed."""
    if not isinstance(failure_modes, list):
        return []
    tax = {**_DEFAULT_TAXONOMY, **(taxonomy or {})}
    out: list[dict[str, Any]] = []
    for i, fm in enumerate(failure_modes):
        if not isinstance(fm, dict):
            raise DomainComputeError(f"failure_modes[{i}] must be an object")
        control = fm.get("recommended_control")
        if control is None:
            control = fm.get("control")
        entry: dict[str, Any] = {}
        # Carry the stable fmea_id so the renderer joins controls to RPN rows by identity, not position.
        if isinstance(fm.get("fmea_id"), int) and not isinstance(fm.get("fmea_id"), bool):
            entry["fmea_id"] = fm["fmea_id"]
        if isinstance(fm.get("failure_mode"), str):
            entry["source_failure_mode"] = fm["failure_mode"]
        if control is None:
            entry.update({"control": None, "type": "Unclassified"})  # no recommended control (valid)
            out.append(entry)
            continue
        if not isinstance(control, str):
            raise DomainComputeError(f"failure_modes[{i}] control must be text when present")
        if not control.strip():
            entry.update({"control": None, "type": "Unclassified"})
            out.append(entry)
            continue
        text = control.lower()
        ctype = "Corrective"  # conservative default when no keyword matches
        for cat in _ORDER:
            if any(kw in text for kw in tax.get(cat, ())):
                ctype = cat
                break
        # a proposed control is LLM-assisted and advisory — pending qualified-engineer approval
        entry.update({"control": control, "type": ctype, "approval_required": True})
        out.append(entry)
    return out
