"""AgentCore Platform v1.0 — SodRatingComputeNode (inner) for MFG-C2-080."""

# LLM-assisted node: propose S/O/D ratings per failure mode on the configured rating_scale; each
# rating is pending qualified-engineer approval (approval_required: true). FunctionNode; ANONYMOUS
# (inner — gated at the outer VERIFIED_EXTERNAL boundary); delta-only; one S-4 event; no S-2.
# error_strategy = propagate: an unavailable / failing / malformed LLM is a hard error.

from typing import Any, ClassVar

from framework.errors import ConfigError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.services.failure_message import GENERATION_UNAVAILABLE
from src.services.progress import emit_progress
from shared.utils.audit_logger import emit_trace_event

from src.services import sod_rating
from src.services.failure_message import explain


class SodRatingComputeNode(FunctionNode):
    """Inner LLM-assisted node — propose SOD ratings (pending approval; fail-closed)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None, llm: Any = None) -> None:
        super().__init__()
        self._llm = llm
        cfg = node_config or {}
        # Validate rating_scale strictly at construction (fail closed, never coerced), mirroring
        # RPNCalculateNode — an unknown scale is a deployment fault, not a silent max=10 default.
        # Unset falls back to the secure code default (PB-6 no-arg construction stays valid).
        scale = cfg.get("rating_scale", "iatf_16949")
        if scale not in sod_rating.VALID_RATING_SCALES:
            raise ConfigError(f"config rating_scale '{scale}' must be one of {sod_rating.VALID_RATING_SCALES}")
        self._rating_scale: str = scale

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        failure_modes = state.get("failure_modes")
        if not isinstance(failure_modes, list) or not failure_modes:
            emit_trace_event("sod_rating_failed", {"correlation_id": corr, "reason": "missing_input"}, state)
            emit_progress(explain("missing_input"))
            return {"status": AgentStatus.ERROR.value, "error_log": ["SodRatingComputeNode: failure_modes missing"]}
        expected_ids = [fm.get("fmea_id") for fm in failure_modes if isinstance(fm, dict)]
        if any(not isinstance(fid, int) or isinstance(fid, bool) for fid in expected_ids) or len(expected_ids) != len(
            failure_modes
        ):
            emit_trace_event("sod_rating_failed", {"correlation_id": corr, "reason": "unanchored_input"}, state)
            emit_progress(explain("unanchored_input"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SodRatingComputeNode: every failure mode must carry an integer fmea_id"],
            }
        if self._llm is None:
            emit_trace_event("sod_rating_failed", {"correlation_id": corr, "reason": "llm_unavailable"}, state)
            emit_progress(GENERATION_UNAVAILABLE)
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SodRatingComputeNode: LLM unavailable (propagate)"],
            }
        try:
            messages = sod_rating.build_sod_messages(failure_modes, self._rating_scale)
            emit_progress("Rating severity, occurrence and detection...")
            text = self._llm.complete(messages, state)
            sod_ratings = sod_rating.parse_and_validate_sod(text, self._rating_scale, expected_ids)
        except Exception as exc:
            emit_trace_event("sod_rating_failed", {"correlation_id": corr, "error": type(exc).__name__}, state)
            emit_progress(GENERATION_UNAVAILABLE)
            return {"status": AgentStatus.ERROR.value, "error_log": [f"SodRatingComputeNode: {type(exc).__name__}"]}
        emit_trace_event("sod_proposed", {"correlation_id": corr, "count": len(sod_ratings)}, state)
        return {"sod_ratings": sod_ratings, "status": AgentStatus.SUCCESS.value}
