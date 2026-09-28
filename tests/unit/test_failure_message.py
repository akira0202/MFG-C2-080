# MFG-C2-080 — Unit Tests: caller-facing wording for a failed run
#
# A failed run reaches the caller naming only the exception type, so without these the caller
# cannot tell an input they can fix from a deployment condition they cannot. The reason text is
# reused from the input gate, so the load-bearing test is that no request value ever reaches it.

import json

import pytest

from src.nodes.failure_mode_normalize_node import FailureModeNormalizeNode
from src.nodes.input_validate_node import InputValidateNode
from src.services.failure_message import GENERATION_UNAVAILABLE, explain

events = pytest.importorskip("shared.services.events")

_MARKER = "ZZSECRETMARKERZZ"


class _Recorder:
    def __init__(self):
        self.sent = []

    def emit_event(self, *, event_type, message, metadata=None, sub_event_type=None):
        self.sent.append(message)


def _reject_messages(user_input):
    recorder = _Recorder()
    with events.bound_emitter(recorder):
        out = InputValidateNode().execute({"user_input": user_input, "correlation_id": "c"})
    # the phase labels end in "..."; the rejection sentence is the one that does not
    return out, [m for m in recorder.sent if not m.endswith("...")]


@pytest.mark.parametrize(
    "user_input,expected",
    [
        ("not json at all", "not valid JSON"),
        ('["a"]', "must be a JSON object, not a list"),
        (
            json.dumps({"equipment_name": "x", "process_description": "y", "failure_events": ["e"]}),
            "equipment_category is missing from the request",
        ),
        (
            json.dumps(
                {
                    "equipment_name": "x",
                    "equipment_category": "general_equipment",
                    "process_description": "y",
                    "failure_events": [],
                }
            ),
            "failure_events must not be empty",
        ),
        (
            json.dumps(
                {
                    "equipment_name": "x",
                    "equipment_category": "general_equipment",
                    "process_description": "y",
                    "failure_events": "e",
                }
            ),
            "failure_events must be a list",
        ),
    ],
)
def test_each_rejection_names_what_to_change(user_input, expected):
    """The sentence is the point here, not the status: each of these requests is one the caller can
    correct, so the run completes carrying its code and the sentence names what to change."""
    out, sent = _reject_messages(user_input)
    assert out["status"] == "success"
    assert out["diagnostic_code"]
    assert len(sent) == 1
    assert expected in sent[0]


@pytest.mark.parametrize(
    "user_input",
    [
        _MARKER,
        json.dumps(
            {
                "equipment_name": _MARKER,
                "equipment_category": _MARKER,
                "process_description": _MARKER,
                "failure_events": [_MARKER],
            }
        ),
        json.dumps(
            {
                "equipment_name": "x",
                "equipment_category": "general_equipment",
                "process_description": "y",
                "failure_events": _MARKER,
            }
        ),
    ],
)
def test_no_request_value_reaches_the_caller_message(user_input):
    # The reason text is reused from the input gate, so this is what keeps that reuse safe:
    # a value the caller sent must never come back inside the message.
    _, sent = _reject_messages(user_input)
    assert all(_MARKER not in m for m in sent)


def test_wording_is_written_for_the_caller_not_the_operator():
    # the node's own reason text names internal state keys and design terms; the caller-facing
    # sentence must not carry them through
    import src.services.failure_message as mod

    sentences = (
        list(mod._EXACT.values())
        + list(mod._OVERRIDES.values())
        + [
            mod._FALLBACK,
            GENERATION_UNAVAILABLE,
        ]
    )
    for sentence in sentences:
        assert "user_input" not in sentence
        assert "fail closed" not in sentence
        assert "{" not in sentence and "}" not in sentence


def test_unknown_code_still_returns_an_actionable_sentence():
    assert "Check the documented request shape" in explain("something_new")


def test_generation_failure_says_it_is_not_the_callers_input():
    recorder = _Recorder()
    with events.bound_emitter(recorder):
        out = FailureModeNormalizeNode(llm=None).execute(
            {"validated_input": {"failure_events": ["Belt slips"]}, "correlation_id": "c"}
        )
    assert out["status"] == "error"
    assert GENERATION_UNAVAILABLE in recorder.sent
    assert "not something to change in your request" in GENERATION_UNAVAILABLE
