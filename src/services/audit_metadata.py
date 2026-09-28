"""AgentCore Platform v1.0 — audit-metadata assembly for MFG-C2-080.

Pure functions (no framework imports; unit-testable standalone). Assembles the S-4 audit
artifact for the FMEA audit trail. Raw bodies are NEVER persisted — only a content hash. The
inner get_output() returns {output, audit_metadata}; this module builds the audit_metadata and
the final output payload (the rendered FMEA table + the safety gate).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _hash(*parts: Any) -> str:
    blob = json.dumps(parts, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_audit_metadata(
    *, fmea_table: Any, safety_gate: Any, rating_scale: Any, model_version: Any, generated_at: str
) -> dict[str, Any]:
    """Return audit_metadata {data_hash, generated_at, rating_scale, model_version} — hashes only."""
    return {
        "data_hash": _hash(fmea_table, safety_gate),
        "generated_at": generated_at,
        "rating_scale": rating_scale,
        "model_version": model_version,
    }


def build_output(*, fmea_table: Any, safety_gate: Any) -> dict[str, Any]:
    """Assemble the human-review payload projected to the outer result: the rendered FMEA table and
    the mandatory safety gate (warning + block flag)."""
    return {"fmea_table": fmea_table, "safety_gate": safety_gate if isinstance(safety_gate, dict) else {}}
