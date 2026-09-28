"""AgentCore Platform v1.0 — which rejections are answered, and which stay fail-closed.

A run that ends without an FMEA table is reported one of two ways, and the split is by **whose
problem it is**. A request the caller can correct, and a draft this agent withholds on policy,
both complete carrying an allow-listed ``diagnostic_code``; the caller-facing sentence is the one
``failure_message.explain()`` already derives from that code, so this module keeps no second
catalogue of wording. A refusal or a fault this agent owns stays ``status=ERROR`` with no output.

**The line this template draws.** The screen that decides whether the body is a document at all —
non-text input, a NUL byte, a flood of control characters — is a refusal and stays one: it exists
so such a payload is never parsed. Everything decided *about a parsed request* is the caller's to
correct: the ceiling on how much can be sent at once, a body that is not a JSON object, the
required equipment name / process description / category / failure events, the closed category
vocabulary, and the per-field length and character contracts. Those name something the caller can
change and send again — including a control character inside one field, which is a contract on a
value in a body this agent has already accepted as text.

**Withheld on policy, not refused.** ``safety_ack_required`` and ``safety_ack_type`` are not
defects in the request. A safety-critical equipment category is admitted only where the deployment
has recorded a safety-review acknowledgement, so the answer names what the operator has to enable.
Reported as an error it would read as a malfunction; reported as a notice it says what it is — a
draft deliberately not produced. It is still not produced: nothing about answering it admits the
request.

**Fail-closed (unchanged).**

- ``s2_non_text`` / ``s2_non_text_type`` — the document screen above.
- ``output_gated`` — the terminal S-3 gate (proprietary / confidential markers in the composed
  table). Nothing about the request explains it and no output may leave.
- Everything inside the workflow: the handoffs (``empty_handoff``, ``malformed``, ``shape``, the
  ``missing_*`` inputs between nodes), an unusable LLM (``llm_unavailable`` and the extraction
  faults behind it), ``unanchored_input``, and the rating / RPN / rendering faults
  (``rpn_calculate_failed``, ``countermeasure_classify_failed``, ``fmea_table_failed``). These are
  this deployment's configuration or this agent's own invariants.

Only ``pre_process`` degrades, so no marker has to cross the inner graph boundary in this template
— ``main`` simply skips the workflow when the gate has already answered.
"""

from __future__ import annotations

from src.services.failure_message import explain

# Conditions about a request that was read and found correctable.
CALLER_CORRECTABLE_CODES = frozenset(
    {
        "s2_oversized",
        "empty_or_missing",
        "malformed",
        "too_deep",
        "not_object",
        "missing_equipment_name",
        "missing_process_description",
        "equipment_name_invalid",
        "process_description_too_long",
        "process_description_invalid",
        "missing_equipment_category",
        "unknown_equipment_category",
        "missing_failure_events",
        "failure_events_type",
        "failure_events_empty",
        "failure_events_too_many",
        "failure_events_item",
        "failure_events_item_too_long",
        "failure_events_item_invalid",
    }
)

# Drafts this agent declines to produce on policy. Answered, not refused — and not admitted.
POLICY_WITHHELD_CODES = frozenset(
    {
        "safety_ack_required",
        "safety_ack_type",
    }
)

_ANSWERED = CALLER_CORRECTABLE_CODES | POLICY_WITHHELD_CODES

_FALLBACK = "The request could not be completed and no FMEA draft was produced."


def is_caller_correctable(code: str) -> bool:
    """True when the run completes carrying this code rather than failing closed.

    Covers both what the caller can change and what this agent withholds on policy: neither is a
    malfunction, and both are answered with the sentence the code names.
    """
    return code in _ANSWERED


def is_policy_withheld(code: str) -> bool:
    """True when the draft was deliberately not produced, rather than the request being at fault."""
    return code in POLICY_WITHHELD_CODES


def explain_degradation(code: str) -> str:
    """Return the caller-facing sentence for a degradation code.

    Delegates to ``failure_message.explain()``, which derives the sentence from the allow-listed
    code alone — only the field name the code itself carries is ever substituted, so no request
    value, equipment name or failure-event text can appear.
    """
    return explain(code) if code else _FALLBACK
