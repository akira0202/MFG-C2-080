"""Unit tests for the MFG inner deterministic nodes (require the framework — run in CI)."""

import json

import pytest

from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.input_hydrate_node import InputHydrateNode
from src.nodes.rpn_calculate_node import RPNCalculateNode
from src.nodes.countermeasure_classify_node import CountermeasureClassifyNode
from src.nodes.safety_gate_check_node import SafetyGateCheckNode
from src.nodes.fmea_table_generate_node import FMEATableGenerateNode

import src.nodes.rpn_calculate_node as rpn_mod

_ALL = [InputHydrateNode, RPNCalculateNode, CountermeasureClassifyNode,
        SafetyGateCheckNode, FMEATableGenerateNode]


def _validated(equipment_category="hydraulic_press"):
    return {"equipment_name": "500t Hydraulic Press", "equipment_category": equipment_category,
            "process_description": "forming", "failure_events": ["crack"],
            "safety_review_acknowledged": True}


class TestContract:
    def test_all_no_arg_constructible(self):  # PB-6
        for nc in _ALL:
            assert nc() is not None

    def test_all_inner_anonymous(self):
        # inner nodes run inside the outer VERIFIED_EXTERNAL S-1 boundary; the framework does not
        # propagate caller trust into the inner graph, so inner nodes declare ANONYMOUS explicitly.
        for nc in _ALL:
            assert nc.required_trust_level is TrustLevel.ANONYMOUS


class TestRPNConfigFailClosed:
    """rpn_calculate validates its config strictly at construction (fail closed, never coerced)."""

    def test_valid_config_ok(self):
        assert RPNCalculateNode({"rating_scale": "iso_9001_simplified",
                                 "rpn_high_priority_threshold": 50}) is not None

    def test_unknown_rating_scale_rejected(self):
        with pytest.raises(ConfigError):
            RPNCalculateNode({"rating_scale": "iatf16949"})  # typo → not silently max=10

    def test_threshold_bool_rejected(self):
        with pytest.raises(ConfigError):
            RPNCalculateNode({"rpn_high_priority_threshold": True})  # int(True)==1 would flag all

    def test_threshold_non_positive_rejected(self):
        for bad in (0, -5):
            with pytest.raises(ConfigError):
                RPNCalculateNode({"rpn_high_priority_threshold": bad})

    def test_threshold_non_int_rejected(self):
        for bad in (1.5, "100", None):
            with pytest.raises(ConfigError):
                RPNCalculateNode({"rpn_high_priority_threshold": bad})


class TestEndToEnd:
    def _run(self, equipment_category="hydraulic_press", art9=True):
        state = {"user_input": json.dumps(_validated(equipment_category)), "correlation_id": "e"}
        state.update(InputHydrateNode().execute(state))
        state["sod_ratings"] = [{"fmea_id": 0, "severity": 8, "occurrence": 5, "detection": 4,
                                 "failure_mode": "crack", "approval_required": True}]
        state["failure_modes"] = [{"fmea_id": 0, "failure_mode": "crack", "recommended_control": "install monitor sensor"}]
        state.update(RPNCalculateNode().execute(state))
        state.update(CountermeasureClassifyNode().execute(state))
        state.update(SafetyGateCheckNode().execute(state))
        state.update(FMEATableGenerateNode({"eu_ai_act_art9_mode": art9}).execute(state))
        return state

    def test_full_flow(self):
        s = self._run()
        assert s["status"] == AgentStatus.SUCCESS.value
        assert s["rpn_results"][0]["rpn"] == 160 and s["rpn_results"][0]["high_priority"] is True
        assert s["countermeasures"][0]["type"] == "Detective"
        assert s["safety_gate"]["safety_critical"] and s["safety_gate"]["blocked"]
        assert "REVIEW REQUIRED" in s["fmea_table"] and "Article 9" in s["fmea_table"]

    def test_non_critical_still_blocked(self):
        # V1 conservative: a non-critical category is NOT safety_critical but its FMEA is STILL
        # review-required (blocked) — no caller-declared category grants auto-approval.
        s = self._run(equipment_category="general_equipment", art9=False)
        assert s["safety_gate"]["safety_critical"] is False
        assert s["safety_gate"]["blocked"] is True

    def test_rpn_delta_keys(self):
        d = RPNCalculateNode().execute(
            {"sod_ratings": [{"severity": 3, "occurrence": 3, "detection": 3, "approval_required": True}],
             "correlation_id": "t"})
        assert set(d.keys()) == {"rpn_results", "status"}


class TestFailClosed:
    def test_rpn_bad_ratings(self):
        assert RPNCalculateNode().execute({"sod_ratings": [], "correlation_id": "x"})["status"] == AgentStatus.ERROR.value

    def test_rpn_out_of_range(self):
        d = RPNCalculateNode().execute(
            {"sod_ratings": [{"severity": 99, "occurrence": 1, "detection": 1, "approval_required": True}],
             "correlation_id": "x"})
        assert d["status"] == AgentStatus.ERROR.value

    def test_rpn_missing_approval_required_fails_closed(self):
        # an unapproved SOD rating must not flow into the RPN result as if final
        d = RPNCalculateNode().execute({"sod_ratings": [{"severity": 3, "occurrence": 3, "detection": 3}],
                                        "correlation_id": "x"})
        assert d["status"] == AgentStatus.ERROR.value

    def test_countermeasure_missing(self):
        assert CountermeasureClassifyNode().execute({"correlation_id": "x"})["status"] == AgentStatus.ERROR.value

    def test_countermeasure_malformed_member_fails_closed(self):
        # a malformed failure-mode record must fail closed, never silently drop and shift alignment
        d = CountermeasureClassifyNode().execute(
            {"failure_modes": [{"recommended_control": "inspect"}, "not a dict"], "correlation_id": "x"})
        assert d["status"] == AgentStatus.ERROR.value

    def test_safety_missing_equipment(self):
        assert SafetyGateCheckNode().execute({"validated_input": {}, "correlation_id": "x"})["status"] == AgentStatus.ERROR.value

    def test_fmea_missing_inputs(self):
        assert FMEATableGenerateNode().execute({"correlation_id": "x"})["status"] == AgentStatus.ERROR.value


class TestAuditEvents:
    def test_rpn_emits_domain_event(self, monkeypatch):
        seen = []
        monkeypatch.setattr(rpn_mod, "emit_trace_event", lambda et, p, s=None: seen.append(et))
        RPNCalculateNode().execute(
            {"sod_ratings": [{"severity": 3, "occurrence": 3, "detection": 3, "approval_required": True}],
             "correlation_id": "t"})
        assert "rpn_computed" in seen
