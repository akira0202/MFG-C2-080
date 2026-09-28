"""AgentCore Platform v1.0 — InputValidateNode for MFG-C2-080 EquipmentFMEADraftGeneratorAgent."""

# Node contract:
#  - Extend FunctionNode; implement execute(state) -> dict
#  - Return ONLY the fields this node changes (never full state — error_log /
#    node_history carry operator.add reducers, so spreading state double-appends)
#  - Return AgentStatus.<X>.value for status — never the bare enum object
#  - Never import from mediator/, api/, or other agents
#
# Responsibility (docs/02_design.md — outer pre_process / InputValidateNode row):
#  - This is the OUTER pre_process node. It receives the framework-inherited
#    user_input: str (a JSON FMEA request), parses and validates it, resolves the
#    safety-acknowledgement precedence, and writes the canonical, msgpack-safe
#    validated_input. It does NOT build any inner domain value (failure_modes, SOD,
#    RPN, countermeasures, safety_gate, FMEA table, audit_metadata) and does NOT
#    touch result — those belong to the inner DomainWorkflowGraph. The main
#    GraphNode.extract_input() JSON-encodes validated_input into the inner user_input
#    string; the inner input_hydrate restores it (no duplicated S-2 there).
#  - S-2 lives in _extra_security_gate_input(): STRUCTURAL sanitization of the raw
#    string — non-str/binary/NUL/control-char and oversized payloads are rejected.
#    The framework @final _security_gate_input() (baseline PII scan) is NOT
#    overridden — that scan, plus the structured-only extraction below (only the
#    contract fields reach validated_input), is the raw-PII posture. Active per-value
#    PII pattern-matching is deliberately avoided so that legitimate equipment names
#    and technical terms are not false-positive-blocked. SEMANTIC prompt-injection
#    defense belongs at the inner LLM boundary (failure_mode_normalize), not here.
#  - execute() performs business validation only: require caller-input equipment_name
#    + equipment_category (a member of the closed code-side vocabulary; unknown/missing
#    fails closed) + process_description + non-empty failure_events (a list of non-empty
#    strings), naming the offending field on reject; resolve safety_review_acknowledged
#    (per-call value first, else the config default false); reject a safety-critical
#    equipment_category when the resolved value is not true (admission control); assemble
#    the canonical validated_input carrying equipment_category + the resolved value so the
#    inner safety_gate_check cannot drift. The safety decision keys on equipment_category
#    only — equipment_name is display-only (the S-2 baseline can mask it; docs/02 ADR-017).

import json
from typing import Any, ClassVar

from framework.errors import ConfigError
from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event
from src.services.degradation import is_caller_correctable
from src.services.failure_message import explain
from src.services.progress import emit_progress

# Mandatory caller-input fields (docs/02 input contract).
_REQUIRED_TEXT_FIELDS = ("equipment_name", "process_description")

# Defensive limits (overridable via node_config).
_DEFAULT_MAX_INPUT_CHARS = 1_000_000
_DEFAULT_MAX_EVENTS = 1_000
_EQUIPMENT_NAME_MAX = 256  # single-line display key (FMEA content — NOT used for the safety decision)
_TEXT_MAX = 20_000  # NL description bound

# Secure code-side SSOT for the MANDATORY safety gate. The safety-critical set lives in code
# (not operator-editable config) so it can never be dropped to fail-open. Classification keys on
# the structured equipment_category TOKEN — NEVER a substring of the free-text equipment_name,
# which the framework @final S-2 baseline can mask to "[MASKED]" before execute() (docs/02 ADR-017).
_DEFAULT_SAFETY_CRITICAL_CATEGORIES: tuple[str, ...] = (
    "hydraulic_press",
    "power_press",
    "robotic_arm",
    "industrial_robot",
    "plc",
    "scada",
    "conveyor",
    "overhead_crane",
    "boiler",
    "pressure_vessel",
)
# The only recognized non-safety-critical category. A caller-declared category is unverifiable
# (no trusted equipment catalog in V1), so general_equipment is admitted without acknowledgement,
# but its FMEA is STILL review-required downstream — safety_gate_check blocks auto-approval for
# every category, so mis-declaring a critical item as general_equipment gains no auto-approval.
_NON_CRITICAL_CATEGORIES: tuple[str, ...] = ("general_equipment",)
# Closed controlled vocabulary — equipment_category MUST be a member; unknown/missing fails closed.
_EQUIPMENT_CATEGORY_VOCABULARY: frozenset[str] = frozenset(
    _DEFAULT_SAFETY_CRITICAL_CATEGORIES + _NON_CRITICAL_CATEGORIES
)


def _screen_key(value: str, field: str, max_len: int) -> str | None:
    """Reject control-char injection + over-length in a single-line key value
    (equipment_name is a display key — NOT used for the safety decision). Returns a
    violation string or None. NUL/binary in the whole payload is already rejected
    by the raw S-2 scan; this screens the field value itself."""
    if len(value) > max_len:
        return f"{field} exceeds {max_len} characters"
    if any(ord(c) < 32 for c in value):
        return f"{field} contains control characters (possible injection)"
    return None


def _nl_injection(value: str, field: str) -> str | None:
    """Structural injection screen for a multi-line NL field (process_description,
    failure_events items): reject control characters other than tab / newline / CR.
    NUL / binary in the whole payload is already rejected by the raw S-2 scan; multi-line
    NL is allowed; SEMANTIC prompt-injection is neutralized at the inner LLM boundary
    (failure_mode_normalize). Satisfies docs/02 'reject injection in the NL failure
    descriptions' at the structural level."""
    if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        return f"{field} contains control characters (possible injection)"
    return None


def _screen_non_text(text: str) -> str | None:
    """S-2 structural sanitization scan — flag non-text / binary content.

    Returns None when acceptable text, else a violation string. NUL is an
    unambiguous binary signal; a high proportion of other control characters
    (excluding tab / newline / carriage-return, legitimate in JSON) indicates
    binary or injected control sequences. No lexical prompt-injection heuristic is
    applied here — semantic prompt-injection is the inner LLM boundary's job.
    """
    if "\x00" in text:
        return "non-text/binary content detected (NUL byte)"
    control = sum(1 for c in text if ord(c) < 32 and c not in "\t\n\r")
    if control and control > max(8, len(text) // 100):
        return "non-text/binary content detected (excessive control characters)"
    return None


class InputValidateNode(FunctionNode):
    """Outer pre_process gate — parse and validate the JSON FMEA request.

    S-2 (non-str / binary / control-char / oversized) runs in
    _extra_security_gate_input() before execute(); execute() then parses the JSON,
    enforces the caller-input contract + safety-acknowledgement precedence, and
    writes the canonical validated_input dict. S-1: VERIFIED_EXTERNAL (equipment /
    FMEA failure data is business-confidential).
    """

    # S-1: equipment / FMEA failure data is business-confidential → VERIFIED_EXTERNAL
    # (verified caller; matches the manifest). Declared explicitly, not inherited.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        # Config injected by the graph's register_nodes() (agent.yaml → node_config).
        # Defaults keep the node no-arg constructible (the PB-6 invoke-order test
        # instantiates every src/nodes/ class with no args).
        cfg = node_config or {}
        # Config default is validated strictly, NOT coerced: bool("false") == True would
        # silently open the safety gate. A non-bool config value fails fast at construction.
        ack_default = cfg.get("safety_review_acknowledged", False)
        if not isinstance(ack_default, bool):
            raise ConfigError(
                "InputValidateNode: config safety_review_acknowledged must be a boolean "
                f"(or absent), got {type(ack_default).__name__}"
            )
        self._safety_ack_default: bool = ack_default
        # Safety-critical categories are the FIXED code-side SSOT — NO config override in V1 (an
        # operator/caller cannot widen or narrow the set; the closed vocabulary and its critical
        # members are code-owned). Classification keys on the equipment_category TOKEN, so the
        # framework S-2 baseline (which can mask the free-text equipment_name) cannot fail it open.
        self._safety_critical: frozenset[str] = frozenset(_DEFAULT_SAFETY_CRITICAL_CATEGORIES)
        self._max_input_chars: int = int(cfg.get("max_input_chars", _DEFAULT_MAX_INPUT_CHARS))
        self._max_events: int = int(cfg.get("max_events", _DEFAULT_MAX_EVENTS))

    def _extra_security_gate_input(self, state: dict[str, Any]) -> dict[str, Any]:
        """S-2 domain gate — reject non-str / binary raw input, answer an oversized one.

        Contract: return ``state`` when clean; return a delta dict on violation. Never raise,
        never return None. The framework @final _security_gate_input() (baseline PII scan) is not
        overridden.

        Non-text input is refused here and ``execute()`` never runs — that is what this screen is
        for. The size ceiling is answered instead: it is a limit the caller can work within. The
        answered delta carries ``diagnostic_code`` and no ``user_input``, and ``execute()`` returns
        on seeing it, so an oversized body is still never parsed.
        """
        payload = state.get("user_input")
        if payload is None:
            return state
        corr = state.get("correlation_id")
        if not isinstance(payload, str):
            return self._reject(
                corr,
                "s2_non_text_type",
                f"user_input must be text (str), got {type(payload).__name__} (S-2)",
                state,
                {"type": type(payload).__name__},
            )
        violation = _screen_non_text(payload)
        if violation:
            return self._reject(corr, "s2_non_text", f"{violation} (S-2)", state, {"detail": violation})
        if len(payload) > self._max_input_chars:
            return self._reject(
                corr,
                "s2_oversized",
                f"input exceeds max_input_chars ({self._max_input_chars}) (S-2)",
                state,
                {"limit": self._max_input_chars},
            )
        return state

    def _reject(
        self,
        corr: Any,
        reason: str,
        message: str,
        state: dict[str, Any],
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Report a rejection to the caller — answered when it is not a malfunction, else fail closed.

        Both branches send the same sentence; they differ in what the run reports. A request the
        caller can change, and a draft withheld on policy, complete carrying the code (no table is
        built), so the reason reaches them as the result of the turn rather than as a terminal
        failure naming an exception type. A refusal this agent owns keeps status: error and its
        operator-facing error_log — and once that is returned, BaseNode.__call__ skips execute()
        on every later node, which is why the sentence is sent here rather than downstream.
        """
        emit_progress(explain(reason))
        payload = {"correlation_id": corr, "reason": reason, **(detail or {})}
        if is_caller_correctable(reason):
            emit_trace_event("input_degraded", payload, state)
            return {"status": AgentStatus.SUCCESS.value, "diagnostic_code": reason}
        emit_trace_event("input_validate_rejected", payload, state)
        return {"status": AgentStatus.ERROR.value, "error_log": [f"InputValidateNode: {message}"]}

    def _is_safety_critical(self, equipment_category: str) -> bool:
        # Exact token membership — NEVER a substring of the maskable free-text equipment_name.
        return equipment_category in self._safety_critical

    def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        corr = state.get("correlation_id")
        # The S-2 gate answers an oversized request rather than refusing it, and hands the answer
        # down as a delta carrying the code and no user_input. Return it untouched: the body was
        # not parsed there and must not be parsed here.
        answered = state.get("diagnostic_code")
        if answered:
            return {"status": AgentStatus.SUCCESS.value, "diagnostic_code": answered}

        emit_progress("Checking the request...")
        raw = state.get("user_input")

        # --- empty / non-str (S-2 already rejects non-str before execute) --------
        if not isinstance(raw, str) or not raw.strip():
            return self._reject(corr, "empty_or_missing", "user_input is empty or not a text document", state)

        # --- parse JSON object ----------------------------------------------------
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return self._reject(corr, "malformed", f"malformed JSON: {exc.__class__.__name__}", state)
        except RecursionError:
            return self._reject(corr, "too_deep", "JSON nesting is too deep", state)
        if not isinstance(payload, dict):
            return self._reject(corr, "not_object", "user_input must be a JSON object", state)

        # --- mandatory text fields -----------------------------------------------
        for field in _REQUIRED_TEXT_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                return self._reject(corr, f"missing_{field}", f"{field} is required (non-empty text)", state)
        # equipment_name is a single-line key (safety-critical list match) → control-char screen;
        # process_description is bounded NL text (multi-line allowed; NUL caught by the raw S-2 scan).
        eq_violation = _screen_key(payload["equipment_name"], "equipment_name", _EQUIPMENT_NAME_MAX)
        if eq_violation:
            return self._reject(corr, "equipment_name_invalid", eq_violation, state)
        if len(payload["process_description"]) > _TEXT_MAX:
            return self._reject(
                corr, "process_description_too_long", f"process_description exceeds {_TEXT_MAX} characters", state
            )
        pd_inj = _nl_injection(payload["process_description"], "process_description")
        if pd_inj:
            return self._reject(corr, "process_description_invalid", pd_inj, state)

        # --- equipment_category: required, closed controlled vocabulary (fail-closed) ---
        # The MANDATORY safety classification keys on this structured token (mask-immune),
        # never on the free-text equipment_name. An unknown/missing category can never be
        # confirmed non-critical → reject (no auto-approval bypass via a made-up label).
        equipment_category = payload.get("equipment_category")
        if not isinstance(equipment_category, str) or not equipment_category.strip():
            return self._reject(
                corr, "missing_equipment_category", "equipment_category is required (non-empty text)", state
            )
        equipment_category = equipment_category.strip()
        if equipment_category not in _EQUIPMENT_CATEGORY_VOCABULARY:
            return self._reject(
                corr,
                "unknown_equipment_category",
                "equipment_category is not a recognized category (fail closed)",
                state,
            )

        # --- failure_events: non-empty list of non-empty strings -----------------
        events = payload.get("failure_events")
        if events is None:
            return self._reject(corr, "missing_failure_events", "failure_events is required", state)
        if not isinstance(events, list):
            return self._reject(corr, "failure_events_type", "failure_events must be a list", state)
        if not events:
            return self._reject(corr, "failure_events_empty", "failure_events must not be empty", state)
        if len(events) > self._max_events:
            return self._reject(
                corr, "failure_events_too_many", f"failure_events exceeds {self._max_events} items", state
            )
        clean_events: list[str] = []
        for idx, ev in enumerate(events):
            if not isinstance(ev, str) or not ev.strip():
                return self._reject(
                    corr,
                    "failure_events_item",
                    f"failure_events[{idx}] must be non-empty text",
                    state,
                )
            if len(ev) > _TEXT_MAX:
                return self._reject(
                    corr, "failure_events_item_too_long", f"failure_events[{idx}] exceeds {_TEXT_MAX} characters", state
                )
            ev_inj = _nl_injection(ev, f"failure_events[{idx}]")
            if ev_inj:
                return self._reject(corr, "failure_events_item_invalid", ev_inj, state)
            clean_events.append(ev.strip())

        # --- safety_review_acknowledged: per-call value first, else config default
        if "safety_review_acknowledged" in payload:
            ack_raw = payload["safety_review_acknowledged"]
            if not isinstance(ack_raw, bool):
                return self._reject(
                    corr,
                    "safety_ack_type",
                    "safety_review_acknowledged must be a boolean when supplied",
                    state,
                )
            safety_ack = ack_raw
        else:
            safety_ack = self._safety_ack_default

        equipment_name = payload["equipment_name"].strip()
        # Admission control: a safety-critical equipment_category requires acknowledgement to be
        # admitted. (Every FMEA is review-required downstream regardless — safety_gate_check blocks
        # auto-approval for every category; ack gates admission of a known safety-critical category.)
        if self._is_safety_critical(equipment_category) and safety_ack is not True:
            return self._reject(
                corr,
                "safety_ack_required",
                "equipment_category is safety-critical; safety_review_acknowledged must be true "
                "(per-call or config) to admit the request",
                state,
            )

        # --- assemble canonical, msgpack-safe validated_input (structured-only) ---
        validated = {
            "equipment_name": equipment_name,
            "equipment_category": equipment_category,
            "process_description": payload["process_description"].strip(),
            "failure_events": clean_events,
            # resolved value carried so the inner safety_gate_check cannot drift
            "safety_review_acknowledged": safety_ack,
        }

        emit_trace_event(
            "input_validated",
            {"correlation_id": corr, "event_count": len(clean_events), "safety_review_acknowledged": safety_ack},
            state,
        )
        return {"validated_input": validated, "status": AgentStatus.SUCCESS.value}
