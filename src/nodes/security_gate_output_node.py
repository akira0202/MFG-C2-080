"""AgentCore Platform v1.0 — SecurityGateOutputNode (outer post_process) for MFG-C2-080."""

# Outer post_process node — the S-3 output gate. execute() reads the composed result and BLOCKS
# (status: error) when proprietary design data (IP) is detected; the framework @final
# _security_gate_output() (baseline credential scan) still runs after. The provisional disclaimer +
# safety banner are already carried in the rendered FMEA table.
# FunctionNode; VERIFIED_EXTERNAL (outer boundary); one S-4 event (output_gated).

from typing import Any, ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import output_gate
from src.services.progress import emit_progress
from src.services.degradation import explain_degradation
from src.services.failure_message import explain


class SecurityGateOutputNode(FunctionNode):
    """Outer S-3 output gate — block proprietary design data (IP) from the FMEA output."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        # Accepts node_config for a uniform graph-wiring signature (all domain slots are
        # constructed with node_config=self.config); the S-3 IP screen is a mandatory code
        # baseline with no config surface, so the value is not read here.
        super().__init__()

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        # S-3 output gate: execute() receives the full state, so it reads the composed result and
        # BLOCKS (status: error) when proprietary design data (IP) is detected; one S-4 event per
        # invocation. The block must live here, not in _extra_security_gate_output(): that hook is
        # passed execute()'s return delta (not state["result"]), and neither its returned error-state
        # nor a raised exception surfaces as the node result — a hook block would be inert.
        emit_progress("Preparing the response...")
        corr = state.get("correlation_id")
        code = state.get("diagnostic_code")
        if code:
            # The input gate answered the request — a correctable one, or a draft withheld for a
            # safety-critical category — so there is no table to gate. Release the notice as the
            # result of the turn. An empty FMEA table is NOT returned in its place: that would be a
            # real finding about equipment this agent never analysed.
            emit_trace_event("fmea_degraded", {"correlation_id": corr, "reason": code}, state)
            return {
                "status": AgentStatus.SUCCESS.value,
                "diagnostic_code": code,
                "formatted_output": explain_degradation(code),
                "result": None,
            }
        marker = output_gate.detect_proprietary(state.get("result"))
        emit_trace_event("output_gated", {"correlation_id": corr, "blocked": marker is not None}, state)
        if marker is not None:
            emit_progress(explain("output_gated"))
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["SecurityGateOutputNode: proprietary design data detected — " "output blocked (S-3)"],
            }
        return {}
