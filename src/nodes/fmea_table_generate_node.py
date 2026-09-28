"""AgentCore Platform v1.0 — FMEATableGenerateNode (inner) for MFG-C2-080."""

# Deterministic (no LLM). Render the RPN-ranked FMEA table (output_format markdown/csv/json)
# with IATF 16949 / ISO 9001 headers; inject the EU AI Act Art.9 block when
# eu_ai_act_art9_mode; surface the safety-gate review-required banner. FunctionNode;
# VERIFIED_EXTERNAL; delta-only; one S-4 event; fail-closed; no S-2.

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import fmea_rendering
from src.services.domain_errors import DomainComputeError
from src.services.failure_message import explain
from src.services.progress import emit_progress


class FMEATableGenerateNode(FunctionNode):
    """Inner deterministic node — render the FMEA table."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        self._output_format: str = str(cfg.get("output_format", "markdown"))
        self._eu_ai_act_art9_mode: bool = bool(cfg.get("eu_ai_act_art9_mode", False))

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        rpn_results = state.get("rpn_results")
        countermeasures = state.get("countermeasures")
        safety_gate = state.get("safety_gate")
        if (
            not isinstance(rpn_results, list)
            or not isinstance(countermeasures, list)
            or not isinstance(safety_gate, dict)
        ):
            emit_trace_event("fmea_table_failed", {"correlation_id": corr, "reason": "missing_input"}, state)
            emit_progress(explain("missing_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["FMEATableGenerateNode: rpn_results / countermeasures / safety_gate missing"],
            }
        try:
            table = fmea_rendering.render(
                rpn_results,
                countermeasures,
                safety_gate,
                output_format=self._output_format,
                eu_ai_act_art9_mode=self._eu_ai_act_art9_mode,
            )
        except DomainComputeError as exc:
            # a misaligned RPN/countermeasure id set must fail closed, never render a plausible but
            # mis-associated safety table
            emit_trace_event("fmea_table_failed", {"correlation_id": corr, "detail": str(exc)}, state)
            emit_progress(explain("fmea_table_failed"))
            return {"status": AgentStatus.ERROR.value, "error_log": [f"FMEATableGenerateNode: {exc}"]}
        emit_trace_event(
            "fmea_rendered",
            {"correlation_id": corr, "format": self._output_format, "blocked": safety_gate.get("blocked")},
            state,
        )
        return {"fmea_table": table, "status": AgentStatus.SUCCESS.value}
