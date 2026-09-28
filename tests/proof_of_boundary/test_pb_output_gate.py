# PB: S-3 Output Security Gate — boundary output-protection proof
#
# Proves the S-3 output gate at the framework BOUNDARY (BaseNode.__call__), exercising the deployed
# post_process node rather than a helper in isolation. MFG's S-3 blocks in execute() (which receives
# the full state and reads the composed result): when the FMEA output carries a proprietary /
# confidential design-IP marker (English or Japanese, top-level or nested), the real __call__ result
# is an error — the successful output does not reach the caller. execute() emits exactly one
# output_gated S-4 event per invocation carrying whether the output was blocked.

from framework.schemas.agent_status import AgentStatus

from src.nodes.security_gate_output_node import SecurityGateOutputNode


def _call_state(result, **extra):
    """A minimal __call__ state that passes the S-1 trust gate for SecurityGateOutputNode."""
    state = {
        "result": result,
        "caller_trust_level": SecurityGateOutputNode.required_trust_level.value,
        "correlation_id": "pb-s3",
        "session_id": "s",
        "thread_id": "t",
        "trace_id": "tr",
    }
    state.update(extra)
    return state


def _spy_events(monkeypatch):
    """Capture (event_type, payload) for every emit_trace_event from the S-3 node."""
    events: list[tuple] = []
    monkeypatch.setattr(
        "src.nodes.security_gate_output_node.emit_trace_event",
        lambda event_type, payload, _state, _e=events: _e.append((event_type, payload)),
    )
    return events


class TestS3OutputGateProprietaryBlock:
    def test_english_proprietary_marker_blocked_at_boundary(self, monkeypatch):
        events = _spy_events(monkeypatch)
        result = SecurityGateOutputNode()(_call_state("FMEA draft: this design is PROPRIETARY to ACME"))
        assert result.get("status") == AgentStatus.ERROR.value   # the real __call__ boundary result is blocked
        assert any(e[0] == "output_gated" and e[1].get("blocked") is True for e in events)

    def test_japanese_proprietary_marker_nested_blocked_at_boundary(self, monkeypatch):
        events = _spy_events(monkeypatch)
        # marker in a nested field — detect_proprietary flattens the result (json, ensure_ascii=False)
        result = SecurityGateOutputNode()(_call_state({"fmea_table": [{"note": "社外秘 の設計データ"}]}))
        assert result.get("status") == AgentStatus.ERROR.value
        assert any(e[0] == "output_gated" and e[1].get("blocked") is True for e in events)


class TestS3OutputGateClean:
    def test_clean_output_passes_and_gated_once(self, monkeypatch):
        events = _spy_events(monkeypatch)
        result = SecurityGateOutputNode()(_call_state({"fmea_table": [
            {"function": "clamp part", "failure_mode": "die crack", "effect": "scrap"}]}))
        assert result.get("status") != AgentStatus.ERROR.value   # a clean FMEA output is not blocked
        assert any(e[0] == "output_gated" and e[1].get("blocked") is False for e in events)
        assert [e[0] for e in events].count("output_gated") == 1  # exactly one S-4 event per invocation
