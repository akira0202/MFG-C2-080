# Template Design Specification — MFG-C2-080 EquipmentFMEADraftGeneratorAgent

## Agent Overview

Assists manufacturing quality engineers in drafting Failure Mode and Effect Analysis (FMEA)
documents. Given an equipment name and category, a process description, and a list of observed or
hypothetical failure events in natural language, it structures each event into FMEA schema fields, proposes
Severity / Occurrence / Detection (SOD) ratings, computes the Risk Priority Number (RPN), classifies
countermeasures, and renders an IATF 16949 / ISO 9001-format FMEA table. SOD ratings are
**agent-assisted, not agent-autonomous** — a qualified quality engineer must review and approve them.
A mandatory **SafetyGateCheck** marks every FMEA review-required (auto-approval blocked); a
safety-critical equipment category additionally carries an explicit mandatory safety warning. The
output is a **draft for engineer validation**, not a final FMEA.

## Position in AgentCore Architecture

- **Agent Class**: `EquipmentFMEADraftGeneratorAgent` (inherits `AgentBaseGraph`)
- **L1 Base**: **AgentBaseGraph** (L1-direct). `DocGenerationAgent` is a **pattern reference only**,
  not an inheritance target.
- **Cat 2 structure**: the outer `AgentBaseGraph` keeps the fixed backbone; the multi-step domain
  workflow is an inner `BaseGraph` (`src/graph/domain_workflow_graph.py`) wrapped by a `GraphNode`
  in the `main` slot. **The outer graph does NOT override `add_edges()`** — the framework wires the
  backbone.
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()`; backbone wiring left to the framework)

## Architecture Overview

Cat 2 two-layer composition. **S-1 trust is enforced at the OUTER boundary**: `input_validate` and the `security_gate_output` (S-3) declare `required_trust_level = VERIFIED_EXTERNAL`. The **inner** nodes declare `ANONYMOUS` (explicitly): `BaseGraph.invoke()` does not propagate `caller_trust_level` into the inner graph, so inner `VERIFIED_EXTERNAL` would be unreachable, and re-gating trust there is redundant (as S-2 is not duplicated on the trusted inner handoff). Equipment /
FMEA failure data is business-confidential, so a verified caller is required.
`SafetyGateCheck` (Pattern 11) is a **domain gate, independent of the S-1–S-5 layers**.

### Outer graph (`src/graph/graph.py`, `AgentBaseGraph`)

Fixed 5-node backbone; `register_nodes()` fills `pre_process` / `main` / `post_process` (+ `super()`
injects `initialize` / `finalize`). `add_edges()` is **not** overridden.

| Slot | Node | Responsibility | Inherits/Overrides |
|------|------|---------------|-------------------|
| initialize | InitializeNode | schema_version, correlation setup | default |
| pre_process | InputValidateNode | **S-2** (`_extra_security_gate_input()`): raw-string structural screen (non-str/binary/NUL/excessive-control/oversized). **execute()**: require caller-input `equipment_name`, `equipment_category`, `process_description`, non-empty `failure_events`; reject with the missing fields listed; **reject if `equipment_category` is not a member of the closed code-side vocabulary** (unknown/missing → fail closed — an unknown category is never treated as non-critical); per-field structural injection screen of the NL failure descriptions (`process_description` / `failure_events` items: reject control chars other than tab/newline/CR — multi-line allowed), plus a control-char screen on the single-line `equipment_name` key (carried for FMEA content/display — **never used for the safety decision**); resolve `safety_review_acknowledged` (per-call input, else config default `false`); **reject if `equipment_category` is safety-critical and the resolved `safety_review_acknowledged` is not true** | FunctionNode |
| main | FMEAWorkflowGraphNode | **`GraphNode`** wrapping the inner `DomainWorkflowGraph`: `get_subgraph()` returns it, `extract_input()` returns `validated_input` JSON-encoded as a **string**, `merge_output()` writes the inner `output` into the inherited `result` (+ `audit_metadata`) — nothing else. `error_strategy = "propagate"`. S-2/S-3 are a deliberate no-op here (gating is on the outer pre/post) | GraphNode |
| post_process | SecurityGateOutputNode | **S-3** (`execute()`): read the composed result and reject the output (`status: error`) if proprietary design data (IP) is detected; the framework `@final` `_security_gate_output()` baseline credential scan still runs after. The gated FMEA result reaches the caller via the inherited `result`. Emits an **S-4** `emit_trace_event` (`output_gated`); the `audit_metadata` artifact is assembled by the inner `audit_metadata_emit` | FunctionNode |
| finalize | FinalizeNode | response_metadata, node_history | default |

### Inner graph (`src/graph/domain_workflow_graph.py`, `BaseGraph`)

Custom linear topology; the domain pipeline. Each node emits at least one S-4 domain trace event.

| Node | Responsibility | Input State | Output State |
|------|---------------|-------------|--------------|
| input_hydrate | **Hydration (first inner node):** JSON-decode the inner `user_input` (the string handed off by the outer `extract_input()`) into the inherited `validated_input`; shape/format confirmation only — **no duplicated S-2** (the outer `input_validate` already gated the input; trusted handoff). `BaseGraph.invoke()` sets only inner `user_input`, not `validated_input` | user_input | validated_input |
| failure_mode_normalize | **LLM**: parse each NL failure description into FMEA schema fields (Function, Failure Mode, Effect, Cause). **Exactly one row per input failure event** (row-count drift → fail closed); each row is stamped a stable `fmea_id` that anchors the whole pipeline | validated_input | failure_modes |
| sod_rating_compute | **LLM-assisted (not autonomous)**: propose Severity / Occurrence / Detection ratings on the configured `rating_scale`, calibrated by `sod_rating_guidelines`; **mark each rating as pending qualified-engineer approval**. The output must be a **bijection with `failure_modes` by `fmea_id`** (same id set, each once — drop/add/duplicate/reorder-away → fail closed) so ratings join to the right failure mode | failure_modes | sod_ratings |
| rpn_calculate | **Deterministic**: RPN = S × O × D; flag items with RPN ≥ `rpn_high_priority_threshold` (default 100) as high-priority | sod_ratings | rpn_results |
| countermeasure_generate | **LLM-assisted**: for each HIGH-priority failure mode (RPN ≥ threshold) propose one engineering countermeasure (advisory; pending qualified-engineer approval), joined by `fmea_id` (bijection with the high-priority ids); augment those failure modes with `recommended_control`. Low-priority modes get no proposed control. `error_strategy = propagate` when high-priority modes exist | failure_modes, rpn_results | failure_modes |
| countermeasure_classify | For each failure mode, classify the recommended control as Preventive / Detective / Corrective (`countermeasure_taxonomy`); a proposed control is stamped `approval_required: true`; a mode with no control is `Unclassified` | rpn_results, failure_modes | countermeasures |
| safety_gate_check | **Pattern 11 — MANDATORY domain gate (static config-flag, not HITL):** classify by the structured `equipment_category` token against the fixed code-side critical category set (never the maskable `equipment_name`); **always block auto-approval — every FMEA is review-required in V1** — and for a safety-critical category additionally inject the mandatory safety-critical warning | countermeasures, validated_input | safety_gate |
| fmea_table_generate | Render the FMEA table (`output_format`: markdown / csv / json) with IATF 16949 / ISO 9001 column headers and RPN ranking; **inject the EU AI Act Art.9 risk-management block when `eu_ai_act_art9_mode`** | rpn_results, countermeasures, safety_gate | fmea_table |
| audit_metadata_emit | **S-4 artifact (terminal inner node):** assemble `audit_metadata` `{data_hash, generated_at, rating_scale, model_version}`; raw bodies not persisted (hashes only). The inner `get_output()` returns `{output: <fmea_table + safety_gate>, audit_metadata}` | fmea_table, safety_gate | audit_metadata |

### Data Flow

```
Outer backbone (fixed — add_edges NOT overridden):
  START → initialize → pre_process (InputValidate) → main (FMEAWorkflowGraphNode) → {route}
        → post_process (SecurityGateOutput) → finalize → END
                              ↓ (RETRY, max 3)
                           pre_process

Inner DomainWorkflowGraph (invoked by the main GraphNode):
  START → input_hydrate → failure_mode_normalize → sod_rating_compute → rpn_calculate → countermeasure_generate
        → countermeasure_classify → safety_gate_check → fmea_table_generate → audit_metadata_emit → END
```

> The outer `AgentBaseGraph` does **not** override `add_edges()` — the framework owns the backbone
> and the RETRY route. The `main` `GraphNode` (`FMEAWorkflowGraphNode`) calls
> `inner_graph.invoke(extract_input(state), session_id=ctx.session_id, ctx=ctx)` (a **string** — the JSON-encoded outer `validated_input`); the inner graph runs its own topology and returns
> `sub_result` via its `get_output()`; `merge_output()` maps the changed keys back into the outer
> state. The inner graph lives at `src/graph/domain_workflow_graph.py` (not `src/subagents/`).

### State Definition

Two state schemas (Cat 2 two-layer). Framework fields are **inherited from `AgentState`** (which
extends `SubgraphContext`) and are **NOT redeclared** — notably `user_input: str` (raw caller payload
as a string), `validated_input`, `result` / `formatted_output` (terminal output), `status`,
`session_id`, and the reducer-managed `node_history` / `error_log`.

**Input contract:** the caller passes the FMEA request as a **string** in `user_input` (a JSON
string) — `{equipment_name, equipment_category, process_description, failure_events: [str]}`. The outer
`input_validate` parses/validates it into the inherited `validated_input`. `equipment_category` MUST be
a member of a **closed, code-side controlled vocabulary** of lowercase/underscore tokens
(`hydraulic_press`, `power_press`, `robotic_arm`, `industrial_robot`, `plc`, `scada`, `conveyor`,
`overhead_crane`, `boiler`, `pressure_vessel`, and the non-safety-critical `general_equipment`); a
missing or out-of-vocabulary value **fails closed** (reject). The **mandatory safety classification
keys on `equipment_category` only** — `equipment_name` is free-text carried for the FMEA
content/display and is **never** used for the safety decision (ADR-017: the framework `@final` S-2
baseline can mask free-text, so a maskable field cannot back a never-fail-open gate).
`safety_review_acknowledged` is a **config flag** (default `false`), optionally overridden per-call —
not part of the mandatory caller JSON; the safety gate uses the resolved value.
**Conservative V1 (no caller-selectable auto-approval):** because `equipment_category` is a
caller-declared label and V1 has no trusted equipment catalog to verify it against the actual
equipment, the safety gate is conservative — **every FMEA is review-required (auto-approval is always
blocked), regardless of category**. `equipment_category` selects the **explicit mandatory
safety-critical warning** and the **admission-acknowledgement requirement** for a known safety-critical
category (reject if not acknowledged) — it is **not** an auto-approval fast path. A caller-selected
`general_equipment` is admitted without acknowledgement but its FMEA is **still review-required**, so
mis-declaring a critical item as `general_equipment` gains no auto-approval. The critical vocabulary is
**fully code-side fixed** (no config extension in V1).
The `main` GraphNode's `extract_input()` returns `validated_input` JSON-encoded as a **string** to the
inner graph (`extract_input(state) -> str`). The inner graph receives this string as its own
`user_input`; the first inner node `input_hydrate` JSON-decodes it back into the inherited
`validated_input` (`BaseGraph.invoke()` sets only inner `user_input`, not `validated_input`).

**Outer State** (`src/schemas/state.py`, `State(AgentState)`) — only outer-specific fields:

| Field | Type | Purpose | Set by |
|-------|------|---------|--------|
| audit_metadata | NotRequired[dict] | `{data_hash, generated_at, rating_scale, model_version}` — projected from the inner workflow | merge_output (from inner) |

The final S-3-gated FMEA table is written to the **inherited `result`**;
`AgentBaseGraph.get_output()` returns `formatted_output or result`.

**Inner State** (`src/graph/domain_workflow_graph.py`, `DomainWorkflowState(AgentState)`) — the domain
pipeline fields (schema created in the inner-graph issue; named + owned here):

| Field | Type | Purpose | Set by |
|-------|------|---------|--------|
| failure_modes | NotRequired[list[dict]] | `{fmea_id, function, failure_mode, effect, cause}` per event — `fmea_id` is the stable per-row identity | failure_mode_normalize |
| sod_ratings | NotRequired[list[dict]] | `{fmea_id, severity, occurrence, detection, approval_required: true}` (+ the echoed failure-mode text, informational) | sod_rating_compute |
| rpn_results | NotRequired[list[dict]] | `{fmea_id, severity, occurrence, detection, rpn, high_priority, source_failure_mode?}` — `fmea_id` carried through so the renderer joins by identity | rpn_calculate |
| countermeasures | NotRequired[list[dict]] | `{fmea_id, control, type: Preventive/Detective/Corrective, approval_required?, source_failure_mode?}` — `approval_required: true` on a proposed (LLM-assisted) control | countermeasure_classify |
| safety_gate | NotRequired[dict] | `{safety_critical, warning, blocked}` | safety_gate_check |
| fmea_table | NotRequired[str] | Rendered FMEA table (per `output_format`) | fmea_table_generate |
| audit_metadata | NotRequired[dict] | `{data_hash, generated_at, rating_scale, model_version}` — S-4 artifact, projected to outer | audit_metadata_emit |

**Projection (`get_output()` / `merge_output()`):** the inner `get_output()` returns
`{output: <fmea_table + safety_gate>, audit_metadata: …}`; the `main` GraphNode's `merge_output()`
writes `output` into the outer inherited `result` and copies `audit_metadata` — nothing else.
`node_history` / `error_log` are reducer-managed and are **NEVER** projected inner → outer.

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- No JWT, API keys, credentials in State (LLM key via `ctx.secrets.require()`, never stored)
- InvocationContext via `config["configurable"]` only (not in State)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)
- Manufacturing process data only; no PII

## Config Surface (agent.yaml / config)

```yaml
rating_scale: iatf_16949                            # iatf_16949 (1–10) | iso_9001_simplified (1–5)
rpn_high_priority_threshold: 100                    # RPN >= this = high-priority flag
safety_review_acknowledged: false                  # MUST be true to admit a safety-critical equipment_category
# The safety-critical category set is FULLY CODE-SIDE FIXED (_DEFAULT_SAFETY_CRITICAL_CATEGORIES:
# hydraulic_press, power_press, robotic_arm, industrial_robot, plc, scada, conveyor, overhead_crane,
# boiler, pressure_vessel) — NO config override in V1 (an operator/caller cannot widen or narrow it).
# Every FMEA is review-required regardless of category (conservative V1 — no caller-selectable auto-approval).
output_format: markdown                            # markdown | csv | json
eu_ai_act_art9_mode: false                         # inject EU AI Act Art.9 risk-management block
countermeasure_taxonomy: {}                        # optional override of the Preventive/Detective/Corrective keyword map (code default applies when empty)
```

- **The RPN threshold has a single home: `rpn_high_priority_threshold` (config, default 100).** There
  is no per-call `rpn_threshold` input field; zero-config uses 100.
- **SOD ratings are LLM-assisted, never final.** They carry `approval_required: true`; a qualified
  quality engineer reviews and approves before the FMEA is used (IATF 16949 first-issuance
  defensibility). RPN (`S × O × D`) is the deterministic part.
- **`safety_review_acknowledged`** is a config flag (default `false`), per-call overridable. For a
  safety-critical `equipment_category`, the **resolved** value must be true to **admit** the request;
  otherwise `input_validate` rejects. A config default is always present, so it always resolves — it is
  not a mandatory caller-input field. (Acknowledgement permits admission; it never auto-approves the
  FMEA — every FMEA is review-required.)
- **The safety-critical category set (`_DEFAULT_SAFETY_CRITICAL_CATEGORIES`) is a MANDATORY,
  fully code-side-fixed gate** — **always applied and not operator-configurable in V1** (no config
  override widens or narrows it; the closed `equipment_category` vocabulary and its critical members are
  the code-side SSOT). The gate is conservative: **every FMEA is review-required (auto-approval
  blocked)**; a known safety-critical category additionally requires acknowledgement for admission and
  carries the explicit mandatory safety-critical warning. Site-specific critical categories are a
  post-V1 concern (they would require a vetted vocabulary-promotion mechanism with change control and a
  boundary test) — never a caller/operator-supplied list.
- **Audit metadata is always produced (no external sink, no config toggle in V1).** There is no
  external/persistent audit-log sink in V1, so nothing to gate. On every run, `audit_metadata_emit`
  writes the mandatory invocation-local `audit_metadata` (data hash, timestamp, `rating_scale` / model
  version) and projects it to outer State; raw bodies are never stored (hashes only).

## KB / Data Files

- The closed `equipment_category` vocabulary and its safety-critical members ship as a **secure
  code-side default** (`_DEFAULT_SAFETY_CRITICAL_CATEGORIES` in `src/nodes/input_validate_node.py`) —
  not a separate file, not operator-configurable in V1. Classification is by **exact `equipment_category`
  token membership** (`hydraulic_press`, `power_press`, `plc`, `scada`, `conveyor`, `robotic_arm`, etc.)
  — **not** a substring match of the free-text `equipment_name` (which the S-2 baseline can mask; see
  ADR-017). There is **no config override in V1**; widening the critical set is a post-V1 concern
  requiring a vetted vocabulary-promotion mechanism (not a caller/operator list).
- `fmea_table_templates/` — IATF 16949 / ISO 9001 column-header templates (Markdown + CSV variants).
  **Rendered in-process** (no external template engine); an invalid `output_format` falls back to
  Markdown.
- `sod_rating_guidelines/` — reference rating guidance (calibration aid for the LLM-assisted SOD).
- `countermeasure_taxonomy` — Preventive / Detective / Corrective keyword map. A **code-side default**
  (`_DEFAULT_TAXONOMY` in `src/services/countermeasure_classification.py`), **config-overridable as an
  inline dict** (`node_config["countermeasure_taxonomy"]`) — not a `config/*.yaml` data file (the repo
  ships no config YAML data files; see docs/07).
- **Ownership:** `prompts/`, `sod_rating_guidelines/`, and the base templates are a starter set
  authored/bundled by the implementer (automotive / electronics / machinery references);
  company- or equipment-specific customization is the customer's deploy-time responsibility.

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, permissions, credential handle)
- [x] ConnectionPolicy (retry/timeout for the LLM calls)
- [x] **LLM adapter injection** — the adapter travels through the graph config under `llm`;
      `graph.py` prefers a supplied adapter and otherwise injects the default keyless
      `AzureOpenAIFMEALLM`, so a config-less deploy still has a working adapter. It resolves
      `AZURE_OPENAI_*` from the **request-bound `SecretProvider`**
      (`InvocationContext.from_state(state).secrets.require`) at CALL time — never `os.environ`,
      never State, never a build-time read; an unprovisioned key fails closed. `src/api/startup.py`
      is the single startup boundary that loads `config/config.yaml`, selects the deployment
      profile and compiles, so no node reads the process environment. Provisional Stage ⑤
      substitutes a bounded fake for the provider call under an explicit CI-only flag, so it needs
      no credential and proves the workflow contract rather than a live call (operation guide,
      *Provisional STG evidence*).
- [x] SecurityViolationError
- [x] **S-2** `_extra_security_gate_input()` (on `input_validate`): raw-string structural screen
      (non-str/binary/NUL/excessive-control/oversized). Per-field structural injection screening of
      the NL failure descriptions (`process_description`, `failure_events` items: reject control
      chars other than tab/newline/CR — multi-line NL allowed) runs in `execute()` after the JSON is
      parsed, where the field is identifiable. Semantic prompt-injection is neutralized at the inner
      LLM boundary (`failure_mode_normalize`). **`_security_gate_input()` is NOT overridden.**
- [x] **S-3** `execute()` (on `security_gate_output`): reject the output (`status: error`) if
      proprietary design data (IP) is detected — the block reads `state["result"]`, which only
      `execute()` receives. **`_security_gate_output()` is NOT overridden** (its `@final` baseline
      credential scan still runs). **Credential BLOCK posture:** a credential-shaped string
      in any node's `execute()` return — including a token carried through an LLM-generated
      countermeasure into the FMEA rows — trips the baseline `@final` scan, which runs *before* and
      *independent of* the domain IP gate, and fails closed (`status: error`, no output, credential
      body never surfaced). BLOCK is the approved end-to-end behaviour (no downstream
      redaction-to-success); it is separate from the domain S-3 IP reject.
- [x] **S-4** `emit_trace_event()` — at least one domain event inside each `execute()` (e.g.
      `input_validated`, `failure_modes_normalized`, `sod_proposed`, `rpn_computed`,
      `countermeasures_generated`, `countermeasures_classified`, `safety_gate_checked`, `fmea_rendered`, `output_gated`).
      Framework `node_start`/`node_complete`/`node_error` are **not** re-emitted.

> **SafetyGateCheck (Pattern 11)** is a domain gate, **separate from S-1–S-5**. It is MANDATORY and
> is implemented as a **static gate** on the fixed code-side `equipment_category` token set — in V1
> every category is `blocked: true` (review-required); a safety-critical category additionally requires
> `safety_review_acknowledged` for admission and carries the explicit mandatory safety warning — **not**
> an in-graph HITL `interrupt()`. (Were HITL ever required, it would need `hitl.enabled: true`
> + `memory_enabled: true` and a PB-7 interrupt-propagation test — not used here.)

> **S-2/S-3 gate behaviour (ADR-017):** the outer `pre_process` / `post_process` and the inner
> `FunctionNode`s run the framework `@final` gates (baseline PII / credential scan), which are NOT
> overridden. The S-2 domain screen extends via `_extra_security_gate_input()` (it runs before
> `execute()` and its returned error-state short-circuits + blocks). The S-3 output block is done in
> `execute()`: only `execute()` receives the full `state` (and thus `state["result"]`, the composed
> output), and the `_extra_security_gate_output()` hook — passed `execute()`'s return delta, with its
> return/raise not surfaced as the node result — cannot reject the output. The `main` `GraphNode`'s
> S-2/S-3 are a **deliberate no-op** — input gating is done by the outer `pre_process`, output gating
> by the outer `post_process`.

> **Safety classification vs S-2 masking (ADR-017 addendum):** the framework `@final` baseline S-2
> scan may **mask/mutate free-text values before `execute()` sees them** (observed via `Graph.invoke()`:
> a Title-case `equipment_name` such as `"500t Hydraulic Press"` is rewritten to `"500t [MASKED]"`, and
> the masking is applied before the domain `_extra_security_gate_input()` hook — the template never sees
> the clean value). A MANDATORY, never-fail-open safety gate therefore MUST NOT key on a maskable
> free-text field. The safety classification keys on the structured, mask-immune `equipment_category`
> token (a closed code-side vocabulary of lowercase/underscore tokens); `equipment_name` is display-only.
> An unknown/missing `equipment_category` fails closed (reject) — an unclassifiable request is never
> treated as non-critical. The template does not rely on the baseline leaving free-text intact.

### Composition Pattern
- **Pattern**: **GraphNode (subgraph)** — the `main` slot wraps the inner `DomainWorkflowGraph`.
- **Composition target**: `src/graph/domain_workflow_graph.py` (`BaseGraph`, custom topology).
- **Error propagation strategy**: **propagate** (`error_strategy = "propagate"`) — inner-graph errors
  re-raise as `SubgraphError`; the outer graph short-circuits to `finalize` with `status: error`; no
  partial FMEA is emitted. No in-graph HITL (`propagate_hitl` is a GraphNode-subclass opt-in, not used here).

## Outcome Classification

A run ends in one of three ways, and which one is decided by **whose problem the condition is**.

| Outcome | Conditions | What the caller receives |
|---|---|---|
| FMEA table | the request was admitted and drafted | the table in `output`, plus `audit_metadata` |
| Notice (`status: success` + `diagnostic_code`) | **the caller can correct it** — more content than the ceiling allows, a body that is not a JSON object, a missing equipment name / process description / category / failure events, a category outside the closed vocabulary, a per-field length or character contract; or **this agent withholds the draft on policy** — a safety-critical category with no recorded safety-review acknowledgement | one sentence naming what to change or what the operator has to enable, in `output`, with the allow-listed code in `diagnostic_code`. **No table is built** |
| Error (`status: error`, no output) | the screen that decides whether the body is a document at all (non-text input, a NUL byte, a flood of control characters); the terminal S-3 gate (proprietary / confidential markers in the table, credential-shaped content); and every condition inside the workflow — a broken handoff, an unusable LLM, an unanchored extraction, a rating / RPN / rendering fault | nothing. The operator-facing reason stays in `error_log` |

**Withholding is not a refusal, and answering it does not admit the request.** A safety-critical
category still produces no FMEA without the acknowledgement; what changes is only how the run
reports it. Reported as an error it reads as a malfunction, and the caller cannot tell a
deliberate withholding from a broken agent. An empty FMEA table is never returned in place of a
notice either: that would be a real finding about equipment this agent never analysed.

The size ceiling is answered rather than refused because it is a limit the caller can work within.
It is screened before `execute()`, so the answer carries the code and no `user_input`, and
`execute()` returns on seeing it — an oversized body is never parsed. The non-text screen next to
it keeps refusing: it exists so such a payload is never interpreted at all.

Only the outer `pre_process` produces a notice, so no marker has to cross the inner graph
boundary: `main` skips the workflow when the gate has already answered, and `post_process`
releases the sentence. The wording comes from the single `failure_message` catalogue, which
substitutes only the field name the code itself carries — no request value, equipment name or
failure-event text can appear in it.

## Error & Edge Cases

| Condition | Behaviour |
|-----------|-----------|
| Missing `equipment_name` / `equipment_category` / `process_description` or empty `failure_events` | `input_validate` → **notice** (`status: success` + `diagnostic_code`), no table |
| `equipment_category` not in the closed code-side vocabulary | `input_validate` → **notice** (`unknown_equipment_category`); the request is not admitted — an unknown category is never treated as non-critical |
| Safety-critical `equipment_category` with resolved `safety_review_acknowledged` not true (no per-call true; config default `false`) | `input_validate` → **withheld** (`safety_ack_required`): answered, not admitted, no table |
| Request larger than `max_input_chars` | S-2 → **notice** (`s2_oversized`); the body is never parsed |
| Non-text / binary input (NUL byte, control-character flood) | S-2 **reject** (`status: error`), `execute()` never runs |
| Control character in one NL failure description / process description / equipment name | `input_validate` → **notice**; a contract on a value inside a body already accepted as text |
| Safety-critical `equipment_category` (acknowledged) | `safety_gate_check` injects a mandatory safety-review warning; FMEA marked review-required (not auto-approved) |
| Non-safety-critical `equipment_category` (`general_equipment`) | admitted without acknowledgement; the FMEA is **still review-required** — `safety_gate_check` blocks auto-approval for every category in V1, so a caller label never grants auto-approval |
| Proprietary design data detected in output | S-3 reject |
| LLM unavailable | error (normalization and SOD proposal require the LLM) |
| Credential-shaped content reaches a node's `execute()` return (e.g. a token that passes advisory validation into an LLM-generated countermeasure) | framework `@final` S-3 credential scan **BLOCKS** fail-closed → `status: error`, no output; credential body not returned in `result` / response / trace / `error_log`. Approved end-to-end posture — not softened by any downstream redaction-to-success; separate from the domain S-3 IP reject (baseline S-3 runs before and independent of `security_gate_output`) |

## Guardrails & Human-Review Boundary

- **SOD ratings are agent-assisted, never final** — a qualified quality engineer must review and
  approve before the FMEA is used. AI-proposed ratings are marked `approval_required`.
- **SafetyGateCheck is MANDATORY**: in V1 it blocks an auto-approved FMEA for **every** category
  (review-required), and for a safety-critical `equipment_category` it additionally injects the explicit
  mandatory safety-review warning.
- Output is a **draft for engineer validation**, not a final FMEA and not an equipment-design
  decision.
- **In-graph HITL is not used** — SafetyGateCheck is a static gate. (If in-graph HITL were required,
  it would need `hitl.enabled: true` + `memory_enabled: true` + a PB-7 test.)
- Read-only: no external writes.

### Deterministic FMEA rules (fixed; no LLM)

- **`rpn_calculate`**: `RPN = Severity × Occurrence × Detection`; `high_priority` when
  `RPN ≥ rpn_high_priority_threshold` (default 100). Each S/O/D must be an integer within the
  `rating_scale` bound (`iatf_16949` → 1–10, `iso_9001_simplified` → 1–5); a missing or
  out-of-range rating is a data fault → **fail closed** (`status: error`). Each SOD rating must also
  carry `approval_required: true` (the pending-qualified-engineer marker); a missing or non-true
  value is a fault → **fail closed**, so an unapproved rating never flows into the RPN result as if
  final. Config is validated strictly at construction (never coerced): an unrecognized `rating_scale`
  (not one of the two above — which would silently pick a permissive max) or a non-positive / non-int
  `rpn_high_priority_threshold` raises `ConfigError`. Unset values take the secure code default.
- **`countermeasure_classify`**: match each recommended control against the
  `countermeasure_taxonomy` keyword map in the order Preventive → Detective → Corrective (first
  match wins; prevention prioritized). No keyword match → **Corrective** (conservative default);
  no recommended control → **Unclassified**. Emits **exactly one entry per failure mode**, each
  carrying its `fmea_id`; a malformed member (a non-object record, or a control field present but not
  text) is a data fault → **fail closed** (`status: error`), never silently dropped.
- **`safety_gate_check`** (MANDATORY): the safety-critical **category set** is the SAME code-side SSOT
  as the outer `input_validate` (`_DEFAULT_SAFETY_CRITICAL_CATEGORIES`), **fully code-side fixed (no
  config override in V1)**. Classification keys on the structured `equipment_category` token (never the
  maskable `equipment_name`). **`blocked: true` for every category** — in V1 every FMEA is
  review-required, so no caller-declared category grants auto-approval. A safety-critical category
  additionally sets `safety_critical: true` and carries the mandatory safety-critical warning,
  regardless of the upstream acknowledgement (acknowledgement permits admission; it does not
  auto-approve the FMEA).
- **`fmea_table_generate`**: **join RPN rows to countermeasures by `fmea_id`, never by array
  position** — if the two id sets differ (a missing / extra / duplicate row) the render **fails
  closed** (`status: error`) rather than emit a plausible but mis-associated safety table. Render the
  FMEA rows ranked by RPN (descending) in `output_format`
  (markdown / csv / json) with IATF 16949 / ISO 9001 headers; prepend the safety review-required
  banner when blocked and the EU AI Act Art.9 risk-management block when `eu_ai_act_art9_mode`.
  **Always** surface a provisional-draft disclaimer on every output (all formats) — the S/O/D
  ratings are agent-assisted and pending qualified-engineer approval — so the human-review boundary
  is marked even for non-safety-critical equipment / non-EU-AI-Act runs, where the safety banner
  and the Art.9 block do not apply.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only (no `agents/base/` required)

## Acceptance Criteria (testable)

- Standard failure mode → correct FMEA table in IATF 16949 format.
- RPN ≥ `rpn_high_priority_threshold` → high-priority flag raised.
- Multiple failure events in one call → all processed into the table.
- `eu_ai_act_art9_mode: true` → EU AI Act Art.9 risk-management block injected.
- `output_format: csv` → valid Excel-compatible CSV.
- Safety-critical `equipment_category` with `safety_review_acknowledged` resolving to not-true (no per-call true; config default `false`) → blocked (`input_validate` reject).
- Safety-critical `equipment_category` with `safety_review_acknowledged: true` (per-call or config) → admitted; FMEA review-required (blocked, not auto-approved).
- Non-safety-critical `equipment_category` (`general_equipment`) → admitted; FMEA still review-required (auto-approval blocked) — mis-declaring a critical item as `general_equipment` does NOT yield an auto-approved FMEA.
- `equipment_category` missing or out-of-vocabulary → reject (fail closed).
- Injection in NL failure description → S-2 reject.
- `audit_metadata` (hash-only) is produced by `audit_metadata_emit` on every run, carried in the inner graph output, projected to the outer State by `merge_output`, and surfaced to the caller by the outer graph's `get_output()` (there is no external audit-log sink or config toggle in V1).
- **PB**: through the deployed outer `Graph.invoke()`, a safety-critical `equipment_category` is
  classified `safety_critical` and blocks the auto-approved FMEA — **no fail-open even when
  `equipment_name` is masked by the S-2 baseline**; a `general_equipment` request is **still
  review-required** (blocked — a caller label grants no auto-approval); an unacknowledged safety-critical
  category and an out-of-vocabulary `equipment_category` each fail closed (no partial FMEA); and the S-3
  gate rejects proprietary design data — all verified end-to-end.
- **PB**: credential-shaped content carried into an LLM-generated countermeasure → full outer `invoke` returns `status: error`, `get_output()["output"] is None`, and the credential body is not surfaced (framework S-3 BLOCK; no downstream redaction-to-success).

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed deterministic pipeline, not an autonomous think-act loop |
| Graph class name | `…Agent` | `…AgentGraph` | **`…Agent`** | Matches the fixed design spec; some fleet templates suffix `…Graph` |
| Composition pattern | flat FunctionNode chain (`add_edges` override) | outer backbone + inner `BaseGraph` via `main` GraphNode | **outer backbone + inner GraphNode** | Framework Cat 2 pattern: fixed outer backbone, domain workflow in an inner graph, `add_edges()` not overridden |
| Credential-shaped content in output | Redact-to-SUCCESS (downstream mask) | BLOCK (fail-closed at baseline S-3) | **BLOCK** | Baseline `@final` S-3 runs before the domain output gate and raises on match, so a downstream mask-to-success is unreachable end-to-end; an FMEA carrying a credential-shaped token is safer returned fail-closed (`status: error`) for human follow-up than masked-and-shipped |
| Node structure | 6-node | 8-stage architect flow | **8-stage** | Carries the mandatory safety gate + explicit RPN step; in Cat 2 pattern B it maps to outer pre/post (InputValidate, SecurityGateOutput) + the inner workflow (input_hydrate + 6 domain nodes + audit_metadata_emit = 8 inner) |
| SOD scoring | deterministic value | LLM-assisted + human approval | **LLM-assisted + approval** | Agent-assisted, not autonomous; RPN (`S × O × D`) is the deterministic part |
| RPN threshold | input `rpn_threshold` + config | config `rpn_high_priority_threshold` only | **config only** | Removes the duplicate input field; zero-config uses 100 |
| SafetyGateCheck mechanism | HITL `interrupt()` | static config-flag gate | **static config-flag gate** | Structured `equipment_category` + `safety_review_acknowledged` decide the block; no checkpointer / HITL needed |
| Safety classification signal | free-text `equipment_name` substring | structured `equipment_category` token (closed vocabulary) | **structured `equipment_category`** | The framework `@final` S-2 baseline masks free-text (`equipment_name` → `[MASKED]`), which fails-open a substring gate through `.invoke()`; a closed-vocabulary category token is mask-immune and keeps the MANDATORY gate never-fail-open. Unknown/missing category fails closed |
| Non-critical fast path (V1) | caller-selected `general_equipment` → auto-approvable | conservative — every FMEA review-required; category only adds the safety-critical warning + admission ack | **conservative (no auto-approval via a caller label)** | A caller-declared category is unverifiable (no trusted equipment catalog in V1); letting `general_equipment` auto-approve would let a mis-label bypass the MANDATORY gate. So `blocked: true` for every category; critical categories add the explicit warning + admission ack. Trusted-catalog verification / config-extensible critical set is a post-V1 concern |
| Report/template mechanism | external engine (Jinja2) | in-process rendering, fallback markdown | **in-process rendering** | Avoids a template-engine dependency; invalid format falls back to markdown |
| Domain-prompt scope | fully customer-authored | bundled starter + deploy-time customization | **bundled starter set** | Implementer ships automotive/electronics/machinery references; customer tunes per equipment |
| External audit-log sink | wire an external/persistent sink + a config toggle | none — always-on invocation-local artifact | **no external sink in V1** | The audit-trail requirement is satisfied by `audit_metadata_emit` (always-on, invocation-local, hash-only), so no external audit-log sink or toggle is exposed |
