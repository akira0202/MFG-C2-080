"""AgentCore Platform v1.0 — AuditMetadataEmitNode (inner, terminal) for MFG-C2-080."""

# Deterministic S-4 audit node (terminal). Assembles audit_metadata {data_hash, generated_at,
# rating_scale, model_version} — hashes only, no raw body. FunctionNode; ANONYMOUS (inner — gated
# at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; fail-closed; no S-2.

from datetime import datetime, timezone
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import audit_metadata
from src.services.failure_message import explain
from src.services.progress import emit_progress


class AuditMetadataEmitNode(FunctionNode):
    """Inner terminal node — assemble the S-4 audit_metadata artifact (hashes only)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        self._rating_scale = cfg.get("rating_scale", "iatf_16949")
        self._model_version = cfg.get("model_version", "unspecified")

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        fmea_table = state.get("fmea_table")
        if not isinstance(fmea_table, str):
            emit_trace_event("audit_emit_failed", {"correlation_id": corr, "reason": "missing_input"}, state)
            emit_progress(explain("missing_input"))
            return {"status": AgentStatus.ERROR.value, "error_log": ["AuditMetadataEmitNode: fmea_table missing"]}
        meta = audit_metadata.build_audit_metadata(
            fmea_table=fmea_table,
            safety_gate=state.get("safety_gate"),
            rating_scale=self._rating_scale,
            model_version=self._model_version,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        emit_trace_event("audit_emitted", {"correlation_id": corr, "data_hash": meta["data_hash"][:12]}, state)
        return {"audit_metadata": meta, "status": AgentStatus.SUCCESS.value}
