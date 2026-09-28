"""Unit tests for the MFG deterministic FMEA services (framework-free)."""

import json

import pytest

from src.services import (rpn_calculation as RPN, countermeasure_classification as CC,
                          safety_classification as SC, fmea_rendering as FR,
                          failure_mode_normalization as FMN, sod_rating as SOD,
                          audit_metadata as AM)
from src.services.domain_errors import DomainComputeError


def _ok(s, o, d):
    """A well-formed SOD rating carrying the required pending-approval marker."""
    return {"severity": s, "occurrence": o, "detection": d, "approval_required": True}


class TestRPN:
    def test_rpn_is_s_times_o_times_d(self):
        r = RPN.calculate([_ok(8, 5, 4)])
        assert r[0]["rpn"] == 160

    def test_high_priority_flag(self):
        r = RPN.calculate([_ok(8, 5, 4), _ok(2, 2, 2)], high_priority_threshold=100)
        assert r[0]["high_priority"] is True and r[1]["high_priority"] is False

    def test_out_of_scale_range_rejected(self):
        with pytest.raises(DomainComputeError):
            RPN.calculate([_ok(11, 1, 1)], rating_scale="iatf_16949")

    def test_iso_scale_range(self):
        with pytest.raises(DomainComputeError):
            RPN.calculate([_ok(6, 1, 1)], rating_scale="iso_9001_simplified")

    def test_missing_rating_rejected(self):
        with pytest.raises(DomainComputeError):
            RPN.calculate([{"severity": 8, "occurrence": 5, "approval_required": True}])

    def test_unknown_rating_scale_rejected(self):
        with pytest.raises(DomainComputeError):  # not silently coerced to a permissive max
            RPN.calculate([_ok(5, 1, 1)], rating_scale="custom")

    def test_high_priority_threshold_inclusive(self):
        # threshold is inclusive: RPN == threshold → high; RPN just below → not.
        rating = [_ok(8, 5, 4)]  # RPN = 160
        assert RPN.calculate(rating, high_priority_threshold=160)[0]["high_priority"] is True
        assert RPN.calculate(rating, high_priority_threshold=161)[0]["high_priority"] is False

    def test_empty_rejected(self):
        with pytest.raises(DomainComputeError):
            RPN.calculate([])

    def test_missing_approval_required_rejected(self):
        with pytest.raises(DomainComputeError):
            RPN.calculate([{"severity": 8, "occurrence": 5, "detection": 4}])

    def test_false_approval_required_rejected(self):
        with pytest.raises(DomainComputeError):
            RPN.calculate([{"severity": 8, "occurrence": 5, "detection": 4, "approval_required": False}])

    def test_true_approval_required_accepted(self):
        assert RPN.calculate([_ok(8, 5, 4)])[0]["rpn"] == 160


class TestCountermeasureClassification:
    def test_detective(self):
        assert CC.classify([{"recommended_control": "install a sensor to detect overheating"}])[0]["type"] == "Detective"

    def test_preventive(self):
        assert CC.classify([{"recommended_control": "design out via poka-yoke"}])[0]["type"] == "Preventive"

    def test_corrective(self):
        assert CC.classify([{"recommended_control": "rework the part"}])[0]["type"] == "Corrective"

    def test_unmatched_defaults_corrective(self):
        assert CC.classify([{"recommended_control": "do something unspecified"}])[0]["type"] == "Corrective"

    def test_no_control_unclassified(self):
        assert CC.classify([{"failure_mode": "x"}])[0]["type"] == "Unclassified"

    def test_malformed_member_rejected(self):
        with pytest.raises(DomainComputeError):
            CC.classify([{"recommended_control": "rework"}, "not a dict"])

    def test_malformed_control_type_rejected(self):
        with pytest.raises(DomainComputeError):
            CC.classify([{"recommended_control": {"bad": 1}}])

    def test_malformed_member_cannot_shift_alignment(self):
        # a bad member must fail closed — never silently drop and shift the positional row-to-control
        # join (which would associate a control with the wrong failure mode)
        with pytest.raises(DomainComputeError):
            CC.classify([{"failure_mode": "a", "recommended_control": "inspect"}, 12345,
                         {"failure_mode": "c", "recommended_control": "rework"}])

    def test_one_entry_per_failure_mode(self):
        out = CC.classify([{"failure_mode": "a", "recommended_control": "inspect"},
                           {"failure_mode": "b"}])  # second has no control → Unclassified
        assert len(out) == 2 and out[1]["type"] == "Unclassified"


class TestSafetyClassification:
    def test_safety_critical_blocks(self):
        g = SC.build_safety_gate("hydraulic_press", ["hydraulic_press"])
        assert g["safety_critical"] and g["blocked"] and g["warning"]

    def test_non_critical_still_review_required(self):
        # V1 conservative: a non-critical category is NOT safety_critical, but its FMEA is STILL
        # review-required (blocked) — no caller-declared category grants auto-approval.
        g = SC.build_safety_gate("general_equipment", ["hydraulic_press"])
        assert g["safety_critical"] is False and g["blocked"] is True and g["warning"]

    def test_classifies_by_token_not_maskable_name(self):
        # the safety decision keys on the equipment_category TOKEN, never the free-text name the
        # S-2 baseline can mask ("500t Hydraulic Press" -> "500t [MASKED]")
        assert SC.is_safety_critical("hydraulic_press", ["hydraulic_press"]) is True
        assert SC.is_safety_critical("500t [MASKED]", ["hydraulic_press"]) is False


class TestFMEARendering:
    def _fixture(self):
        r = [{"fmea_id": 0, "severity": 8, "occurrence": 5, "detection": 4, "source_failure_mode": "crack", "rpn": 160, "high_priority": True},
             {"fmea_id": 1, "severity": 2, "occurrence": 2, "detection": 2, "source_failure_mode": "minor", "rpn": 8, "high_priority": False}]
        cms = [{"fmea_id": 0, "control": "add sensor", "type": "Detective"}, {"fmea_id": 1, "control": "-", "type": "Unclassified"}]
        gate = {"safety_critical": True, "warning": "review", "blocked": True}
        return r, cms, gate

    def test_markdown_ranked_high_rpn_first(self):
        r, cms, gate = self._fixture()
        tbl = FR.render(r, cms, gate, output_format="markdown")
        assert tbl.index("crack") < tbl.index("minor")

    def test_safety_banner_and_eu_block(self):
        r, cms, gate = self._fixture()
        tbl = FR.render(r, cms, gate, output_format="markdown", eu_ai_act_art9_mode=True)
        assert "REVIEW REQUIRED" in tbl and "Article 9" in tbl

    def test_csv_and_json(self):
        r, cms, gate = self._fixture()
        assert "Failure Mode,Severity" in FR.render(r, cms, gate, output_format="csv")
        assert '"rows"' in FR.render(r, cms, gate, output_format="json")

    def test_provisional_disclaimer_on_every_output(self):
        # non-safety-critical, non-EU-mode → no safety banner / Art.9 block, but the provisional
        # "pending qualified-engineer approval" marker must still surface in every format.
        r = [{"fmea_id": 0, "severity": 2, "occurrence": 2, "detection": 2, "source_failure_mode": "minor",
              "rpn": 8, "high_priority": False}]
        cms = [{"fmea_id": 0, "control": "inspect", "type": "Detective"}]
        gate = {"safety_critical": False, "warning": None, "blocked": False}
        for fmt in ("markdown", "csv"):
            out = FR.render(r, cms, gate, output_format=fmt)
            assert "Provisional" in out and "REVIEW REQUIRED" not in out
        j = json.loads(FR.render(r, cms, gate, output_format="json"))
        assert "Provisional" in j["disclaimer"]


class TestRowAlignment:
    """LLM rows are joined by a stable fmea_id, never by array position — a dropped / added /
    reordered / duplicated row must fail closed, never mis-associate SOD/RPN/controls with another
    failure event (safety-related manufacturing output)."""

    # --- normalize: exactly one row per failure event ---
    def test_normalize_row_count_must_match_events(self):
        two = json.dumps([{"function": "f", "failure_mode": "a", "effect": "e", "cause": "c"},
                          {"function": "f", "failure_mode": "b", "effect": "e", "cause": "c"}])
        with pytest.raises(DomainComputeError):   # 2 rows for 3 events (a dropped row)
            FMN.parse_and_validate_failure_modes(two, expected_count=3)

    def test_normalize_stamps_stable_ids(self):
        two = json.dumps([{"function": "f", "failure_mode": "a", "effect": "e", "cause": "c"},
                          {"function": "f", "failure_mode": "b", "effect": "e", "cause": "c"}])
        out = FMN.parse_and_validate_failure_modes(two, expected_count=2)
        assert [m["fmea_id"] for m in out] == [0, 1]

    # --- sod: bijection with the input failure-mode ids ---
    def _sod(self, rows):
        return SOD.parse_and_validate_sod(json.dumps(rows), "iatf_16949", expected_ids=[0, 1])

    def test_sod_dropped_id_rejected(self):
        with pytest.raises(DomainComputeError):  # only id 0 returned for ids [0,1]
            self._sod([{"fmea_id": 0, "severity": 3, "occurrence": 3, "detection": 3}])

    def test_sod_foreign_id_rejected(self):
        with pytest.raises(DomainComputeError):  # id 9 is not an input id
            self._sod([{"fmea_id": 0, "severity": 3, "occurrence": 3, "detection": 3},
                       {"fmea_id": 9, "severity": 3, "occurrence": 3, "detection": 3}])

    def test_sod_duplicate_id_rejected(self):
        with pytest.raises(DomainComputeError):  # id 0 twice, id 1 missing
            self._sod([{"fmea_id": 0, "severity": 3, "occurrence": 3, "detection": 3},
                       {"fmea_id": 0, "severity": 3, "occurrence": 3, "detection": 3}])

    def test_sod_reordered_ids_ok_and_stamped(self):
        out = self._sod([{"fmea_id": 1, "severity": 4, "occurrence": 4, "detection": 4},
                         {"fmea_id": 0, "severity": 3, "occurrence": 3, "detection": 3}])
        assert {r["fmea_id"] for r in out} == {0, 1} and all(r["approval_required"] for r in out)

    # --- render: join by id, fail closed on a mismatched id set ---
    def test_render_missing_id_rejected(self):
        r = [{"fmea_id": 0, "severity": 8, "occurrence": 5, "detection": 4, "rpn": 160, "high_priority": True}]
        cms = [{"control": "x", "type": "Detective"}]  # no fmea_id
        with pytest.raises(DomainComputeError):
            FR.render(r, cms, {"blocked": False})

    def test_render_mismatched_id_set_rejected(self):
        r = [{"fmea_id": 0, "severity": 8, "occurrence": 5, "detection": 4, "rpn": 160, "high_priority": True}]
        cms = [{"fmea_id": 7, "control": "x", "type": "Detective"}]  # control for a different failure mode
        with pytest.raises(DomainComputeError):
            FR.render(r, cms, {"blocked": False})

    def test_render_duplicate_id_rejected(self):
        r = [{"fmea_id": 0, "severity": 8, "occurrence": 5, "detection": 4, "rpn": 160, "high_priority": True},
             {"fmea_id": 0, "severity": 2, "occurrence": 2, "detection": 2, "rpn": 8, "high_priority": False}]
        cms = [{"fmea_id": 0, "control": "x", "type": "Detective"}]
        with pytest.raises(DomainComputeError):
            FR.render(r, cms, {"blocked": False})

    def test_render_joins_by_id_not_position(self):
        # rpn order [1,0] vs controls order [0,1] — a positional join would swap controls; id-join must not
        r = [{"fmea_id": 1, "severity": 2, "occurrence": 2, "detection": 2, "rpn": 8, "high_priority": False, "source_failure_mode": "minor"},
             {"fmea_id": 0, "severity": 9, "occurrence": 9, "detection": 9, "rpn": 729, "high_priority": True, "source_failure_mode": "crack"}]
        cms = [{"fmea_id": 0, "control": "fix crack", "type": "Corrective", "source_failure_mode": "crack"},
               {"fmea_id": 1, "control": "watch minor", "type": "Detective", "source_failure_mode": "minor"}]
        out = json.loads(FR.render(r, cms, {"blocked": False}, output_format="json"))
        by_fm = {row["Failure Mode"]: row["Recommended Control"] for row in out["rows"]}
        assert by_fm["crack"] == "fix crack" and by_fm["minor"] == "watch minor"

    # --- deterministic carry of fmea_id ---
    def test_rpn_and_classify_carry_fmea_id(self):
        rpn = RPN.calculate([{"fmea_id": 5, "severity": 3, "occurrence": 3, "detection": 3, "approval_required": True}])
        assert rpn[0]["fmea_id"] == 5
        cm = CC.classify([{"fmea_id": 5, "failure_mode": "x", "recommended_control": "inspect"}])
        assert cm[0]["fmea_id"] == 5


class TestCountermeasureGeneration:
    """LLM proposes one advisory control per HIGH-priority failure mode, bijective on fmea_id."""

    def _ok(self, rows):
        from src.services import countermeasure_generation as CG
        import json
        return CG.parse_and_validate_controls(json.dumps(rows), expected_ids=[0, 1])

    def test_bijection_ok(self):
        out = self._ok([{"fmea_id": 0, "recommended_control": "Add interlock (review)"},
                        {"fmea_id": 1, "recommended_control": "Inspect weld (review)"}])
        assert out == {0: "Add interlock (review)", 1: "Inspect weld (review)"}

    def test_reordered_ids_ok(self):
        out = self._ok([{"fmea_id": 1, "recommended_control": "b"}, {"fmea_id": 0, "recommended_control": "a"}])
        assert set(out) == {0, 1}

    def test_dropped_id_fails_closed(self):
        from src.services.domain_errors import DomainComputeError
        with pytest.raises(DomainComputeError):
            self._ok([{"fmea_id": 0, "recommended_control": "a"}])

    def test_foreign_id_fails_closed(self):
        from src.services.domain_errors import DomainComputeError
        with pytest.raises(DomainComputeError):
            self._ok([{"fmea_id": 0, "recommended_control": "a"}, {"fmea_id": 9, "recommended_control": "b"}])

    def test_control_with_control_chars_rejected(self):
        from src.services.domain_errors import DomainComputeError
        with pytest.raises(DomainComputeError):
            self._ok([{"fmea_id": 0, "recommended_control": "ok"},
                      {"fmea_id": 1, "recommended_control": "bad\x00ctrl"}])


class TestCountermeasureApproval:
    def test_proposed_control_marked_advisory(self):
        out = CC.classify([{"fmea_id": 0, "failure_mode": "crack", "recommended_control": "inspect weld"}])
        assert out[0]["type"] != "Unclassified" and out[0]["approval_required"] is True

    def test_no_control_not_approval_marked(self):
        out = CC.classify([{"fmea_id": 0, "failure_mode": "crack"}])  # no control
        assert out[0]["type"] == "Unclassified" and "approval_required" not in out[0]


class TestAuditMetadata:
    def test_hash_only_artifact(self):
        # the S-4 audit_metadata is a hash-only artifact — the raw FMEA table body is never persisted,
        # only its content hash; rating scale / model version are carried for the audit trail. It is
        # always produced (this template has no external audit sink or config toggle).
        md = AM.build_audit_metadata(fmea_table="| Failure Mode | ... | secret weld spec |",
                                     safety_gate={"safety_critical": True, "blocked": False},
                                     rating_scale="iatf_16949", model_version="v1",
                                     generated_at="2025-01-01T00:00:00+09:00")
        assert set(md) == {"data_hash", "generated_at", "rating_scale", "model_version"}
        assert len(md["data_hash"]) == 64 and all(c in "0123456789abcdef" for c in md["data_hash"])
        assert md["rating_scale"] == "iatf_16949" and md["model_version"] == "v1"
        # the raw table body must never appear in the artifact (hash-only)
        assert "secret weld spec" not in json.dumps(md)
