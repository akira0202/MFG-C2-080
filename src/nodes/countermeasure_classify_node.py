"""AgentCore Platform v1.0 — CountermeasureClassifyNode (inner) for MFG-C2-080."""

# Deterministic (no LLM). Classify each failure mode's recommended control as
# Preventive / Detective / Corrective by taxonomy keyword match. FunctionNode;
# VERIFIED_EXTERNAL; delta-only; one S-4 event; fail-closed; no S-2.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import countermeasure_classification
from src.services.domain_errors import DomainComputeError
from src.services.failure_message import explain
from src.services.progress import emit_progress


class CountermeasureClassifyNode(FunctionNode):
    """Inner deterministic node — classify recommended controls."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        raw = cfg.get("countermeasure_taxonomy")
        self._taxonomy: dict[str, Any] = raw if isinstance(raw, dict) else {}

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        failure_modes = state.get("failure_modes")
        if not isinstance(failure_modes, list):
            emit_trace_event(
                "countermeasure_classify_failed", {"correlation_id": corr, "reason": "missing_input"}, state
            )
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["CountermeasureClassifyNode: failure_modes missing or malformed"],
            }
        try:
            countermeasures = countermeasure_classification.classify(failure_modes, self._taxonomy)
        except DomainComputeError as exc:
            emit_trace_event("countermeasure_classify_failed", {"correlation_id": corr, "detail": str(exc)}, state)
            emit_progress(explain("countermeasure_classify_failed"))
            return {"status": AgentStatus.ERROR.value, "error_log": [f"CountermeasureClassifyNode: {exc}"]}
        emit_trace_event("countermeasures_classified", {"correlation_id": corr, "count": len(countermeasures)}, state)
        return {"countermeasures": countermeasures, "status": AgentStatus.SUCCESS.value}
