"""AgentCore Platform v1.0 — bounded fake for the provisional Stage 5 CI profile.

NOT a deployment path. Selected only by the explicit CI profile resolved in `src/api/startup.py`;
the shipped runtime config cannot reach it, and no production or AgentRegistry path constructs it.

Why it exists: the provisional Stage 5 job boots the template inside a CI container and performs
one first invoke. `failure_mode_normalize` and `sod_rating_compute` are unconditional, fail-closed
steps, so a live run would have to call a real LLM provider — which means a provider credential in
CI, a per-run cost, and a non-deterministic response that can fail the evidence assertion for
reasons unrelated to this template. The bounded fake keeps the whole workflow — hydration,
normalization validation, SOD validation, the deterministic RPN calculation, countermeasure
generation and classification, the safety gate, table rendering and the audit trail — executing
exactly as in production, and replaces only the provider call. What Stage 5 then proves is the
deploy -> invoke -> workflow contract, NOT that a live provider call succeeds. See docs/07 for
that distinction.

The responses below are derived from the request the node actually sent, so every real validator
runs against them for real: the normalization response carries exactly one row per input failure
event, and the SOD and countermeasure responses are bijective with the `fmea_id` set the node put
in the request. Nothing here bypasses `parse_and_validate_*`.
"""

import json
from typing import Any

# Ratings for the synthetic SOD response, on the iatf_16949 scale (1..10). The first failure mode
# is rated so that its RPN (7 x 5 x 4 = 140) clears the high-priority threshold (100) and the rest
# stay below it (3 x 3 x 3 = 27) — so a single invoke exercises BOTH the high-priority branch
# (countermeasure generation + classification) and the low-priority branch (carried Unclassified).
_HIGH_SOD = (7, 5, 4)
_LOW_SOD = (3, 3, 3)

_FAKE_CONTROL = (
    "Review the maintenance interval and add a scheduled inspection step; confirm with the "
    "responsible engineer before changing the process."
)


def _rows_for_normalize(payload: dict[str, Any]) -> str:
    events = payload.get("failure_events")
    events = events if isinstance(events, list) else []
    equipment = payload.get("equipment_name") or ""
    return json.dumps(
        [
            {
                "function": f"Stated function of {equipment}".strip(),
                "failure_mode": str(event),
                "effect": "Effect not established by the bounded-fake profile.",
                "cause": "Cause not established by the bounded-fake profile.",
            }
            for event in events
        ],
        ensure_ascii=True,
    )


def _rows_for_sod(payload: dict[str, Any]) -> str:
    modes = payload.get("failure_modes")
    modes = modes if isinstance(modes, list) else []
    scale_max = payload.get("scale_max")
    scale_max = scale_max if isinstance(scale_max, int) and scale_max > 0 else 10
    out = []
    for position, mode in enumerate(modes):
        severity, occurrence, detection = _HIGH_SOD if position == 0 else _LOW_SOD
        out.append(
            {
                "fmea_id": mode.get("fmea_id"),
                "failure_mode": mode.get("failure_mode"),
                # Clamp to the scale the node asked for: on iso_9001_simplified (max 5) the
                # iatf ratings would be out of range and the real validator would reject them.
                "severity": min(severity, scale_max),
                "occurrence": min(occurrence, scale_max),
                "detection": min(detection, scale_max),
            }
        )
    return json.dumps(out, ensure_ascii=True)


def _rows_for_controls(payload: dict[str, Any]) -> str:
    modes = payload.get("failure_modes")
    modes = modes if isinstance(modes, list) else []
    return json.dumps(
        [{"fmea_id": mode.get("fmea_id"), "recommended_control": _FAKE_CONTROL} for mode in modes],
        ensure_ascii=True,
    )


class FakeFMEALLM:
    """In-process stand-in for `AzureOpenAIFMEALLM` — contacts no provider.

    Implements the same interface the LLM nodes call, `complete(messages, state) -> str`, and
    records every call so a test can assert a node actually reached the provider boundary with the
    messages its own message builder produced.

    Which of the three requests it received is decided from the request payload the node sent, not
    from any out-of-band flag: only the normalization request carries `failure_events`, only the
    SOD request carries `rating_scale`, and the countermeasure request carries neither.
    """

    def __init__(self, raise_on_complete: Exception | None = None) -> None:
        self._raise_on_complete = raise_on_complete
        self.calls: list[list[Any]] = []

    def complete(self, messages: list[Any], state: dict[str, Any]) -> str:
        self.calls.append(messages)
        if self._raise_on_complete is not None:
            raise self._raise_on_complete
        payload = self._payload(messages)
        if "failure_events" in payload:
            return _rows_for_normalize(payload)
        if "rating_scale" in payload:
            return _rows_for_sod(payload)
        return _rows_for_controls(payload)

    @staticmethod
    def _payload(messages: list[Any]) -> dict[str, Any]:
        """The untrusted data always travels in the SEPARATE user message the builders emit."""
        for message in reversed(messages if isinstance(messages, list) else []):
            if isinstance(message, dict) and message.get("role") == "user":
                try:
                    data = json.loads(message.get("content", ""))
                except (TypeError, ValueError):
                    return {}
                return data if isinstance(data, dict) else {}
        return {}
