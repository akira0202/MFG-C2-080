# PB: S-2 Input Security Gate — boundary rejection proof
#
# Proves the S-2 structural input gate at the framework BOUNDARY (BaseNode.__call__),
# not by calling the gate helper in isolation: a non-text / binary / control-char /
# oversized raw user_input is rejected by _extra_security_gate_input() BEFORE execute()
# runs, so it can never reach the JSON parser. Each rejection test spies execute() and
# asserts it was NOT invoked — asserting only that validated_input is absent is
# insufficient (execute() also omits it for bytes, which would pass for the wrong
# reason). A clean FMEA request is the negative control: it flows through the gate into
# execute() and succeeds.

import json

from framework.schemas.agent_status import AgentStatus

from src.nodes.input_validate_node import InputValidateNode


def _call_state(user_input, **extra):
    """A minimal __call__ state that passes the S-1 trust gate for InputValidateNode."""
    state = {
        "user_input": user_input,
        "caller_trust_level": InputValidateNode.required_trust_level.value,
        "correlation_id": "pb-s2",
        "session_id": "s",
        "thread_id": "t",
        "trace_id": "tr",
    }
    state.update(extra)
    return state


def _spy_execute(monkeypatch):
    """Patch InputValidateNode.execute with a spy that records invocation and still runs."""
    calls: list[bool] = []
    orig = InputValidateNode.execute

    def spy(self, state, _o=orig):
        calls.append(True)
        return _o(self, state)

    monkeypatch.setattr(InputValidateNode, "execute", spy)
    return calls


def _clean_request():
    # general_equipment is non-safety-critical, so no safety acknowledgement is required for admission.
    return json.dumps({"equipment_name": "CNC lathe", "equipment_category": "general_equipment",
                       "process_description": "metal turning operation",
                       "failure_events": ["tool wear under sustained load"]})


class TestS2InputGateBoundaryRejection:
    """S-2 must short-circuit at __call__ — execute() never runs on rejected input."""

    def test_binary_bytes_rejected_before_execute(self, monkeypatch):
        calls = _spy_execute(monkeypatch)
        result = InputValidateNode()(_call_state(b"\x00binary-not-text"))
        assert calls == []  # execute() was NOT reached — the S-2 gate short-circuited
        assert result.get("status") == AgentStatus.ERROR.value
        assert "validated_input" not in result

    def test_nul_byte_in_string_rejected_before_execute(self, monkeypatch):
        calls = _spy_execute(monkeypatch)
        result = InputValidateNode()(_call_state("clean text\x00smuggled"))
        assert calls == []
        assert result.get("status") == AgentStatus.ERROR.value
        assert "validated_input" not in result

    def test_control_char_flood_rejected_before_execute(self, monkeypatch):
        calls = _spy_execute(monkeypatch)
        payload = "".join(chr(1) for _ in range(200))  # excessive control chars → binary signal
        result = InputValidateNode()(_call_state(payload))
        assert calls == []
        assert result.get("status") == AgentStatus.ERROR.value
        assert "validated_input" not in result

    def test_oversized_input_is_answered_and_never_parsed(self, monkeypatch):
        """The ceiling is answered, not refused — a caller can send less. What the boundary still
        has to hold is that the oversized body is never parsed: the gate hands execute() a delta
        carrying the code and no user_input, and execute() returns on it."""
        calls = _spy_execute(monkeypatch)
        node = InputValidateNode(node_config={"max_input_chars": 100})
        result = node(_call_state("x" * 500))
        assert calls == [True]  # execute() is entered — and returns on the code
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result.get("diagnostic_code") == "s2_oversized"
        assert "validated_input" not in result  # nothing was parsed out of the body


class TestS2InputGateBoundaryPass:
    """Negative control — a clean FMEA request flows through S-2 into execute() and succeeds."""

    def test_clean_request_reaches_execute_and_succeeds(self, monkeypatch):
        calls = _spy_execute(monkeypatch)
        result = InputValidateNode()(_call_state(_clean_request()))
        assert calls == [True]  # the gate passed the clean input through to execute()
        assert result.get("status") == AgentStatus.SUCCESS.value
        assert result["validated_input"]["equipment_name"] == "CNC lathe"
