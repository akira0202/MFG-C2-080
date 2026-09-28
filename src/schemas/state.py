"""AgentCore Platform v1.0"""

# ADR-005: State must be a flat TypedDict — never Pydantic BaseModel.
# LangGraph checkpoints use msgpack serialization; Pydantic objects
# cause silent corruption.  Extend AgentState with agent-specific
# fields only.  Do NOT add credentials, secrets, or Pydantic models.

from typing import Any, NotRequired

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Outer State for EquipmentFMEADraftGeneratorAgent (Cat 2).

    Flat TypedDict — primitives / JSON-serializable types only (no
    Pydantic, dataclass, or arbitrary Python objects; msgpack-safe).

    Only outer-graph-specific fields are declared here. Framework fields
    are inherited from AgentState (which itself extends SubgraphContext)
    and are NOT redeclared:
      - user_input: str  — raw caller payload; JSON is passed as a string
      - validated_input  — set by the outer pre_process (input_validate)
      - result / formatted_output — terminal output; AgentBaseGraph.get_output()
        returns formatted_output or result
      - status, session_id, schema_version, response_metadata, and the
        reducer-managed node_history / error_log

    Cat 2 two-layer: the domain pipeline runs in the inner
    DomainWorkflowGraph (src/graph/domain_workflow_graph.py), which owns its
    own DomainWorkflowState holding the intermediate domain fields
    (failure_modes, sod_ratings, rpn_results, countermeasures, safety_gate,
    fmea_table). The main GraphNode's merge_output() projects the inner
    output into the inherited `result` (plus audit_metadata below);
    node_history / error_log are reducer-managed and are NEVER projected
    inner -> outer.

    The final S-3-gated structured result is written to the inherited
    `result`. No credentials / InvocationContext / Pydantic in State.
    """

    # Set when the input gate answered a request instead of refusing it — a request the caller
    # can correct, or a draft withheld on policy. The run completes with this allow-listed code
    # instead of an FMEA table, and post_process releases the sentence the code names.
    diagnostic_code: NotRequired[str]

    # Audit metadata projected from the inner workflow (S-4 artifact).
    audit_metadata: NotRequired[dict[str, Any]]  # {data_hash, generated_at, rating_scale,
    #  model_version}
