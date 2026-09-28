# MFG-C2-080 — EquipmentFMEADraftGeneratorAgent

> **Category**: Cat 2 (multi-step domain workflow)
> **Industry**: MFG

## Overview

This agent drafts an equipment FMEA (Failure Mode and Effects Analysis) for a quality
engineer to review. You give it the equipment, a description of the process, and the failure events
you have observed or want to consider; it works out the possible effects and root causes, assigns
Severity, Occurrence and Detection scores with the resulting Risk Priority Number, and proposes
countermeasures for the highest-scoring items. The output is a structured FMEA table, intended to
turn a first draft from a multi-day job into a short one.

The scoring proposals and wording come from a language model; the arithmetic, the countermeasure
classification, the safety gate and the table layout are deterministic. It is a provisional draft
only — it is not a completed risk assessment, and a qualified engineer has to validate every row
before it counts.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## What it returns

- **An FMEA draft** when the request is admitted: the failure modes with their S/O/D ratings and
  RPN, the proposed countermeasures and the mandatory safety-review note — always review-required,
  never auto-approved.
- **A short notice** when the request is one you can correct (more content than the limit allows,
  a body that is not a JSON object, a missing equipment name, process description, category or
  failure events, a category outside the accepted set, or a field that is too long or holds
  characters that are not allowed), or when the draft is withheld: a safety-critical equipment
  category is only drafted where the deployment has recorded a safety-review acknowledgement. The
  run completes and the answer names what to change or what the operator has to enable. No FMEA is
  produced — an empty table is never returned in its place.
- **An error with no output** when the input is not a text document at all (binary content, a NUL
  byte, a flood of control characters), when the composed table trips the output gate, or when a
  step inside the workflow cannot complete.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent connects to the platform at start-up. Without it, start-up fails immediately (see *Behaviour without the platform* below). Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Installed from PyPI as a dependency. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run **only** on AGENTIC STAR. There is no fallback or degraded
mode. If the platform is unreachable or the SDK version does not match, the agent raises
`PlatformRequired` during graph compile / start-up preflight rather than starting in a partially
working state. This is intentional — a half-running agent is worse than one that refuses to start.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection. Running the agent itself does not.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/` for the design specification and the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.

