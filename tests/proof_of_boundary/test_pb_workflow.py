# PB workflow: end-to-end boundary tests for the approved workflow (outer Graph.invoke).
#
# Proves at the deployed outer boundary: (1) the required human-review posture is surfaced to the
# caller — a safety-critical equipment_category FMEA is marked review-required (output.safety_gate.blocked
# with a warning), and the always-on audit_metadata artifact reaches the caller; (2) a safety-critical
# category without acknowledgement fails closed at input validation; and (3) invalid input fails closed
# (error_strategy=propagate). Classification keys on the equipment_category token — the maskable
# equipment_name is not used. Synthetic inputs only; the LLM is faked (no live model call).

import json

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import EquipmentFMEADraftGeneratorAgentGraph as Graph
from src.services.failure_message import explain


def _req(**extra):
    d = {"equipment_name": "500t Hydraulic Press", "equipment_category": "hydraulic_press",
         "process_description": "metal forming",
         "failure_events": ["die crack under load"]}  # hydraulic_press is a safety-critical category
    d.update(extra)
    return json.dumps(d)


class _FakeLLM:
    """One adapter serves all three LLM inner nodes; branches on the system task; echoes one row per
    input and copies each fmea_id back so the row-alignment contracts hold."""

    def complete(self, messages, state):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        if "FMEA schema rows" in system:  # failure_mode_normalize
            return json.dumps([{"function": "clamp part", "failure_mode": f"die crack {i}",
                                "effect": "scrap", "cause": "fatigue"}
                               for i, _ in enumerate(payload["failure_events"])])
        if "engineering countermeasure" in system:  # countermeasure_generate
            return json.dumps([{"fmea_id": fm["fmea_id"],
                                "recommended_control": "Install a monitoring sensor and inspect (review)"}
                               for fm in payload["failure_modes"]])
        return json.dumps([{"fmea_id": fm["fmea_id"], "failure_mode": fm["failure_mode"],  # sod_rating
                            "severity": 8, "occurrence": 4, "detection": 5}
                           for fm in payload["failure_modes"]])


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


def _graph():
    # the deployed graph classifies by the fixed code-side equipment_category set (no config override);
    # equipment_category "hydraulic_press" is safety-critical — the maskable equipment_name is not used
    return Graph(config={"llm": _FakeLLM()})


class TestWorkflowSafetyReviewEscalation:
    def test_safety_critical_review_required_surfaced_to_caller(self):
        # acknowledged so the request is admitted; the FMEA is still marked review-required (blocked)
        result = _graph().invoke(_req(safety_review_acknowledged=True), ctx=_ctx())
        assert result.get("status") != "error", f"invoke errored: {result.get('error_log')}"
        output = result.get("output")
        assert isinstance(output, dict), f"final output must reach the caller: {output!r}"
        gate = output.get("safety_gate")
        assert isinstance(gate, dict) and gate.get("safety_critical") is True
        assert gate.get("blocked") is True and gate.get("warning")  # auto-approval blocked → human review
        assert isinstance(result.get("audit_metadata"), dict)  # always-on artifact reaches the caller
        nh = result.get("node_history", [])
        assert "FMEAWorkflowGraphNode" in nh and "SecurityGateOutputNode" in nh, f"composition: {nh}"
        # composition runs in order: inner workflow node before the outer S-3 gate
        assert nh.index("FMEAWorkflowGraphNode") < nh.index("SecurityGateOutputNode"), f"order: {nh}"


class TestWorkflowFailsClosed:
    def test_safety_critical_without_ack_is_withheld_not_drafted(self):
        """The draft is withheld, and the caller is told why. What matters at this boundary is that
        no FMEA is produced — not even an empty one — and that the workflow never runs."""
        result = _graph().invoke(_req(), ctx=_ctx())  # no acknowledgement (default false)
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result.get("diagnostic_code") == "safety_ack_required"
        assert result.get("output") == explain("safety_ack_required")
        assert result.get("audit_metadata") is None  # the inner workflow did not run

    def test_malformed_input_is_answered_with_a_notice(self):
        result = _graph().invoke("{ this is not valid json", ctx=_ctx())
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result.get("diagnostic_code") == "malformed"
        assert result.get("output") == explain("malformed")
        assert result.get("audit_metadata") is None

    def test_a_refusal_this_agent_makes_still_fails_closed(self):
        """The screen that decides whether the body is a document at all keeps refusing: a NUL byte
        is not a shape a caller is invited to correct."""
        result = _graph().invoke("clean text\x00smuggled", ctx=_ctx())
        assert result.get("status") == AgentStatus.ERROR.value
        assert not result.get("output")
        assert result.get("diagnostic_code") is None


class _CredentialControlLLM:
    """normalize + sod_rating return normal rows (RPN 160 ≥ 100 → high-priority, so the countermeasure
    LLM runs); the countermeasure branch returns a recommended_control embedding a credential-shaped
    token. It passes the advisory-text validation and is attached to the FMEA rows, so the framework
    @final S-3 credential scan trips on the countermeasure_generate node's return. AKIAIOSFODNN7EXAMPLE
    is the public AWS documentation example key, not a live secret."""

    def complete(self, messages, state):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        if "FMEA schema rows" in system:  # failure_mode_normalize
            return json.dumps([{"function": "clamp part", "failure_mode": f"die crack {i}",
                                "effect": "scrap", "cause": "fatigue"}
                               for i, _ in enumerate(payload["failure_events"])])
        if "engineering countermeasure" in system:  # countermeasure_generate
            return json.dumps([{"fmea_id": fm["fmea_id"],
                                "recommended_control": "Install a monitoring sensor AKIAIOSFODNN7EXAMPLE and inspect (review)"}
                               for fm in payload["failure_modes"]])
        return json.dumps([{"fmea_id": fm["fmea_id"], "failure_mode": fm["failure_mode"],  # sod_rating
                            "severity": 8, "occurrence": 4, "detection": 5}
                           for fm in payload["failure_modes"]])


class TestS3CredentialBlockEndToEnd:
    """PB: the framework baseline S-3 credential scan BLOCKS end-to-end. A credential-shaped
    token carried in an LLM-generated countermeasure reaches the countermeasure_generate node's return;
    the baseline @final _security_gate_output — which runs before, and independent of, the domain output
    gate — fails closed. Under error_strategy=propagate the outer invoke returns status:error with no
    output, and the credential body never reaches a caller-facing channel. BLOCK is the approved posture;
    there is no downstream redaction-to-success."""

    def _credential_graph(self):
        return Graph(config={"llm": _CredentialControlLLM()})

    def test_credential_in_generated_control_blocks_with_no_output(self):
        result = self._credential_graph().invoke(_req(safety_review_acknowledged=True), ctx=_ctx())
        # fail-closed BLOCK, not a redacted success
        assert result.get("status") == AgentStatus.ERROR.value
        assert result.get("output") is None
        # the credential body must not reach any caller-facing channel (result / audit / error_log / trace)
        surfaced = json.dumps(result, ensure_ascii=False, default=str)
        assert "AKIAIOSFODNN7EXAMPLE" not in surfaced
