"""Graph-level integration tests — prove the DEPLOYED config wiring, not just node
defaults (require the framework — run in CI).

The safety-acknowledgement precedence is only real if the graph injects config/config.yaml
into InputValidateNode. These tests construct the real Graph, register its nodes, and
exercise the node the graph built — so a regression to a bare ``InputValidateNode()``
(safety config not wired) fails here.
"""

import json

import pytest

from framework.errors import ConfigError
from framework.schemas.agent_status import AgentStatus

from src.graph.graph import EquipmentFMEADraftGeneratorAgentGraph as Graph


def _req(**extra):
    d = {"equipment_name": "500t Hydraulic Press", "equipment_category": "hydraulic_press",
         "process_description": "metal forming", "failure_events": ["die crack under load"]}
    d.update(extra)
    return json.dumps(d)


class TestGraphConfigWiring:
    def test_config_safety_ack_default_flows_through_graph(self):
        # safety-critical category + config ack default True, no per-call ack → accepted
        g = Graph(config={"safety_review_acknowledged": True})
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _req(), "correlation_id": "g"})
        assert r["status"] == AgentStatus.SUCCESS.value

    def test_safety_critical_rejected_without_ack_through_graph(self):
        # no config ack (default False), no per-call ack → rejected
        g = Graph(config={})
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _req(), "correlation_id": "g"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "safety_ack_required"
        assert "validated_input" not in r  # withheld: the request is not admitted

    def test_registration_rejects_non_bool_safety_ack_config(self):
        g = Graph(config={"safety_review_acknowledged": "true"})  # string, not bool
        with pytest.raises(ConfigError):
            g.register_nodes()

    def test_registration_uses_code_side_gate_when_config_absent(self):
        # no config → the fixed code-side category set keeps the gate active through the graph
        g = Graph(config={})
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _req(equipment_category="hydraulic_press"),
                          "correlation_id": "g"})
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "safety_ack_required"
        assert "validated_input" not in r


class _FakeLLM:
    """One adapter serves all three LLM inner nodes; branches on the system task. Echoes exactly one
    row per input (and copies each fmea_id back) so the row-alignment contracts are satisfied."""

    def complete(self, messages, state):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        if "FMEA schema rows" in system:  # failure_mode_normalize
            return json.dumps([{"function": "clamp part", "failure_mode": f"die crack {i}",
                                "effect": "scrap", "cause": "fatigue"}
                               for i, _ in enumerate(payload["failure_events"])])
        if "engineering countermeasure" in system:  # countermeasure_generate (high-priority modes)
            return json.dumps([{"fmea_id": fm["fmea_id"],
                                "recommended_control": "Install a monitoring sensor and inspect (review)"}
                               for fm in payload["failure_modes"]])
        return json.dumps([{"fmea_id": fm["fmea_id"], "failure_mode": fm["failure_mode"],  # sod_rating
                            "severity": 8, "occurrence": 4, "detection": 5}
                           for fm in payload["failure_modes"]])


class TestComposition:
    def test_main_is_the_inner_workflow_graph_node(self):
        from src.graph.domain_workflow_graph import FMEAWorkflowGraphNode
        g = Graph(config={})
        g.register_nodes()
        assert isinstance(g._nodes["main"], FMEAWorkflowGraphNode)

    def test_post_process_is_the_s3_gate(self):
        from src.nodes.security_gate_output_node import SecurityGateOutputNode
        g = Graph(config={})
        g.register_nodes()
        assert isinstance(g._nodes["post_process"], SecurityGateOutputNode)

    def test_inner_graph_invoke_directly(self):
        from src.graph.domain_workflow_graph import DomainWorkflowGraph
        inner = DomainWorkflowGraph(config={"llm": _FakeLLM()})
        result = inner.invoke(_req(safety_review_acknowledged=True))
        assert result.get("status") != "error", f"inner failed: {result.get('error_log')}"
        assert isinstance(result.get("output"), dict) and "fmea_table" in result["output"]
        # BL-14: the inner graph output carries the always-produced hash-only audit_metadata
        # (audit_metadata_emit runs every invoke; get_output projects it alongside the output).
        am = result.get("audit_metadata")
        assert isinstance(am, dict) and set(am) == {"data_hash", "generated_at", "rating_scale", "model_version"}, \
            f"audit_metadata must be the always-produced hash-only artifact (4 keys): {am}"
        # hash-only: a raw FMEA-table body (e.g. a failure-mode string from the fake LLM) is never stored
        assert "die crack" not in json.dumps(am, ensure_ascii=False)

    def test_end_to_end_invoke(self):
        from framework.schemas.invocation_context import InvocationContext
        from framework.schemas.trust_level import TrustLevel
        g = Graph(config={"llm": _FakeLLM()})
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        result = g.invoke(_req(safety_review_acknowledged=True), ctx=ctx)
        assert result.get("status") != "error", f"invoke errored: {result.get('error_log')}"
        nh = result.get("node_history", [])
        assert "FMEAWorkflowGraphNode" in nh and "SecurityGateOutputNode" in nh, f"composition: {nh}"
        # BL-14: the outer graph.get_output() surfaces the audit_metadata projected from the inner
        # workflow (merge_output -> outer State), so a successful outer invoke returns it.
        am = result.get("audit_metadata")
        assert isinstance(am, dict) and set(am) == {"data_hash", "generated_at", "rating_scale", "model_version"}, \
            f"audit_metadata must be projected to the outer result (4 keys): {am}"
        # hash-only: a raw FMEA-table body (e.g. a failure-mode string from the fake LLM) is never stored
        assert "die crack" not in json.dumps(am, ensure_ascii=False)


class TestSafetyGateThroughInvoke:
    """Regression for the invoke-path safety-gate fail-open: the MANDATORY gate keys on the
    equipment_category token through the deployed outer Graph.invoke() — no fail-open when the
    free-text equipment_name is masked by the framework S-2 baseline; every category is blocked
    (review-required), and unknown / unacknowledged-critical categories fail closed."""

    def _invoke(self, **extra):
        from framework.schemas.invocation_context import InvocationContext
        from framework.schemas.trust_level import TrustLevel
        g = Graph(config={"llm": _FakeLLM()})
        ctx = InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)
        return g.invoke(_req(**extra), ctx=ctx)

    def test_safety_critical_category_classified_through_invoke(self):
        # _req name is "500t Hydraulic Press" (Title-case, maskable) + category hydraulic_press:
        # the CATEGORY drives classification, so safety_critical is True through the deployed
        # invoke even though the name would be masked — this is the fail-open regression guard.
        result = self._invoke(safety_review_acknowledged=True)
        assert result.get("status") != "error", result.get("error_log")
        gate = result["output"]["safety_gate"]
        assert gate["safety_critical"] is True and gate["blocked"] is True

    def test_general_equipment_still_blocked_through_invoke(self):
        # non-critical category → admitted without ack, but the FMEA is STILL blocked
        # (review-required) — a caller label never grants auto-approval.
        result = self._invoke(equipment_category="general_equipment")
        assert result.get("status") != "error", result.get("error_log")
        gate = result["output"]["safety_gate"]
        assert gate["safety_critical"] is False and gate["blocked"] is True

    def test_unacknowledged_safety_critical_is_withheld_through_invoke(self):
        result = self._invoke()  # hydraulic_press category, no acknowledgement
        assert result.get("diagnostic_code") == "safety_ack_required"
        assert result.get("audit_metadata") is None  # no FMEA was drafted, not even an empty one

    def test_unknown_category_is_answered_through_invoke(self):
        result = self._invoke(equipment_category="widget")
        assert result.get("diagnostic_code") == "unknown_equipment_category"
        assert result.get("audit_metadata") is None
