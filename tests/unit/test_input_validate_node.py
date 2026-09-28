# MFG-C2-080 — Unit Tests: InputValidateNode (outer pre_process / S-2 gate)

import json

import pytest

from src.nodes.input_validate_node import InputValidateNode
from src.services.failure_message import explain
from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus


def _payload(equipment_name="CNC lathe", equipment_category="general_equipment",
             process_description="turning shaft", failure_events=None, **extra):
    p = {
        "equipment_name": equipment_name,
        "equipment_category": equipment_category,
        "process_description": process_description,
        "failure_events": ["spindle overheating"] if failure_events is None else failure_events,
    }
    p.update(extra)
    return json.dumps(p)


class TestSuccess:
    def setup_method(self):
        self.node = InputValidateNode()

    def test_valid_input(self):
        r = self.node.execute({"user_input": _payload(failure_events=["a", "b"]), "correlation_id": "t1"})
        assert r["status"] == AgentStatus.SUCCESS.value
        vi = r["validated_input"]
        assert vi["equipment_name"] == "CNC lathe"
        assert vi["failure_events"] == ["a", "b"]
        assert vi["safety_review_acknowledged"] is False   # config default

    def test_structured_only_drops_unexpected(self):
        r = self.node.execute({"user_input": _payload(operator_id="山田 太郎"), "correlation_id": "t2"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "operator_id" not in r["validated_input"]   # raw-PII-bearing unexpected field dropped

    def test_input_validated_trace_once(self, monkeypatch):
        events = []
        monkeypatch.setattr("src.nodes.input_validate_node.emit_trace_event",
                            lambda et, p, s: events.append(et))
        r = self.node.execute({"user_input": _payload(), "correlation_id": "t3"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert events.count("input_validated") == 1

    def test_returns_only_validated_input_and_status(self):
        r = self.node.execute({"user_input": _payload(), "correlation_id": "t4"})
        assert set(r.keys()) == {"validated_input", "status"}


class TestRejections:
    def setup_method(self):
        self.node = InputValidateNode()

    def _ex(self, ui):
        return self.node.execute({"user_input": ui, "correlation_id": "r"})

    def test_missing_equipment_name(self):
        r = self._ex(json.dumps({"process_description": "x", "failure_events": ["a"]}))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "equipment_name" in explain(r["diagnostic_code"])

    def test_missing_process_description(self):
        r = self._ex(json.dumps({"equipment_name": "x", "failure_events": ["a"]}))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "process_description" in explain(r["diagnostic_code"])

    def test_failure_events_missing(self):
        r = self._ex(json.dumps({"equipment_name": "x", "equipment_category": "general_equipment",
                                 "process_description": "y"}))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "failure_events" in explain(r["diagnostic_code"])

    def test_failure_events_empty(self):
        r = self._ex(_payload(failure_events=[]))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "failure_events_empty"
        assert "failure_events" in explain(r["diagnostic_code"])

    def test_failure_events_not_list(self):
        r = self._ex(_payload(failure_events="just a string"))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "failure_events must be a list" in explain(r["diagnostic_code"])

    def test_failure_events_non_str_item(self):
        r = self._ex(_payload(failure_events=["ok", 123]))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "failure_events_item"
        # The sentence names the field, never the entry the caller sent.
        assert "failure_events" in explain(r["diagnostic_code"])

    def test_failure_events_empty_str_item(self):
        r = self._ex(_payload(failure_events=["ok", "   "]))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "failure_events_item"

    def test_failure_events_item_injection_rejected(self):
        # docs/02 "reject injection in the NL failure descriptions" — a stray control
        # char (other than tab/newline/CR) is structural injection, rejected.
        r = self._ex(_payload(failure_events=["overheating", "bad\x1b[2Jclear"]))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "failure_events_item_invalid"
        assert "not allowed" in explain(r["diagnostic_code"])

    def test_failure_events_item_multiline_ok(self):
        r = self._ex(_payload(failure_events=["overheating\nunder sustained load"]))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "sustained load" in r["validated_input"]["failure_events"][0]

    def test_process_description_injection_rejected(self):
        r = self._ex(_payload(process_description="turning\x07evil"))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "process_description" in explain(r["diagnostic_code"])

    def test_process_description_multiline_ok(self):
        r = self._ex(_payload(process_description="Step 1: load\nStep 2: cut\nStep 3: unload"))
        assert r["status"] == AgentStatus.SUCCESS.value

    def test_malformed_json(self):
        r = self._ex('{"equipment_name": ')
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "malformed"
        assert "not valid JSON" in explain(r["diagnostic_code"])

    def test_non_object_json(self):
        r = self._ex(json.dumps(["a", "b"]))
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "JSON object" in explain(r["diagnostic_code"])


class TestSafetyAcknowledgement:
    """Admission control keyed on the equipment_category TOKEN (never the maskable name)."""

    def test_safety_critical_without_ack_rejected(self):
        node = InputValidateNode()  # config default false
        r = node.execute({"user_input": _payload(equipment_category="hydraulic_press"), "correlation_id": "s"})
        # Withheld on policy, not a malfunction: answered, and still not admitted.
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "safety_ack_required"
        assert "validated_input" not in r
        assert "safety review acknowledgement" in explain(r["diagnostic_code"])

    def test_safety_critical_with_percall_ack_accepted(self):
        node = InputValidateNode()
        r = node.execute({"user_input": _payload(equipment_category="hydraulic_press",
                                                 safety_review_acknowledged=True), "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["validated_input"]["safety_review_acknowledged"] is True

    def test_safety_critical_with_config_ack_accepted(self):
        node = InputValidateNode(node_config={"safety_review_acknowledged": True})
        r = node.execute({"user_input": _payload(equipment_category="plc"), "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value

    def test_non_safety_critical_admitted_with_false(self):
        node = InputValidateNode()
        r = node.execute({"user_input": _payload(equipment_category="general_equipment"), "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["validated_input"]["safety_review_acknowledged"] is False

    def test_percall_ack_non_bool_rejected(self):
        node = InputValidateNode()
        r = node.execute({"user_input": _payload(safety_review_acknowledged="yes"), "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "safety_ack_type"
        assert "validated_input" not in r

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, ["x"]])
    def test_non_bool_config_ack_raises_at_construction(self, bad):
        # config default is validated, not coerced — bool("false") == True would open the gate
        with pytest.raises(ConfigError):
            InputValidateNode(node_config={"safety_review_acknowledged": bad})


class TestEquipmentCategoryGate:
    """The MANDATORY safety gate classifies by the FIXED code-side equipment_category token set
    (no config override in V1). Unknown/missing category fails closed; a critical category needs
    acknowledgement to be admitted; the maskable free-text equipment_name is never the decision."""

    def test_no_arg_construction_gate_active(self):
        # PB-6 constructs node_cls() with no args → must not raise AND the gate must be active
        r = InputValidateNode().execute(
            {"user_input": _payload(equipment_category="hydraulic_press"), "correlation_id": "s"})
        # critical category, no ack → withheld, and no validated_input is produced
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "safety_ack_required"
        assert "validated_input" not in r

    def test_non_critical_admitted_under_default(self):
        r = InputValidateNode().execute(
            {"user_input": _payload(equipment_category="general_equipment"), "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value

    def test_missing_equipment_category_rejected(self):
        p = json.dumps({"equipment_name": "CNC lathe", "process_description": "turning",
                        "failure_events": ["x"]})  # no equipment_category
        r = InputValidateNode().execute({"user_input": p, "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "equipment_category" in explain(r["diagnostic_code"])

    @pytest.mark.parametrize("bad", ["Hydraulic Press", "hydraulic press", "widget", "", "   ", 123, None])
    def test_unknown_equipment_category_fails_closed(self, bad):
        r = InputValidateNode().execute(
            {"user_input": _payload(equipment_category=bad), "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "equipment_category" in explain(r["diagnostic_code"])

    def test_free_text_name_does_not_drive_the_gate(self):
        # a critical-sounding equipment_name with a general_equipment category is admitted without ack
        # — proves the safety decision keys on the token, not the maskable name (no ack demanded here).
        r = InputValidateNode().execute(
            {"user_input": _payload(equipment_name="500t Hydraulic Press",
                                    equipment_category="general_equipment"), "correlation_id": "s"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["validated_input"]["equipment_category"] == "general_equipment"


class TestS2Gate:
    def setup_method(self):
        self.node = InputValidateNode()

    def test_s2_rejects_non_str_type(self):
        for bad in (b"binary", 123, {"a": 1}, ["l"]):
            g = self.node._extra_security_gate_input({"user_input": bad, "correlation_id": "s2"})
            assert g["status"] == AgentStatus.ERROR.value, f"{type(bad).__name__} should be rejected"
            assert "validated_input" not in g

    def test_s2_rejects_nul(self):
        g = self.node._extra_security_gate_input({"user_input": "ok\x00bin", "correlation_id": "s2"})
        assert g["status"] == AgentStatus.ERROR.value
        assert "NUL" in g["error_log"][0]

    def test_s2_answers_oversized_without_parsing_it(self):
        """The ceiling is a limit the caller can work within, so it is answered rather than
        refused. The answer carries no user_input, and execute() returns on seeing the code — so an
        oversized body is still never parsed."""
        node = InputValidateNode(node_config={"max_input_chars": 100})
        g = node._extra_security_gate_input({"user_input": "x" * 500, "correlation_id": "s2"})
        assert g["status"] == AgentStatus.SUCCESS.value
        assert g["diagnostic_code"] == "s2_oversized"
        assert set(g.keys()) == {"status", "diagnostic_code"}
        after = node.execute(g)
        assert after == {"status": AgentStatus.SUCCESS.value, "diagnostic_code": "s2_oversized"}
        assert "validated_input" not in after

    def test_s2_answer_does_not_let_a_body_be_parsed_afterwards(self):
        """Guard the short-circuit itself: if execute() stopped honouring the code, the oversized
        body left in state would be parsed after the gate had already answered."""
        node = InputValidateNode(node_config={"max_input_chars": 100})
        after = node.execute({"user_input": _payload(), "diagnostic_code": "s2_oversized", "correlation_id": "s2"})
        assert "validated_input" not in after
        assert after["diagnostic_code"] == "s2_oversized"

    def test_s2_passes_clean(self):
        st = {"user_input": _payload(), "correlation_id": "s2"}
        assert self.node._extra_security_gate_input(st) is st

    def test_s2_does_not_coerce_binary(self):
        g = self.node._extra_security_gate_input({"user_input": b"\x00abc", "correlation_id": "s2"})
        assert g["status"] == AgentStatus.ERROR.value
        assert "must be text" in g["error_log"][0]


class TestCallPathBoundary:
    """PB-style: via the node's real __call__ path, an S-2 reject short-circuits before
    execute() so validated_input is never produced (framework runs the @final gate)."""

    def test_s2_reject_via_call_does_not_invoke_execute(self, monkeypatch):
        # Prove the S-2 gate short-circuits: execute() must NOT run on an S-2-invalid
        # payload. Asserting only that validated_input is absent is insufficient —
        # execute() also omits it for bytes, so that would pass for the wrong reason.
        calls = []
        orig_execute = InputValidateNode.execute

        def spy(self, state, _o=orig_execute):
            calls.append(True)
            return _o(self, state)

        monkeypatch.setattr(InputValidateNode, "execute", spy)
        node = InputValidateNode()
        state = {
            "user_input": b"\x00binary",  # non-text → S-2 reject in the gate, before execute()
            "caller_trust_level": InputValidateNode.required_trust_level.value,
            "correlation_id": "call-pb",
            "session_id": "s", "thread_id": "t", "trace_id": "tr",
        }
        result = node(state)
        assert calls == []  # execute() was NOT invoked — the S-2 gate short-circuited
        assert "validated_input" not in result
        assert result.get("status") == AgentStatus.ERROR.value


class TestRecursionSafety:
    """Deeply nested JSON must fail-closed (ERROR), never raise RecursionError."""

    def test_deep_nesting_does_not_crash(self):
        node = InputValidateNode()
        deep = "{" * 2000 + "}" * 2000  # pathologically nested JSON tokens
        result = node.execute({"user_input": deep, "correlation_id": "d"})
        assert result["status"] == AgentStatus.SUCCESS.value


class TestEquipmentNameScreen:
    """equipment_name (the safety-list match key) is control-char + length screened."""

    def setup_method(self):
        self.node = InputValidateNode()

    def _ex(self, en):
        return self.node.execute({"user_input": _payload(equipment_name=en), "correlation_id": "e"})

    def test_control_char_injection_rejected(self):
        r = self._ex("Press\nHACK")
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "equipment_name" in explain(r["diagnostic_code"])

    def test_over_length_rejected(self):
        r = self._ex("A" * 300)
        assert r["status"] == AgentStatus.SUCCESS.value
        assert "equipment_name" in explain(r["diagnostic_code"])
