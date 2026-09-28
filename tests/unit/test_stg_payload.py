"""Stage 5 provisional-deploy regression — distinguishes template bugs from the live-LLM gap.

The provisional deploy-stg boots src/api/server.py config-less; MFG's inner LLM nodes are
mandatory and fail-closed (`error_strategy=propagate`), so the first invoke makes a live
LLM call. If `AZURE_OPENAI_*` is not resolvable in the CI env (or egress fails), the invoke
returns `status: error` — an infrastructure gap, not a template defect.

This test proves the template side is correct: with a WORKING (fake) LLM injected, the
canonical `deploy/invoke_payload.json` runs the full FMEA workflow to `status: success`.
So a deploy-stg FAIL on the same payload is the live-LLM path (key/egress), not the
payload, wiring, parser, or workflow contract. Synthetic input only; no live model call.
"""

import json
from pathlib import Path

from framework.schemas.agent_status import AgentStatus
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import EquipmentFMEADraftGeneratorAgentGraph as Graph

_CANONICAL_INPUT = json.loads((Path(__file__).resolve().parents[2] / "deploy" / "invoke_payload.json").read_text())[
    "input"
]


class _FakeLLM:
    """One adapter serves all three inner LLM nodes; branches on the system task and copies
    each fmea_id back so the row-alignment contracts hold (mirrors test_pb_workflow)."""

    def complete(self, messages, state):
        system = messages[0]["content"]
        payload = json.loads(messages[1]["content"])
        if "FMEA schema rows" in system:  # failure_mode_normalize
            return json.dumps(
                [
                    {"function": "operate", "failure_mode": f"fault {i}", "effect": "downtime", "cause": "wear"}
                    for i, _ in enumerate(payload["failure_events"])
                ]
            )
        if "engineering countermeasure" in system:  # countermeasure_generate
            return json.dumps(
                [
                    {
                        "fmea_id": fm["fmea_id"],
                        "recommended_control": "Add condition monitoring and periodic inspection (review)",
                    }
                    for fm in payload["failure_modes"]
                ]
            )
        return json.dumps(  # sod_rating_compute
            [
                {
                    "fmea_id": fm["fmea_id"],
                    "failure_mode": fm["failure_mode"],
                    "severity": 8,
                    "occurrence": 4,
                    "detection": 5,
                }
                for fm in payload["failure_modes"]
            ]
        )


def _ctx():
    return InvocationContext(caller_trust_level=TrustLevel.VERIFIED_EXTERNAL)


class TestStgCanonicalPayload:
    def test_canonical_payload_succeeds_with_working_llm(self):
        # config-less graph except a working (fake) LLM — the template side of the deploy-stg path.
        result = Graph(config={"llm": _FakeLLM()}).invoke(_CANONICAL_INPUT, ctx=_ctx())
        assert result.get("status") == AgentStatus.SUCCESS.value, (
            "canonical STG payload must reach status:success with a working LLM "
            f"(so a deploy-stg FAIL is the live-LLM path, not the template): "
            f"{result.get('status')} / {result.get('error_log')}"
        )
        assert isinstance(result.get("output"), dict)
