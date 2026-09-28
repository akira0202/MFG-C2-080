"""AgentCore Platform v1.0 — SafetyGateCheckNode (inner) for MFG-C2-080."""

# Deterministic MANDATORY safety gate (Pattern 11 — static config-flag, not HITL). Classify by
# the structured equipment_category TOKEN against the fixed code-side critical set; in V1 BLOCK
# auto-approval for EVERY category (every FMEA is review-required), and for a safety-critical
# category additionally produce the explicit mandatory safety-review warning. The safety-critical
# set is the SAME fixed code-side SSOT as the outer input_validate (_DEFAULT_SAFETY_CRITICAL_CATEGORIES)
# — no config override in V1. Classification never uses the maskable free-text equipment_name (ADR-017).
# FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; fail-closed; no S-2.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.nodes.input_validate_node import _DEFAULT_SAFETY_CRITICAL_CATEGORIES
from src.services import safety_classification
from src.services.failure_message import explain
from src.services.progress import emit_progress


class SafetyGateCheckNode(FunctionNode):
    """Inner deterministic node — mandatory safety gate (warning + block auto-approval)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        # The safety-critical category set is the outer node's FIXED code-side SSOT — no config
        # override in V1 (an operator/caller cannot widen or narrow it). Keeps PB-6 no-arg
        # construction working and the set never fail-open.
        self._safety_critical: frozenset[str] = frozenset(_DEFAULT_SAFETY_CRITICAL_CATEGORIES)

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        validated = state.get("validated_input")
        if not isinstance(validated, dict) or not validated.get("equipment_category"):
            emit_trace_event("safety_gate_failed", {"correlation_id": corr, "reason": "missing_category"}, state)
            emit_progress(explain("missing_category"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SafetyGateCheckNode: validated_input.equipment_category missing"],
            }
        gate = safety_classification.build_safety_gate(validated["equipment_category"], self._safety_critical)
        emit_trace_event(
            "safety_gate_checked",
            {"correlation_id": corr, "safety_critical": gate["safety_critical"], "blocked": gate["blocked"]},
            state,
        )
        return {"safety_gate": gate, "status": AgentStatus.SUCCESS.value}
