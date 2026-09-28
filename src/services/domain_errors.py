"""AgentCore Platform v1.0 — shared domain-compute exception for MFG-C2-080 services.

The deterministic FMEA services (RPN / countermeasure / safety gate / table render) raise
``DomainComputeError`` for a data-integrity fault that must fail the inner workflow closed
(the calling node converts it to a ``status: error`` delta — no partial output). No
framework imports here so the services stay unit-testable without the framework installed.
"""

from __future__ import annotations


class DomainComputeError(Exception):
    """A deterministic-computation fault (bad/insufficient data) that must fail closed."""
