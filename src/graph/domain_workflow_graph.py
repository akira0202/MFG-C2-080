"""AgentCore Platform v1.0 — inner MFG FMEA domain workflow (State + composition) for MFG-C2-080.

This module owns the inner-graph State schema (`DomainWorkflowState`) and the inner `BaseGraph`
composition: the LLM failure-mode-normalize / SOD-rating / countermeasure-generate nodes + the
deterministic RPN / countermeasure-classify / safety-gate / FMEA-table nodes + the S-4 audit node,
wrapped by `FMEAWorkflowGraphNode` (the outer `main` slot). Cat 2 two-layer.
"""

import json
from typing import Any, ClassVar, NotRequired

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

from src.nodes.audit_metadata_emit_node import AuditMetadataEmitNode
from src.nodes.countermeasure_classify_node import CountermeasureClassifyNode
from src.nodes.countermeasure_generate_node import CountermeasureGenerateNode
from src.nodes.failure_mode_normalize_node import FailureModeNormalizeNode
from src.nodes.fmea_table_generate_node import FMEATableGenerateNode
from src.nodes.input_hydrate_node import InputHydrateNode
from src.nodes.rpn_calculate_node import RPNCalculateNode
from src.nodes.safety_gate_check_node import SafetyGateCheckNode
from src.nodes.sod_rating_compute_node import SodRatingComputeNode
from src.services import audit_metadata
from src.services.progress import emit_progress


class DomainWorkflowState(AgentState):
    """Inner State for the MFG FMEA domain workflow (Cat 2 two-layer).

    Flat TypedDict — primitives / JSON-serializable types only (msgpack-safe; no
    Pydantic / dataclass / arbitrary objects). Framework fields are inherited from
    AgentState and are NOT redeclared (user_input / validated_input / result /
    formatted_output / status / node_history / error_log). Reducer-managed fields are
    NEVER projected inner -> outer.

    `failure_modes` / `sod_ratings` are produced by the LLM normalize / SOD nodes;
    `audit_metadata` by the S-4 audit node. The deterministic nodes produce
    `rpn_results` / `countermeasures` / `safety_gate` / `fmea_table`.
    """

    # failure_mode_normalize / sod_rating_compute (LLM)
    failure_modes: NotRequired[list[Any]]  # {fmea_id, function, failure_mode, effect, cause, recommended_control?}
    sod_ratings: NotRequired[list[Any]]  # {fmea_id, severity, occurrence, detection, approval_required}
    # rpn_calculate
    rpn_results: NotRequired[list[Any]]  # {fmea_id, rpn, high_priority, severity, occurrence, detection}
    # countermeasure_classify
    countermeasures: NotRequired[list[Any]]  # {fmea_id, control, type: Preventive/Detective/Corrective}
    # safety_gate_check
    safety_gate: NotRequired[dict[str, Any]]  # {safety_critical, warning, blocked}
    # fmea_table_generate
    fmea_table: NotRequired[str]  # rendered FMEA table (per output_format)
    # audit_metadata_emit
    audit_metadata: NotRequired[dict[str, Any]]  # S-4 artifact


class DomainWorkflowGraph(BaseGraph):
    """Inner FMEA workflow (Cat 2 inner BaseGraph). Linear topology; the LLM adapter and per-node
    config flow in via the graph config (from the outer GraphNode)."""

    @property
    def name(self) -> str:
        return "equipment_fmea_workflow"

    @property
    def state_schema(self) -> type:
        return DomainWorkflowState

    def _validate_config(self) -> None:
        # No mandatory inner config: a missing LLM is a fail-closed runtime error at the LLM nodes
        # (error_strategy = propagate), not a construction error.
        pass

    def register_nodes(self) -> None:
        cfg = self.config or {}
        llm = cfg.get("llm")
        self._nodes["input_hydrate"] = InputHydrateNode()
        self._nodes["failure_mode_normalize"] = FailureModeNormalizeNode(llm=llm)
        self._nodes["sod_rating_compute"] = SodRatingComputeNode(node_config=cfg, llm=llm)
        self._nodes["rpn_calculate"] = RPNCalculateNode(node_config=cfg)
        self._nodes["countermeasure_generate"] = CountermeasureGenerateNode(node_config=cfg, llm=llm)
        self._nodes["countermeasure_classify"] = CountermeasureClassifyNode(node_config=cfg)
        self._nodes["safety_gate_check"] = SafetyGateCheckNode(node_config=cfg)
        self._nodes["fmea_table_generate"] = FMEATableGenerateNode(node_config=cfg)
        self._nodes["audit_metadata_emit"] = AuditMetadataEmitNode(node_config=cfg)

    # Inner hop order. Each hop is conditional on the running status: a node that returned
    # status:error routes straight to END.
    _CHAIN = (
        "input_hydrate",
        "failure_mode_normalize",
        "sod_rating_compute",
        "rpn_calculate",
        "countermeasure_generate",
        "countermeasure_classify",
        "safety_gate_check",
        "fmea_table_generate",
        "audit_metadata_emit",
    )

    def add_edges(self) -> None:
        # Every hop is conditional: a node returning status:error routes to END (no partial
        # output), otherwise it advances to the next inner node. An unconditional chain would keep
        # invoking the remaining nodes after a failure — each one is then skipped by the framework
        # input gate, which returns the whole State, and `error_log` (an operator.add channel) is
        # concatenated onto itself once per remaining node, doubling the audit trail at each hop.
        self._sg.add_edge(START, self._CHAIN[0])
        for node, nxt in zip(self._CHAIN, self._CHAIN[1:]):
            self._sg.add_conditional_edges(
                node,
                self._continue_or_end,
                {"continue": nxt, "end": END},
            )
        self._sg.add_edge(self._CHAIN[-1], END)

    def _continue_or_end(self, state: AgentState) -> str:
        """END on any node error; otherwise advance to the next inner node."""
        return "end" if state.get("status") == AgentStatus.ERROR.value else "continue"

    def route(self, state: AgentState) -> str:
        # Required by the BaseGraph ABC; the per-hop edges use _continue_or_end().
        return self._continue_or_end(state)

    def get_output(self, state: AgentState) -> dict[str, Any]:
        """Shape the sub_result for the outer GraphNode. Key success off the rendered FMEA table
        (not a status enum/string compare): if it was produced, project the human-review output +
        the audit artifact; otherwise carry the error so a failed inner run propagates."""
        status = state.get("status")
        fmea_table = state.get("fmea_table")
        if isinstance(fmea_table, str) and fmea_table:
            payload = audit_metadata.build_output(fmea_table=fmea_table, safety_gate=state.get("safety_gate"))
            # expose the payload under both keys — the outer GraphNode merge reads one of them
            return {
                "status": status,
                "output": payload,
                "result": payload,
                "audit_metadata": state.get("audit_metadata"),
            }
        return {"status": "error", "error_log": state.get("error_log", [])}


class FMEAWorkflowGraphNode(GraphNode):
    """Outer `main`-slot GraphNode wrapping the inner DomainWorkflowGraph. error_strategy =
    propagate: an inner failure re-raises as SubgraphError (no partial output)."""

    error_strategy: ClassVar[str] = "propagate"

    def __init__(self, node_config: dict[str, Any] | None = None) -> None:
        super().__init__()
        self._config = node_config or {}
        self._subgraph = DomainWorkflowGraph(config=self._config)

    def get_subgraph(self) -> DomainWorkflowGraph:
        return self._subgraph

    def execute(self, state: AgentState) -> dict[str, Any]:
        # The input gate answers a request the caller can correct, and withholds a draft where the
        # safety-review acknowledgement is absent. No table is built for either. Running the
        # workflow here would draft for a request that was never admitted — and its first node
        # would fail on the absent handoff, reporting the missing input as this agent's own fault.
        # Skip it and carry the code to post_process.
        code = state.get("diagnostic_code")
        if code:
            emit_trace_event("workflow_skipped", {"correlation_id": state.get("correlation_id"), "reason": code}, state)
            return {"status": AgentStatus.SUCCESS.value, "diagnostic_code": code}
        # Annotated rather than returned directly: GraphNode ships without type information, so
        # super().execute() is Any and a bare return would widen this method's declared dict.
        result: dict[str, Any] = super().execute(state)
        return result

    def extract_input(self, state: AgentState) -> str:
        # Hand validated_input to the inner graph as a JSON string; the inner input_hydrate decodes
        # it (BaseGraph.invoke sets only user_input, not validated_input).
        emit_progress("Drafting the FMEA table...")
        return json.dumps(state.get("validated_input", {}), ensure_ascii=True)

    def merge_output(self, state: AgentState, sub_result: dict[str, Any]) -> dict[str, Any]:
        payload = sub_result.get("output") or sub_result.get("result")
        return {
            "result": payload,
            "audit_metadata": sub_result.get("audit_metadata"),
            "status": sub_result.get("status"),
        }
