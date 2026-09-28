"""AgentCore Platform v1.0 — RPNCalculateNode (inner) for MFG-C2-080."""

# Deterministic (no LLM). RPN = S × O × D from sod_ratings; flag high-priority when
# RPN >= rpn_high_priority_threshold. FunctionNode; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4
# event; fail-closed; no S-2.

from typing import Any, ClassVar

from framework.errors import ConfigError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import rpn_calculation
from src.services.domain_errors import DomainComputeError
from src.services.failure_message import explain
from src.services.progress import emit_progress

_DEFAULT_THRESHOLD = 100


class RPNCalculateNode(FunctionNode):
    """Inner deterministic node — Risk Priority Number + high-priority flag."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        cfg = node_config or {}
        # Config is validated strictly at construction (fail closed, never coerced): an unknown
        # rating_scale or a non-positive / non-int threshold is a deployment fault, not a runtime
        # default. Unset falls back to the secure code default (PB-6 no-arg construction stays valid).
        scale = cfg.get("rating_scale", "iatf_16949")
        if scale not in rpn_calculation.VALID_RATING_SCALES:
            raise ConfigError(f"config rating_scale '{scale}' must be one of {rpn_calculation.VALID_RATING_SCALES}")
        self._rating_scale: str = scale
        threshold = cfg.get("rpn_high_priority_threshold", _DEFAULT_THRESHOLD)
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 1:
            raise ConfigError("config rpn_high_priority_threshold must be a positive integer")
        self._threshold: int = threshold

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        sod_ratings = state.get("sod_ratings")
        try:
            rpn_results = rpn_calculation.calculate(
                sod_ratings, rating_scale=self._rating_scale, high_priority_threshold=self._threshold
            )
        except DomainComputeError as exc:
            emit_trace_event("rpn_calculate_failed", {"correlation_id": corr, "detail": str(exc)}, state)
            emit_progress(explain("rpn_calculate_failed"))
            return {"status": AgentStatus.ERROR.value, "error_log": [f"RPNCalculateNode: {exc}"]}
        emit_trace_event(
            "rpn_computed",
            {
                "correlation_id": corr,
                "items": len(rpn_results),
                "high_priority": sum(1 for r in rpn_results if r["high_priority"]),
            },
            state,
        )
        return {"rpn_results": rpn_results, "status": AgentStatus.SUCCESS.value}
