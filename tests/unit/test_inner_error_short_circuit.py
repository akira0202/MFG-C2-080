"""The inner workflow must stop at the first failing node — MFG-C2-080.

Every hop of the inner graph is a conditional edge that routes to END on `status: error`. Without
that, a failure at the first LLM node still hands control to each remaining node in turn. The
framework's input gate skips a node whose incoming State is already errored and returns the whole
State from `BaseNode.__call__()`, so `error_log` — an `operator.add` reducer channel — is
concatenated onto itself once per remaining node and the audit trail doubles at each hop.

The suite that follows pins both halves: the workflow stops, and the error is recorded once.
"""

import json

from src.graph.domain_workflow_graph import DomainWorkflowGraph

_REQUEST = json.dumps(
    {
        "equipment_name": "500t Hydraulic Press",
        "equipment_category": "general_equipment",
        "process_description": "metal forming",
        "failure_events": ["die crack under load", "hydraulic seal leak"],
    }
)


class _RaisingLLM:
    """Fails on the first call, the way an unprovisioned key or a dead provider does."""

    def complete(self, messages, state):
        raise RuntimeError("provider unavailable")


def _errored_run() -> dict:
    inner = DomainWorkflowGraph(config={"llm": _RaisingLLM()})
    result = inner.invoke(_REQUEST)
    assert result["status"] == "error"
    return result


class TestInnerErrorShortCircuit:
    def test_the_failure_is_recorded_exactly_once(self):
        """The regression this guards: an unconditional chain produced 2^(remaining nodes) copies
        of the same message — 128 entries for one failure."""
        error_log = _errored_run()["error_log"]

        assert len(error_log) == len(set(error_log)) == 1

    def test_the_failure_is_attributed_to_the_node_that_failed(self):
        assert _errored_run()["error_log"][0].startswith("FailureModeNormalizeNode:")

    def test_no_downstream_node_runs_after_the_failure(self):
        """A node after the failing one must not be invoked at all — not invoked-and-skipped."""
        calls = []

        class _CountingLLM(_RaisingLLM):
            def complete(self, messages, state):
                calls.append(messages)
                raise RuntimeError("provider unavailable")

        inner = DomainWorkflowGraph(config={"llm": _CountingLLM()})
        inner.invoke(_REQUEST)

        # sod_rating_compute and countermeasure_generate would each reach the provider boundary
        # if the chain kept advancing past the failed normalize step.
        assert len(calls) == 1
