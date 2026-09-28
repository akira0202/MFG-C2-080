"""AgentCore Platform v1.0 — caller-facing wording for a failed run.

A failed run reaches the caller as a terminal failure whose text the platform runner produces: it
names the exception type and nothing else. The rejecting node therefore sends the reason itself, as
a non-terminal event, before returning the error — and it has to happen there, because once a run
carries ``status: error`` the framework skips ``execute()`` on every later node.

The wording is written for the person who sent the request, so it does not reuse the node's own
reason text: that text is written for an operator reading a log and names internal state keys and
design terms. Each sentence says what to change, or says the condition is not theirs to change, so
a caller does not keep editing a request that was never the problem.

``explain`` derives the sentence from the allowlisted rejection code the node already computes for
its audit event. Only the field name carried in that code is ever substituted — the field name the
caller themselves sent — so no request value can appear. ``test_failure_message`` asserts it.
"""

_EXACT: dict[str, str] = {
    "empty_or_missing": "No request content was received. Send the request as a JSON object.",
    "malformed": "The request is not valid JSON. Check for a missing quote, comma, or brace.",
    "malformed_json": "The request is not valid JSON. Check for a missing quote, comma, or brace.",
    "not_object": "The request must be a JSON object, not a list or a plain value.",
    "not_json_safe": "The request holds a value that cannot be represented in JSON.",
    "too_deep": "The request is nested too deeply. Flatten the structure and send it again.",
    "shape": "The request does not match the documented shape.",
    "contract_violation": "The request does not match the documented shape.",
    "no_rows": "The request carries no data rows.",
    "s2_oversized": "The request is too large. Send less content in one request.",
    "s2_non_text": "The request must be text. Send it as a JSON object, not binary content.",
    "s2_non_text_type": "The request must be text. Send it as a JSON object, not binary content.",
    # This agent's own rejections that the caller can act on. The two below name content the
    # request itself carried; everything else that can fail here comes from a step inside the run.
    "no_failure_events": "The request lists no failure events. Include at least one so there is something to analyse.",
    "missing_category": "The request does not name an equipment category. Include it so the safety gate can be applied.",
}

# Reached when a step inside the run did not receive what the previous one should have produced,
# or when a computed value is not anchored to the table being built. None of it is anything the
# caller sent, so these are answered as a service-side condition. Listed explicitly because the
# `missing_` prefix rule would otherwise render "input is missing from the request" — naming a
# handoff between nodes as though it were a field of the request.
_INTERNAL_CODES: frozenset[str] = frozenset(
    {
        "output_gated",
        "missing_input",
        "empty_handoff",
        "unanchored_input",
    }
)

# (suffix, sentence) — the field name is whatever precedes the suffix in the code.
_SUFFIX: tuple[tuple[str, str], ...] = (
    ("_not_object", "{f} must be a JSON object, not a list or a plain value."),
    ("_not_list", "{f} must be a list."),
    ("_not_dict", "{f} must be an object."),
    ("_too_long", "{f} is too long. Shorten it and send it again."),
    ("_too_many", "{f} has too many entries. Send fewer of them."),
    ("_not_allowed", "{f} is outside what this caller is allowed to use."),
    ("_unresolved", "{f} could not be resolved. Check that it refers to something in the request."),
    ("_injection", "{f} holds characters that are not allowed. Send it as plain text."),
    ("_missing", "{f} is missing from the request."),
    ("_invalid", "{f} could not be accepted. Check its value against the documented shape."),
    ("_empty", "{f} must not be empty."),
    ("_shape", "{f} does not match the documented shape."),
    ("_type", "{f} has the wrong type."),
    ("_parse", "The request is not valid JSON. Check for a missing quote, comma, or brace."),
    ("_required", "{f} is required in the request."),
    ("_version", "{f} does not match a version this agent accepts."),
    ("_mismatch", "{f} does not match the rest of the request."),
    ("_duplicate", "{f} is duplicated in the request."),
)

_PREFIX: tuple[tuple[str, str], ...] = (
    ("missing_", "{f} is missing from the request."),
    ("duplicate_", "{f} is duplicated in the request."),
    ("unresolved_", "{f} could not be resolved. Check that it refers to something in the request."),
    ("unknown_", "{f} is not one of the accepted values."),
)

# Codes whose shared wording would name the wrong thing or miss the operator-action hint.
_OVERRIDES: dict[str, str] = {
    "safety_ack_required": (
        "This equipment category needs a safety review acknowledgement before a draft can be "
        "produced. Ask the operator to enable it."
    ),
    "safety_ack_type": "The safety review acknowledgement setting is invalid. Ask the operator to check it.",
    "failure_events_type": "failure_events must be a list.",
    "failure_events_item": "An entry in failure_events could not be accepted. Send each entry as plain text.",
    "failure_events_item_invalid": (
        "An entry in failure_events could not be accepted: it holds characters that are not "
        "allowed. Send each entry as plain text."
    ),
    "failure_events_item_too_long": "An entry in failure_events is too long. Shorten it and send it again.",
}

_FALLBACK = "The request could not be accepted. Check the documented request shape."

# Codes that name a processing failure, not a defect in the request. They must not fall back to the
# sentence above: it tells the caller to edit a request that was never the problem.
_INTERNAL_SUFFIXES = ("_error", "_failed", "_blocked", "_unavailable", "_unparseable")
_INTERNAL = (
    "The request could not be processed. This is a condition on the service side, not something to "
    "change in your request."
)

# Sent by the generation steps.
GENERATION_UNAVAILABLE = "The FMEA draft could not be generated: the language model is unavailable. This is a deployment setting, not something to change in your request."


def _normalise(reason: str) -> str:
    """Codes are written in more than one house style across templates: lower_snake in most,
    ``ERR_UPPER_SNAKE`` in others. Reduce both to the same form before matching."""
    code = reason.strip().lower()
    for prefix in ("err_", "error_", "reject_"):
        if code.startswith(prefix):
            code = code[len(prefix) :]
    return code


def explain(reason: str) -> str:
    """Return one caller-facing sentence for an allowlisted rejection code.

    Where the sentence has to stay general, the code itself is appended: it is the one handle the
    caller can quote to whoever runs the deployment, and it is an allowlisted token, so it carries
    no request value.
    """
    code = reason.strip()
    reason = _normalise(reason)
    if reason in _OVERRIDES:
        return _OVERRIDES[reason]
    if reason in _EXACT:
        return _EXACT[reason]
    if reason in _INTERNAL_CODES:
        return f"{_INTERNAL} [{code}]"
    for suffix, sentence in _SUFFIX:
        if reason.endswith(suffix) and len(reason) > len(suffix):
            return sentence.format(f=reason[: -len(suffix)])
    for prefix, sentence in _PREFIX:
        if reason.startswith(prefix) and len(reason) > len(prefix):
            return sentence.format(f=reason[len(prefix) :])
    if reason.endswith(_INTERNAL_SUFFIXES):
        return f"{_INTERNAL} [{code}]"
    return f"{_FALLBACK} [{code}]"
