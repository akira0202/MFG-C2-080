"""AgentCore Platform v1.0 — InputHydrateNode (first inner node) for MFG-C2-080."""

# Extend FunctionNode; execute(state) -> dict; return only changed fields (no {**state});
# AgentStatus.<X>.value for status; ANONYMOUS (inner — gated at the outer VERIFIED_EXTERNAL boundary); one S-4 event. First inner node: JSON-decode the
# handoff string (the outer validated_input) back into validated_input. No duplicated S-2
# (trusted inner handoff).

import json
from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.failure_message import explain
from src.services.progress import emit_progress


class InputHydrateNode(FunctionNode):
    """Inner hydration node — decode the handoff string into validated_input."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        raw = state.get("user_input")
        if not isinstance(raw, str) or not raw.strip():
            emit_trace_event("input_hydrate_failed", {"correlation_id": corr, "reason": "empty_handoff"}, state)
            emit_progress(explain("empty_handoff"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputHydrateNode: inner user_input handoff is empty or not a string"],
            }
        try:
            validated = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            emit_trace_event("input_hydrate_failed", {"correlation_id": corr, "reason": "malformed"}, state)
            emit_progress(explain("malformed"))
            return {"status": AgentStatus.ERROR.value, "error_log": ["InputHydrateNode: malformed handoff JSON"]}
        except RecursionError:
            emit_trace_event("input_hydrate_failed", {"correlation_id": corr, "reason": "too_deep"}, state)
            emit_progress(explain("too_deep"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputHydrateNode: handoff JSON nesting is too deep"],
            }
        if not isinstance(validated, dict) or "equipment_name" not in validated:
            emit_trace_event("input_hydrate_failed", {"correlation_id": corr, "reason": "shape"}, state)
            emit_progress(explain("shape"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["InputHydrateNode: handoff must be the canonical validated_input object"],
            }
        emit_trace_event("validated_input_hydrated", {"correlation_id": corr}, state)
        return {"validated_input": validated, "status": AgentStatus.SUCCESS.value}
