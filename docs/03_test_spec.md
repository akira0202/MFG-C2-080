# Test Specification

## Test Strategy
- **Test types:** Unit (framework-free FMEA services + node deltas) · Integration (graph config wiring) ·
  Proof-of-Boundary (framework contract) · End-to-end (`Graph.invoke()` through the outer backbone +
  inner workflow + S-3).
- **Coverage approach:** every testable Acceptance Criterion in `docs/02_design.md` maps to one or more
  tests (see Business Logic Tests below); line-coverage is not a CI gate — criterion coverage + the
  mandatory Framework-Compliance / Proof-of-Boundary items are the target.
- **Non-determinism:** the LLM is used only for failure-mode normalization + SOD proposal; every LLM
  result is bound to the input by a stable `fmea_id` (a non-bijective / dropped / foreign / duplicate id
  fails closed) and schema-validated. RPN is deterministic (`S × O × D`). Tests inject fake / absent /
  malformed LLMs; no live model call is made in CI.

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | Type check pass, no Pydantic/dataclass | Pass (`test_state_file_safety`) |
| TC-02 | SecurityViolationError fires on invalid input | Error raised | Pass (`test_input_validate_node` S-2 rejects; trust gate) |
| TC-03 | No JWT/Credential in State | CI `gate-credential-scan`: 0 violations (S-5 enforcement is done in CI) | Pass (CI gate) |
| TC-04 | InvocationContext via configurable only | Direct access raises error | Pass (State holds no `InvocationContext` or secret — `test_state_file_safety`; ctx passed via `invoke(ctx=...)` in `test_end_to_end_invoke`) |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start` / `node_complete` / `node_error` absent from `execute()` body | 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (`FunctionNode` subclass) | `TypeError` raised at class definition if overridden (`@final` enforced by framework) | 0 overrides |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused | Pass (outer `test_trust_levels`; inner `test_all_inner_anonymous`) |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial when domain checks needed | Domain-specific input checks execute correctly (schema, safety-ack gate, injection screen) | Hook body non-trivial |
| TC-10 | S-3: `execute()` output block (proprietary-IP block gate) | `execute()` rejects (`status: error`) output carrying proprietary design data (IP); the framework `@final` `_security_gate_output()` baseline still runs | Pass (`test_proprietary_output_blocked`, `test_proprietary_japanese_blocked`, `test_clean_output_passes`) |
| TC-11 | S-4: at least one domain `emit_trace_event()` inside each `execute()` | Domain event emitted on every invocation path | Pass (domain event: `test_rpn_emits_domain_event`; every node's S-4 lifecycle events verified by `test_call_order_for_every_node`) |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → EventEmitter | `emit_trace_event()` fires on every invocation path | No silent failures | Pass (`test_call_order_for_every_node` verifies `node_start`/`node_complete` for every node; domain event: `test_rpn_emits_domain_event`) |
| PB-2 | State serialization | Post-invoke State is primitives only | No Pydantic/dataclass | Pass (`test_state_file_safety`) |
| PB-3 | L1 → LLM adapter boundary | the LLM adapter is injected into the workflow nodes (runtime secret `AZURE_OPENAI_*`) | CI verifies the adapter input/output contract + failure propagation with a fake adapter (no live model call); the real LLM connection is verified at STG | Pass in CI (`_FakeLLM` in `test_end_to_end_invoke`; fail-closed: `test_high_priority_llm_unavailable_propagates`, `test_malformed_llm_propagates`); live connection = STG |
| PB-4 | Import isolation | No Level 0 imports | AST scan: 0 violations | Pass (`test_no_prohibited_imports_in_src`; CI `gate-import-isolation`) |
| PB-5 | Checkpoint safety | No JWT/Pydantic in checkpoint | Inspection pass | Pass (`test_state_file_safety` — flat JSON-serializable State) |
| PB-6 | Invoke execution order | `__call__()`: S-1 trust gate → S-4 `node_start` → S-2 `_security_gate_input` → `execute()` → S-3 `_security_gate_output` → S-4 `node_complete` | Order verified | Pass (`test_call_order_for_every_node`) |

*PB-7 (HITL interrupt propagation) is not applicable — `hitl.enabled` is not set (the SafetyGateCheck is a
static config-flag gate, not `interrupt()`). The required exact-name auto-skip stub
(`test_pb7_hitl_interrupt_propagation.py`) is present; it auto-skips while HITL is off and fails loudly
(not skips) if HITL is ever enabled without the interrupt-propagation assertion.*

**Workflow-boundary proofs (end-to-end `Graph.invoke`).** `test_pb_workflow.py` exercises the deployed
outer boundary of the approved workflow:
- **Required human-review posture reaches the caller** — a safety-critical `equipment_category` FMEA is
  marked review-required (`output.safety_gate.blocked` with a warning), and the always-on hash-only
  `audit_metadata` artifact reaches the caller; the composition runs in order (inner
  `FMEAWorkflowGraphNode` → outer `SecurityGateOutputNode`).
- **Fails closed** — a safety-critical category without acknowledgement and malformed input each return
  `status: error` with no partial FMEA (`error_strategy = propagate`). Classification keys on the
  `equipment_category` token — the maskable `equipment_name` is not used.
- **Credential-shaped content carried into an LLM-generated countermeasure is BLOCKED end-to-end** — the
  framework `@final` S-3 credential scan trips on the `countermeasure_generate` node's return; the outer
  `invoke` returns `status: error`, `get_output()["output"] is None`, and the credential body is not
  surfaced (BLOCK posture; no downstream redaction-to-success).

**Security-boundary proofs (S-2 / S-3).** Beyond the invoke-order proof (PB-6), the S-2 input gate and
S-3 output gate are proven at the `__call__` boundary — observable behavior, not fixture presence:
- `test_pb_input_gate.py` — the S-2 structural gate rejects non-text / binary / NUL / control-char /
  oversized raw `user_input` *before* `execute()` runs (each rejection spies `execute()` and asserts it
  was **not** invoked; a clean FMEA request reaches `execute()` and succeeds).
- `test_pb_output_gate.py` — the S-3 gate blocks an output carrying a proprietary / confidential
  design-IP marker (English or Japanese, top-level or nested → `status: error`), passes a clean FMEA
  output, and emits exactly one `output_gated` event (carrying whether the output was blocked).
- Prohibited-import isolation (PB-4) and State safety (PB-2/5) remain enforced by
  `test_no_prohibited_imports_in_src` and `test_state_file_safety`.

## Business Logic Tests

Every row maps to a `docs/02` Acceptance Criterion or Error & Edge case. Test references are
`file::function` (unit tests under `tests/unit/`).

| BL-ID | Test (case type) | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | Standard failure mode → FMEA table (normal) | equipment + process + failure events | ranked FMEA table (IATF 16949 columns), high-RPN first, with the provisional-draft marker; outer invoke returns a non-null table | Pass (`test_domain_services::test_markdown_ranked_high_rpn_first`, `test_domain_nodes::test_full_flow`, `test_graph_config_wiring::test_end_to_end_invoke`) |
| BL-02 | RPN = S×O×D + high-priority threshold (normal / boundary) | SOD ratings; RPN at / below / above threshold | RPN computed deterministically; `high_priority` flag when RPN ≥ `rpn_high_priority_threshold` (inclusive) | Pass (`test_domain_services::test_rpn_is_s_times_o_times_d/test_high_priority_flag/test_high_priority_threshold_inclusive`) |
| BL-03 | Multiple failure events → all processed (normal) | several failure events in one call | one FMEA row per event; the normalized row count must match the input event count | Pass (`test_domain_services::test_normalize_row_count_must_match_events/test_one_entry_per_failure_mode/test_normalize_stamps_stable_ids`) |
| BL-04 | `fmea_id` stable-identity binding (adversarial / fail-closed) | LLM output that drops / adds a foreign / duplicates / reorders `fmea_id` | reordered accepted (rebound by id); dropped / foreign / duplicate id fails closed; render joins by id, not array position | Pass (`test_domain_services::test_sod_dropped_id_rejected/test_sod_foreign_id_rejected/test_sod_duplicate_id_rejected/test_sod_reordered_ids_ok_and_stamped/test_render_joins_by_id_not_position/test_render_missing_id_rejected/test_render_mismatched_id_set_rejected/test_bijection_ok/test_dropped_id_fails_closed/test_foreign_id_fails_closed`) |
| BL-05 | SOD rating scale (boundary / invalid-input) | iatf_16949 (1–10) / iso_9001_simplified (1–5); out-of-range; unknown scale | valid scale accepted; out-of-range and unknown scale rejected | Pass (`test_domain_services::test_iso_scale_range/test_out_of_scale_range_rejected/test_unknown_rating_scale_rejected/test_missing_rating_rejected`, `test_generation_nodes::test_iso_scale_enforces_stricter_max/test_out_of_scale_rejected/test_unknown_scale_rejected`) |
| BL-06 | Countermeasure generation + classification (normal) | high-RPN failure modes / low-RPN only | LLM proposes one control per high-RPN mode (none needed when no high-RPN); each classified Preventive/Detective/Corrective, else Unclassified | Pass (`test_generation_nodes::test_high_priority_gets_proposed_control/test_no_high_priority_no_llm_needed/test_high_priority_llm_unavailable_propagates`, `test_domain_services::test_detective/test_preventive/test_corrective/test_unmatched_defaults_corrective/test_no_control_unclassified`) |
| BL-07 | Countermeasure is advisory; provisional disclaimer (human-review) | proposed control / no control; every output format | proposed control marked `approval_required: True`; no control → not approval-marked; the provisional "pending qualified-engineer approval" marker surfaces in every format | Pass (`test_domain_services::test_proposed_control_marked_advisory/test_no_control_not_approval_marked/test_provisional_disclaimer_on_every_output`) |
| BL-08 | `eu_ai_act_art9_mode: true` → Art.9 block injected (normal) | render / node with `eu_ai_act_art9_mode=True` vs `False` | EU AI Act Article 9 risk-management block injected when true; absent when false | Pass (`test_domain_services::test_safety_banner_and_eu_block`, `test_domain_nodes::test_full_flow` (art9=True → "Article 9"), `test_domain_nodes::test_non_critical_still_blocked` (art9=False)) |
| BL-09 | `output_format` markdown / csv / json (normal) | render in each format | valid Excel-compatible CSV (`Failure Mode,Severity` header); JSON `rows`; markdown default | Pass (`test_domain_services::test_csv_and_json/test_provisional_disclaimer_on_every_output`) |
| BL-10 | Safety gate — `equipment_category` classification, admission + withholding (security boundary) | safety-critical / non-critical / unknown / missing `equipment_category`; ack not-true / per-call true / config true; non-bool ack | classification keys on the `equipment_category` token (never the maskable `equipment_name`); a safety-critical category with ack not-true is **not admitted** — no `validated_input`, no FMEA — and is reported as a withheld draft (`status: success` + `diagnostic_code: safety_ack_required`) rather than a malfunction; per-call or config `true` → admitted; unknown / missing category → answered, never treated as non-critical; non-bool ack rejected at construction; no config override (fixed code-side set). Through the deployed `Graph.invoke()`: a safety-critical category is classified `safety_critical` (no fail-open when the name is masked), a non-critical category is still blocked (review-required), and an unacknowledged-critical or unknown category yields the notice with no `audit_metadata` — proving the inner workflow never ran | Pass (`test_input_validate_node::TestSafetyAcknowledgement`, `::TestEquipmentCategoryGate`, `test_domain_services::test_classifies_by_token_not_maskable_name`, `test_graph_config_wiring::TestGraphConfigWiring`, `test_graph_config_wiring::TestSafetyGateThroughInvoke::test_unacknowledged_safety_critical_is_withheld_through_invoke/test_unknown_category_is_answered_through_invoke`, `test_manifest_config::test_safety_gate_fires_through_the_graph_from_the_manifest`, `test_pb_workflow::TestWorkflowFailsClosed::test_safety_critical_without_ack_is_withheld_not_drafted`) |
| BL-11 | S-2 screen — document vs field (invalid-input / adversarial) | NUL byte / control-character flood / non-text type (whole body); control chars in `failure_events`, `process_description`, `equipment_name`; oversized; deep nesting; multiline text | the whole-body screen **refuses** (`status: error`) and `execute()` never runs; a field-level character contract and the size ceiling are **answered** (`diagnostic_code`, no table); multiline legitimate text accepted; no binary coercion; deep nesting answered, never a crash | Pass (`test_input_validate_node::TestS2Gate::test_s2_rejects_nul/test_s2_does_not_coerce_binary/test_s2_answers_oversized_without_parsing_it/test_s2_answer_does_not_let_a_body_be_parsed_afterwards`, `::TestRejections::test_failure_events_item_injection_rejected/test_process_description_injection_rejected/test_failure_events_item_multiline_ok/test_process_description_multiline_ok`, `::TestRecursionSafety::test_deep_nesting_does_not_crash`, `::TestEquipmentNameScreen::test_control_char_injection_rejected`, `test_pb_input_gate::TestS2InputGateBoundaryRejection`, `test_pb_workflow::TestWorkflowFailsClosed::test_a_refusal_this_agent_makes_still_fails_closed`) |
| BL-12 | S-3 proprietary-IP output block (security boundary) | output containing proprietary design data (EN / JP) / clean output | blocked (fail-closed) when proprietary IP detected; clean output passes | Pass (`test_generation_nodes::test_proprietary_output_blocked/test_proprietary_japanese_blocked/test_clean_output_passes`) |
| BL-13 | LLM fail-closed (error-path / non-determinism) | LLM unavailable / raises / malformed for normalize / SOD / countermeasure | propagate error; no partial or mis-aligned FMEA table returned as success | Pass (`test_generation_nodes::test_llm_unavailable_propagates/test_malformed_llm_propagates/test_missing_input_fails_closed/test_unanchored_failure_modes_fail_closed`) |
| BL-14 | Audit metadata — hash-only, always produced (audit) | audit-metadata assembly; a successful inner-graph invoke; a successful outer `Graph.invoke()` | hash-only `audit_metadata` (`data_hash`, generated_at, rating_scale, model version); raw table body never persisted; produced on every run, carried in the inner graph output, projected to outer State by `merge_output`, and surfaced to the caller by the outer `get_output()` (no external sink or toggle in V1) | Pass (artifact shape: `test_domain_services::test_hash_only_artifact`; inner graph output: `test_graph_config_wiring::test_inner_graph_invoke_directly`; projected to the outer invoke result: `test_graph_config_wiring::test_end_to_end_invoke`) |
| BL-15 | Composition + S-3 end-to-end (security boundary) | verified caller, valid input, fake LLM | outer `Graph.invoke()` runs through `FMEAWorkflowGraphNode` + `SecurityGateOutputNode`; the S-3 gate is the wired post_process | Pass (`test_graph_config_wiring::test_end_to_end_invoke/test_post_process_is_the_s3_gate/test_main_is_the_inner_workflow_graph_node`) |
| BL-16 | S-3 credential BLOCK end-to-end (security boundary) | credential-shaped token carried through an LLM-generated countermeasure into the FMEA rows | framework `@final` S-3 baseline scan BLOCKS fail-closed → outer invoke `status: error`, `get_output()["output"] is None`, credential body not surfaced (BLOCK posture; no downstream redaction-to-success) | Pass (`test_pb_workflow::TestS3CredentialBlockEndToEnd::test_credential_in_generated_control_blocks_with_no_output`) |

> **Audit trail:** there is no external/persistent audit-log sink or config toggle in V1; the
> audit-trail requirement is met by the always-on hash-only `audit_metadata_emit` (BL-14). (The LLM
> adapter is the only external dependency — see PB-3.)

## Additional Regression Coverage

Guards added alongside the changes in this revision. Each entry names the test and the
condition it accepts on; they supplement the mandatory TC / PB items above and replace none
of them.

| Concern | Test | Acceptance condition |
|---|---|---|
| Deployment startup contract | `tests/unit/test_entry_point_import.py` | `src/api/server.py` — the module `uvicorn src.api.server:app` loads — imports successfully and exposes `app` together with a compiled `agent` whose `config` is a mapping. A module-level failure there stops the deployment from starting while node- and service-level tests still pass, and no other test in the suite imports that module. |
| Error → terminal routing | `tests/unit/test_inner_error_short_circuit.py` | Every non-terminal hop of the inner workflow declares a conditional branch; an errored State selects the terminal branch (`END`), a healthy State advances to the next node, and no unconditional edge remains between two domain nodes. A node failure therefore schedules no downstream node and does not amplify the `error_log` reducer channel. |
| Provisional Stage ⑤ bounded fake | `tests/unit/test_stage5_bounded_fake.py` | The fake provider is selected only by the job-scoped CI opt-in; nothing in the shipped runtime config can reach it. The real node and its validators run against the fake's output, and a failing provider still surfaces as `status: error` with no partial result. The resulting evidence proves the deploy → invoke → workflow contract and explicitly does not prove that a live provider call succeeds. |


## Test Execution Summary
- Execution date: 2026-07-20
- Total tests: 165 (`pytest tests/` collected — parametrized cases expanded; `tests/unit` +
  `tests/proof_of_boundary`)
- Pass: 164 / Fail: 0 / Skip: 1 (PB-7 auto-skip stub — `hitl.enabled` not set)
- Evidence: GitLab CI `run-tests` job on the merge pipeline (`164 passed, 1 skipped`; all 12 jobs success);
  local `py_compile` of `src/` passes. Line-coverage is not gated in CI; every testable Acceptance
  Criterion above maps to ≥1 passing test.

## LLM Availability Matrix

Expected `status` and returned artifact for each state of the Azure OpenAI connection. The three
connection values (`AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_DEPLOYMENT`) are
resolved at call time from the invocation-bound `SecretProvider`. Declaring them in
`config/agent.yaml` `requires.secrets` states the deployment contract; it does not change any row
below. The FMEA draft is the deliverable, so an unavailable LLM is fail-closed.

| Condition | Expected `status` | Artifact returned | Result |
|-----------|-------------------|-------------------|--------|
| Connection values resolve, provider responds | `success` | FMEA draft for human review | Pass (`test_stage5_bounded_fake::test_canonical_payload_succeeds_with_working_llm`) |
| A connection value cannot be resolved | `error` | No output | Pass (`test_generation_nodes::test_llm_unavailable_propagates`) |
| Provider fails (error / timeout / malformed response) | `error` | No output | Pass (`test_generation_nodes::test_malformed_llm_propagates`, `test_generation_nodes::test_high_priority_llm_unavailable_propagates`) |
