"""Unit tests for the provisional Stage 5 bounded-fake profile — MFG-C2-080.

The provisional Stage 5 job boots this template in CI and performs one first invoke.
`failure_mode_normalize` and `sod_rating_compute` are unconditional, fail-closed steps, so that
invoke runs against a bounded fake provider rather than a live one. These tests pin what the fake
must prove — every LLM-boundary validator still executes for real — and what it must never do: be
reachable from a normal deployment, or let an unvalidated response through.

The fake is injected through the node constructor here. The CI environment flag is exercised only
in the tests that assert profile selection itself.
"""

import pytest

from framework.schemas.agent_status import AgentStatus
from src.api import startup as startup_mod
from src.nodes.countermeasure_generate_node import CountermeasureGenerateNode
from src.nodes.failure_mode_normalize_node import FailureModeNormalizeNode
from src.nodes.sod_rating_compute_node import SodRatingComputeNode
from src.services import rpn_calculation
from src.services.domain_errors import DomainComputeError
from src.services.failure_mode_normalization import parse_and_validate_failure_modes
from src.services.stage5_fakes import FakeFMEALLM

_VALIDATED_INPUT = {
    "equipment_name": "General Assembly Conveyor",
    "equipment_category": "general_equipment",
    "process_description": "Belt conveyor moving assembled units between two workstations.",
    "failure_events": [
        "Belt slips under load and units stop advancing",
        "Drive motor overheats after prolonged continuous running",
    ],
}


def _normalized_failure_modes() -> list:
    """Run the real normalization node with the fake and return its `failure_modes`."""
    result = FailureModeNormalizeNode(llm=FakeFMEALLM()).execute(
        {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
    )
    assert result["status"] == AgentStatus.SUCCESS.value
    return result["failure_modes"]


class TestNormalizeContractWithTheFake:
    """The fake is exercised through the real node — what matters is that
    `FailureModeNormalizeNode.execute()` behaves the same as it would with a live provider."""

    def test_success_path_returns_one_validated_row_per_failure_event(self):
        fake = FakeFMEALLM()
        result = FailureModeNormalizeNode(llm=fake).execute(
            {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
        )

        assert result["status"] == AgentStatus.SUCCESS.value
        assert set(result) == {"failure_modes", "status"}
        assert len(result["failure_modes"]) == len(_VALIDATED_INPUT["failure_events"])
        # fmea_id is stamped by the real validator, not carried in from the fake's response
        assert [row["fmea_id"] for row in result["failure_modes"]] == [0, 1]

    def test_the_node_reaches_the_provider_boundary_with_the_built_messages(self):
        fake = FakeFMEALLM()
        FailureModeNormalizeNode(llm=fake).execute(
            {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
        )

        assert len(fake.calls) == 1
        assert fake.calls[0][0]["role"] == "system"
        assert fake.calls[0][1]["role"] == "user"

    def test_a_raising_provider_yields_error_and_no_failure_modes(self):
        fake = FakeFMEALLM(raise_on_complete=RuntimeError("provider 503"))
        result = FailureModeNormalizeNode(llm=fake).execute(
            {"validated_input": _VALIDATED_INPUT, "correlation_id": "stage5-bounded-fake"}
        )

        assert result["status"] == AgentStatus.ERROR.value
        assert "failure_modes" not in result

    def test_a_row_count_mismatch_still_fails(self):
        """Guards the guard: the validator is doing real work, not accepting anything the fake
        happens to return."""
        with pytest.raises(DomainComputeError):
            parse_and_validate_failure_modes('[{"failure_mode": "only one row"}]', expected_count=2)


class TestSodContractWithTheFake:
    def test_ratings_are_a_bijection_with_the_input_failure_modes(self):
        failure_modes = _normalized_failure_modes()
        result = SodRatingComputeNode(llm=FakeFMEALLM()).execute(
            {"failure_modes": failure_modes, "correlation_id": "stage5-bounded-fake"}
        )

        assert result["status"] == AgentStatus.SUCCESS.value
        assert [r["fmea_id"] for r in result["sod_ratings"]] == [fm["fmea_id"] for fm in failure_modes]
        # every rating carries the approval marker the real validator stamps
        assert all(r["approval_required"] is True for r in result["sod_ratings"])

    def test_the_synthetic_ratings_produce_one_high_priority_mode(self):
        """The fake rates the first mode above the RPN threshold on purpose, so a single Stage 5
        invoke exercises the high-priority branch (countermeasure generation) as well as the low
        one. If this stops holding, Stage 5 silently stops covering that branch."""
        failure_modes = _normalized_failure_modes()
        sod = SodRatingComputeNode(llm=FakeFMEALLM()).execute(
            {"failure_modes": failure_modes, "correlation_id": "stage5-bounded-fake"}
        )["sod_ratings"]

        rpn_results = rpn_calculation.calculate(sod)

        assert [r["high_priority"] for r in rpn_results] == [True, False]

    def test_ratings_stay_inside_a_stricter_rating_scale(self):
        """On iso_9001_simplified the scale max is 5; ratings must be clamped or the real
        validator rejects them."""
        failure_modes = _normalized_failure_modes()
        result = SodRatingComputeNode(
            node_config={"rating_scale": "iso_9001_simplified"}, llm=FakeFMEALLM()
        ).execute({"failure_modes": failure_modes, "correlation_id": "stage5-bounded-fake"})

        assert result["status"] == AgentStatus.SUCCESS.value
        assert all(1 <= r["severity"] <= 5 for r in result["sod_ratings"])


class TestCountermeasureContractWithTheFake:
    def test_a_control_is_proposed_for_every_high_priority_mode(self):
        failure_modes = _normalized_failure_modes()
        sod = SodRatingComputeNode(llm=FakeFMEALLM()).execute(
            {"failure_modes": failure_modes, "correlation_id": "stage5-bounded-fake"}
        )["sod_ratings"]
        rpn_results = rpn_calculation.calculate(sod)

        result = CountermeasureGenerateNode(llm=FakeFMEALLM()).execute(
            {
                "failure_modes": failure_modes,
                "rpn_results": rpn_results,
                "correlation_id": "stage5-bounded-fake",
            }
        )

        assert result["status"] == AgentStatus.SUCCESS.value
        controlled = [fm for fm in result["failure_modes"] if "recommended_control" in fm]
        assert [fm["fmea_id"] for fm in controlled] == [
            r["fmea_id"] for r in rpn_results if r["high_priority"]
        ]


class TestProfileSelection:
    def test_disabled_without_the_ci_flag(self, monkeypatch):
        monkeypatch.delenv("AGENT_STAGE5_BOUNDED_FAKE", raising=False)
        assert startup_mod.bounded_fake_profile_enabled() is False

    def test_enabled_only_by_the_exact_flag_value(self, monkeypatch):
        monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "true")
        assert startup_mod.bounded_fake_profile_enabled() is False
        monkeypatch.setenv("AGENT_STAGE5_BOUNDED_FAKE", "1")
        assert startup_mod.bounded_fake_profile_enabled() is True

    def test_profile_supplies_the_fake_through_the_config_channel(self):
        """`llm` is the same config key any caller would use; graph.py already prefers a supplied
        adapter over its default, so the profile needs no bespoke constructor keyword."""
        overlay = startup_mod.bounded_fake_profile()

        assert isinstance(overlay["llm"], FakeFMEALLM)
        assert set(overlay) == {"llm"}

    def test_shipped_config_carries_no_llm_key(self):
        """Nothing in the runtime config can select the fake: a deployment always gets the default
        provider adapter the graph injects."""
        assert "llm" not in startup_mod.load_runtime_config()
