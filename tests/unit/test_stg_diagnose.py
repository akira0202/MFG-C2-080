"""Regression: the Stage 5 failure diagnostic never leaks an error_log body to the CI output.

`scripts/stg_diagnose.classify()` must reduce any node error_log entry to an allowlisted
`NodeName: ErrorClass` label. Even when a node stringifies an exception body — a message, a
fragment of business/equipment input, or a credential-like string — none of that content may
appear in the emitted classification.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import stg_diagnose  # noqa: E402


class TestDiagnoseSanitization:
    def test_bare_exception_class_is_kept(self):
        # the safe, useful case: node + bare exception class name (as the LLM nodes emit)
        assert stg_diagnose.classify("FailureModeNormalizeNode: ConnectionError") == \
            "FailureModeNormalizeNode: ConnectionError"
        assert stg_diagnose.classify("FailureModeNormalizeNode: SecretResolutionError") == \
            "FailureModeNormalizeNode: SecretResolutionError"

    def test_message_body_is_collapsed_to_unknown_error(self):
        out = stg_diagnose.classify("RpnCalculateNode: division by zero at row 3 with rpn 12345")
        assert out == "RpnCalculateNode: unknown_error"
        assert "division" not in out and "12345" not in out

    def test_credential_like_body_never_appears(self):
        secret = "sk-live-ABCDEF0123456789 Bearer eyJhbGciOi.token.value"
        out = stg_diagnose.classify(f"CountermeasureClassifyNode: {secret}")
        assert out == "CountermeasureClassifyNode: unknown_error"
        assert "sk-live" not in out and "Bearer" not in out and "eyJ" not in out

    def test_business_input_fragment_never_appears(self):
        out = stg_diagnose.classify("FmeaTableGenerateNode: bad row {'equipment_name': '500t Press'}")
        assert out == "FmeaTableGenerateNode: unknown_error"
        assert "equipment_name" not in out and "Press" not in out

    def test_non_identifier_prefix_is_unknown_node(self):
        assert stg_diagnose.classify("no colon here at all") == "unknown_node: unknown_error"
        assert stg_diagnose.classify("weird prefix: ValueError").startswith("unknown_node: ")

    def test_non_string_entry_is_safe(self):
        assert stg_diagnose.classify({"secret": "x"}) == "unknown_node: unknown_error"
        assert stg_diagnose.classify(None) == "unknown_node: unknown_error"

    def test_status_token_only_bare_values(self):
        assert stg_diagnose._status_token("error") == "error"
        assert stg_diagnose._status_token("weird value with data 123") == "unknown_status"
        assert stg_diagnose._status_token(None) == "unknown_status"
