"""AgentCore Platform v1.0 — FailureModeNormalizeNode (inner) for MFG-C2-080."""

# LLM node: parse the validated NL failure descriptions into FMEA schema rows
# {fmea_id, function, failure_mode, effect, cause} — exactly one row per input failure event, each
# stamped a stable fmea_id. FunctionNode; ANONYMOUS (inner — gated at the outer
# VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; no S-2. error_strategy = propagate: an
# unavailable / failing / malformed LLM is a hard error (status: error), never a partial schema.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.failure_message import GENERATION_UNAVAILABLE
from src.services.progress import emit_progress
from shared.utils.audit_logger import emit_trace_event

from src.services import failure_mode_normalization
from src.services.failure_message import explain


class FailureModeNormalizeNode(FunctionNode):
    """Inner LLM node — normalize NL failures into the FMEA schema (fail-closed)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        validated = state.get("validated_input")
        if not isinstance(validated, dict):
            emit_trace_event(
                "failure_mode_normalize_failed", {"correlation_id": corr, "reason": "missing_input"}, state
            )
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["FailureModeNormalizeNode: validated_input missing"],
            }
        failure_events = validated.get("failure_events")
        if not isinstance(failure_events, list) or not failure_events:
            emit_trace_event(
                "failure_mode_normalize_failed", {"correlation_id": corr, "reason": "no_failure_events"}, state
            )
            emit_progress(explain("no_failure_events"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["FailureModeNormalizeNode: validated_input.failure_events missing"],
            }
        if self._llm is None:
            emit_trace_event(
                "failure_mode_normalize_failed", {"correlation_id": corr, "reason": "llm_unavailable"}, state
            )
            emit_progress(GENERATION_UNAVAILABLE)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["FailureModeNormalizeNode: LLM unavailable (propagate)"],
            }
        try:
            messages = failure_mode_normalization.build_normalize_messages(validated)
            emit_progress("Normalising the failure modes...")
            text = self._llm.complete(messages, state)
            # exactly one normalized row per input failure event (fail closed on drift)
            failure_modes = failure_mode_normalization.parse_and_validate_failure_modes(
                text, expected_count=len(failure_events)
            )
        except Exception as exc:
            emit_trace_event(
                "failure_mode_normalize_failed", {"correlation_id": corr, "error": type(exc).__name__}, state
            )
            emit_progress(GENERATION_UNAVAILABLE)
            return {"status": AgentStatus.ERROR.value, "error_log": [f"FailureModeNormalizeNode: {type(exc).__name__}"]}
        emit_trace_event("failure_modes_normalized", {"correlation_id": corr, "count": len(failure_modes)}, state)
        return {"failure_modes": failure_modes, "status": AgentStatus.SUCCESS.value}
