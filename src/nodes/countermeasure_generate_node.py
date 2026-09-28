"""AgentCore Platform v1.0 — CountermeasureGenerateNode (inner) for MFG-C2-080."""

# LLM-assisted node: for the HIGH-priority failure modes (RPN ≥ threshold) propose an engineering
# countermeasure per failure mode (advisory; pending qualified-engineer approval). Augments each
# high-priority failure mode with recommended_control; the downstream countermeasure_classify then
# classifies it (Preventive / Detective / Corrective). Low-priority failure modes get no proposed
# control (carried Unclassified). FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL
# boundary); delta-only; one S-4 event; no S-2.
# error_strategy = propagate: an unavailable / failing / malformed LLM (when high-priority modes exist)
# is a hard error — never a silently un-actioned high-RPN failure mode.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.failure_message import GENERATION_UNAVAILABLE
from src.services.progress import emit_progress
from shared.utils.audit_logger import emit_trace_event

from src.services import countermeasure_generation
from src.services.failure_message import explain


class CountermeasureGenerateNode(FunctionNode):
    """Inner LLM node — propose advisory countermeasures for high-RPN failure modes (fail-closed)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        failure_modes = state.get("failure_modes")
        rpn_results = state.get("rpn_results")
        if not isinstance(failure_modes, list) or not failure_modes or not isinstance(rpn_results, list):
            emit_trace_event(
                "countermeasure_generate_failed", {"correlation_id": corr, "reason": "missing_input"}, state
            )
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["CountermeasureGenerateNode: failure_modes / rpn_results missing"],
            }

        high_ids = {
            r["fmea_id"]
            for r in rpn_results
            if isinstance(r, dict)
            and r.get("high_priority") is True
            and isinstance(r.get("fmea_id"), int)
            and not isinstance(r.get("fmea_id"), bool)
        }
        high_modes = [fm for fm in failure_modes if isinstance(fm, dict) and fm.get("fmea_id") in high_ids]

        if not high_modes:
            # no high-priority failure mode → no countermeasure proposed (low-RPN modes stay Unclassified)
            emit_trace_event("countermeasures_generated", {"correlation_id": corr, "proposed": 0}, state)
            return {"failure_modes": failure_modes, "status": AgentStatus.SUCCESS.value}

        if self._llm is None:
            emit_trace_event(
                "countermeasure_generate_failed", {"correlation_id": corr, "reason": "llm_unavailable"}, state
            )
            emit_progress(GENERATION_UNAVAILABLE)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["CountermeasureGenerateNode: LLM unavailable (propagate)"],
            }
        try:
            messages = countermeasure_generation.build_generate_messages(high_modes)
            emit_progress("Drafting the recommended controls...")
            text = self._llm.complete(messages, state)
            controls = countermeasure_generation.parse_and_validate_controls(text, [fm["fmea_id"] for fm in high_modes])
        except Exception as exc:
            emit_trace_event(
                "countermeasure_generate_failed", {"correlation_id": corr, "error": type(exc).__name__}, state
            )
            emit_progress(GENERATION_UNAVAILABLE)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": [f"CountermeasureGenerateNode: {type(exc).__name__}"],
            }

        # augment the high-priority failure modes with the proposed advisory control (delta-only:
        # return the whole failure_modes list with recommended_control attached by fmea_id)
        augmented = []
        for fm in failure_modes:
            fid = fm.get("fmea_id") if isinstance(fm, dict) else None
            if fid in controls:
                augmented.append({**fm, "recommended_control": controls[fid]})
            else:
                augmented.append(fm)
        emit_trace_event("countermeasures_generated", {"correlation_id": corr, "proposed": len(controls)}, state)
        return {"failure_modes": augmented, "status": AgentStatus.SUCCESS.value}
