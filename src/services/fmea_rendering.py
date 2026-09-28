"""AgentCore Platform v1.0 — deterministic FMEA-table rendering for MFG-C2-080.

Pure functions (no framework imports, unit-testable standalone; no LLM). Joins the RPN
results with the classified countermeasures, ranks by RPN (descending), and renders the
FMEA table in the configured ``output_format`` (markdown / csv / json) with IATF 16949 /
ISO 9001 column headers. Injects the EU AI Act Art.9 risk-management block when enabled,
and surfaces the safety-gate review-required banner.
"""

from __future__ import annotations

from typing import Any


import csv
import io
import json

from src.services.domain_errors import DomainComputeError

_COLUMNS = [
    "Failure Mode",
    "Severity",
    "Occurrence",
    "Detection",
    "RPN",
    "High Priority",
    "Recommended Control",
    "Control Type",
]

# Always surfaced on every output: the S/O/D ratings are agent-assisted proposals, never final.
# Marks the human-review boundary even for non-safety-critical equipment / non-EU-AI-Act runs
# (where the safety banner and the Art.9 block do not apply).
_PROVISIONAL_DISCLAIMER = (
    "Provisional AI-assisted FMEA draft — Severity / Occurrence / Detection ratings are pending "
    "qualified-engineer review and approval; not a final risk assessment."
)

_EU_AI_ACT_ART9 = (
    "EU AI Act Article 9 — Risk Management: this FMEA is an AI-assisted draft. A continuous, "
    "iterative risk-management process with human oversight applies; SOD ratings and controls "
    "require qualified-engineer review and approval before use."
)


def _index_by_id(items: list[Any], kind: str) -> dict[int, Any]:
    """Index RPN rows / countermeasures by their stable ``fmea_id``. Fail closed on a row missing an
    id or a duplicate id — joining by array position would otherwise associate a rating/control with
    the wrong failure mode when an upstream (LLM) stage dropped, added, reordered, or duplicated a row."""
    by_id: dict[int, Any] = {}
    for item in items or []:
        fid = (item or {}).get("fmea_id")
        if isinstance(fid, bool) or not isinstance(fid, int):
            raise DomainComputeError(f"fmea_render: {kind} row is missing an integer fmea_id (cannot join safely)")
        if fid in by_id:
            raise DomainComputeError(f"fmea_render: duplicate fmea_id {fid} in {kind}")
        by_id[fid] = item
    return by_id


def _rows(rpn_results: list[Any], countermeasures: list[Any]) -> list[dict[str, Any]]:
    # Join RPN rows to controls by identity (fmea_id), never by position.
    rpn_by_id = _index_by_id(rpn_results, "rpn_results")
    cm_by_id = _index_by_id(countermeasures, "countermeasures")
    if set(rpn_by_id) != set(cm_by_id):
        raise DomainComputeError(
            "fmea_render: RPN rows and countermeasures do not cover the same failure-mode id set "
            f"(rpn={sorted(rpn_by_id)}, controls={sorted(cm_by_id)}) — refusing to render a misaligned table"
        )
    rows: list[dict[str, Any]] = []
    for fid, r in rpn_by_id.items():
        cm = cm_by_id[fid]
        rows.append(
            {
                "Failure Mode": cm.get("source_failure_mode") or r.get("source_failure_mode") or f"item {fid + 1}",
                "Severity": r.get("severity"),
                "Occurrence": r.get("occurrence"),
                "Detection": r.get("detection"),
                "RPN": r.get("rpn"),
                "High Priority": "Yes" if r.get("high_priority") else "No",
                "Recommended Control": cm.get("control") or "-",
                "Control Type": cm.get("type") or "-",
            }
        )
    rows.sort(key=lambda x: (x["RPN"] is None, -(x["RPN"] or 0)))
    return rows


def render(
    rpn_results: list[Any],
    countermeasures: list[Any],
    safety_gate: dict[str, Any],
    *,
    output_format: str = "markdown",
    eu_ai_act_art9_mode: bool = False,
) -> str:
    """Render the ranked FMEA table as a string in ``output_format``."""
    rows = _rows(rpn_results, countermeasures)
    safety_gate = safety_gate or {}

    if output_format == "json":
        return json.dumps(
            {
                "disclaimer": _PROVISIONAL_DISCLAIMER,
                "columns": _COLUMNS,
                "rows": rows,
                "safety_gate": safety_gate,
                "eu_ai_act_art9": _EU_AI_ACT_ART9 if eu_ai_act_art9_mode else None,
            },
            ensure_ascii=True,
        )

    if output_format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=_COLUMNS)
        w.writeheader()
        for row in rows:
            w.writerow(row)
        body = buf.getvalue()
    else:  # markdown (default)
        header = "| " + " | ".join(_COLUMNS) + " |"
        sep = "| " + " | ".join("---" for _ in _COLUMNS) + " |"
        lines = [header, sep]
        for row in rows:
            lines.append("| " + " | ".join(str(row[c]) for c in _COLUMNS) + " |")
        body = "\n".join(lines)

    banner = [f"> {_PROVISIONAL_DISCLAIMER}"]  # always first — the human-review boundary marker
    if safety_gate.get("blocked"):
        banner.append(f"> ⚠️ REVIEW REQUIRED — {safety_gate.get('warning')}")
    if eu_ai_act_art9_mode:
        banner.append(f"> {_EU_AI_ACT_ART9}")
    return "\n".join(banner) + "\n\n" + body
