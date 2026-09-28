"""Unit tests for the MFG LLM / output nodes (require the framework — run in CI)."""

import json

import pytest

from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel

from src.nodes.failure_mode_normalize_node import FailureModeNormalizeNode
from src.nodes.sod_rating_compute_node import SodRatingComputeNode
from src.nodes.security_gate_output_node import SecurityGateOutputNode

_FM_JSON = json.dumps([{"function": "clamp part", "failure_mode": "clamp slips",
                        "effect": "misalignment", "cause": "worn jaw"}])
_SOD_JSON = json.dumps([{"fmea_id": 0, "failure_mode": "clamp slips", "severity": 8, "occurrence": 4, "detection": 5}])


class _FMLLM:
    def complete(self, messages, state):
        assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
        return _FM_JSON


class _SODLLM:
    def complete(self, messages, state):
        return _SOD_JSON


class _RaisingLLM:
    def complete(self, messages, state):
        raise TimeoutError("t")


def _validated():
    return {"equipment_name": "500t Hydraulic Press", "equipment_category": "hydraulic_press",
            "process_description": "forming", "failure_events": ["clamp slips during forming"]}


class TestContract:
    def test_no_arg_constructible(self):  # PB-6
        for nc in (FailureModeNormalizeNode, SodRatingComputeNode, SecurityGateOutputNode):
            assert nc() is not None

    def test_trust_levels(self):
        for nc in (FailureModeNormalizeNode, SodRatingComputeNode):
            assert nc.required_trust_level is TrustLevel.ANONYMOUS       # inner
        assert SecurityGateOutputNode.required_trust_level is TrustLevel.VERIFIED_EXTERNAL  # outer S-3


class TestFailureModeNormalize:
    def test_success_delta_only(self):
        d = FailureModeNormalizeNode(llm=_FMLLM()).execute({"validated_input": _validated(), "correlation_id": "t"})
        assert set(d.keys()) == {"failure_modes", "status"} and d["status"] == AgentStatus.SUCCESS.value
        assert d["failure_modes"][0]["failure_mode"] == "clamp slips"

    def test_llm_unavailable_propagates(self):
        assert FailureModeNormalizeNode(llm=None).execute({"validated_input": _validated(), "correlation_id": "t"})["status"] == AgentStatus.ERROR.value

    def test_malformed_llm_propagates(self):
        class _Bad:
            def complete(self, m, s): return "not json"
        assert FailureModeNormalizeNode(llm=_Bad()).execute({"validated_input": _validated(), "correlation_id": "t"})["status"] == AgentStatus.ERROR.value

    def test_missing_input_fails_closed(self):
        assert FailureModeNormalizeNode(llm=_FMLLM()).execute({"correlation_id": "t"})["status"] == AgentStatus.ERROR.value


class TestSodRatingCompute:
    def test_success_stamps_approval_required(self):
        fms = [{"fmea_id": 0, "failure_mode": "clamp slips", "effect": "x", "cause": "y"}]
        d = SodRatingComputeNode(llm=_SODLLM()).execute({"failure_modes": fms, "correlation_id": "t"})
        assert set(d.keys()) == {"sod_ratings", "status"} and d["status"] == AgentStatus.SUCCESS.value
        assert d["sod_ratings"][0]["approval_required"] is True and d["sod_ratings"][0]["severity"] == 8
        assert d["sod_ratings"][0]["fmea_id"] == 0

    def test_out_of_scale_rejected(self):
        class _Bad:
            def complete(self, m, s): return json.dumps([{"fmea_id": 0, "failure_mode": "x", "severity": 11, "occurrence": 1, "detection": 1}])
        assert SodRatingComputeNode(llm=_Bad()).execute({"failure_modes": [{"fmea_id": 0, "failure_mode": "x"}], "correlation_id": "t"})["status"] == AgentStatus.ERROR.value

    def test_unanchored_failure_modes_fail_closed(self):
        # a failure mode without an fmea_id cannot be safely joined downstream → fail closed
        assert SodRatingComputeNode(llm=_SODLLM()).execute({"failure_modes": [{"failure_mode": "x"}], "correlation_id": "t"})["status"] == AgentStatus.ERROR.value

    def test_llm_unavailable_propagates(self):
        assert SodRatingComputeNode(llm=None).execute({"failure_modes": [{"fmea_id": 0, "failure_mode": "x"}], "correlation_id": "t"})["status"] == AgentStatus.ERROR.value

    def test_missing_input_fails_closed(self):
        assert SodRatingComputeNode(llm=_SODLLM()).execute({"correlation_id": "t"})["status"] == AgentStatus.ERROR.value


class TestSodConfigFailClosed:
    """rating_scale is validated strictly at construction (fail closed), mirroring RPNCalculateNode —
    an unknown scale must never silently fall back to max=10 (would accept out-of-range ratings)."""

    def test_valid_scale_ok(self):
        assert SodRatingComputeNode({"rating_scale": "iso_9001_simplified"}, llm=_SODLLM()) is not None

    def test_unknown_scale_rejected(self):
        with pytest.raises(ConfigError):
            SodRatingComputeNode({"rating_scale": "iatf16949"}, llm=_SODLLM())  # typo → not silently max=10

    def test_iso_scale_enforces_stricter_max(self):
        # under iso_9001_simplified (max 5), a rating of 8 that is valid under iatf_16949 must be rejected
        class _Bad:
            def complete(self, m, s):
                return json.dumps([{"fmea_id": 0, "failure_mode": "x", "severity": 8, "occurrence": 1, "detection": 1}])
        node = SodRatingComputeNode({"rating_scale": "iso_9001_simplified"}, llm=_Bad())
        assert node.execute({"failure_modes": [{"fmea_id": 0, "failure_mode": "x"}], "correlation_id": "t"})["status"] == AgentStatus.ERROR.value


class TestSecurityGateOutput:
    def test_clean_output_passes(self):
        node = SecurityGateOutputNode()
        out = node.execute({"result": "# FMEA\n| clamp slips |", "correlation_id": "t"})
        assert out.get("status") != AgentStatus.ERROR.value

    def test_proprietary_output_blocked(self):
        node = SecurityGateOutputNode()
        out = node.execute({"result": "CONFIDENTIAL — proprietary die design", "correlation_id": "t"})
        assert out["status"] == AgentStatus.ERROR.value and "result" not in out

    def test_proprietary_japanese_blocked(self):
        node = SecurityGateOutputNode()
        out = node.execute({"result": {"fmea_table": "社外秘の金型設計"}, "correlation_id": "t"})
        assert out["status"] == AgentStatus.ERROR.value


class TestCountermeasureGenerate:
    """Propose advisory countermeasures for HIGH-priority (RPN >= threshold) failure modes only."""

    def _state(self, high=True):
        fm = [{"fmea_id": 0, "failure_mode": "crack", "effect": "scrap", "cause": "fatigue"}]
        rpn = [{"fmea_id": 0, "rpn": 160, "high_priority": high}]
        return {"failure_modes": fm, "rpn_results": rpn, "correlation_id": "t"}

    class _CtrlLLM:
        def complete(self, m, s):
            import json as _j
            p = _j.loads(m[1]["content"])
            return _j.dumps([{"fmea_id": fm["fmea_id"], "recommended_control": "Add interlock (review)"}
                             for fm in p["failure_modes"]])

    def test_high_priority_gets_proposed_control(self):
        from src.nodes.countermeasure_generate_node import CountermeasureGenerateNode
        d = CountermeasureGenerateNode(llm=self._CtrlLLM()).execute(self._state(high=True))
        assert d["status"] == AgentStatus.SUCCESS.value
        assert d["failure_modes"][0]["recommended_control"] == "Add interlock (review)"

    def test_no_high_priority_no_llm_needed(self):
        from src.nodes.countermeasure_generate_node import CountermeasureGenerateNode
        # low-priority only → no control proposed, no LLM call required (llm=None still succeeds)
        d = CountermeasureGenerateNode(llm=None).execute(self._state(high=False))
        assert d["status"] == AgentStatus.SUCCESS.value
        assert "recommended_control" not in d["failure_modes"][0]

    def test_high_priority_llm_unavailable_propagates(self):
        from src.nodes.countermeasure_generate_node import CountermeasureGenerateNode
        d = CountermeasureGenerateNode(llm=None).execute(self._state(high=True))
        assert d["status"] == AgentStatus.ERROR.value  # a high-RPN mode must not be silently un-actioned

    def test_missing_input_fails_closed(self):
        from src.nodes.countermeasure_generate_node import CountermeasureGenerateNode
        assert CountermeasureGenerateNode(llm=self._CtrlLLM()).execute(
            {"correlation_id": "t"})["status"] == AgentStatus.ERROR.value
