"""AgentCore Platform v1.0"""

# ─────────────────────────────────────────────────────────────────────────────
# Template Category (Cat) — choose ONE based on your Cat judgment:
#
# Cat 1 — Single technical capability (use-case-agnostic)
#   Purpose : Delivers one reusable, domain-independent capability.
#             The same template can be dropped into any project unchanged.
#   Examples: TextSummarizer, EmbeddingGenerator, LanguageDetector,
#             SentimentAnalyzer, KeywordExtractor
#   Parent  : AgentBaseGraph
#   Pipeline: START → initialize → pre_process → main → {route} → post_process → finalize → END
#                                             ↓ (RETRY, max 3)
#                                          pre_process
#
# Cat 2 — Multi-step domain workflow (job-to-be-done)
#   Purpose : Orchestrates multiple steps to accomplish a specific business
#             outcome. The template name describes the outcome, not the
#             individual capabilities it uses.
#   Examples: InvoiceTriageAgent, ContractGapDetectionAgent,
#             ResumeScreeningAgent, SupportTicketResolutionAgent
#   Parent  : AgentBaseGraph (outer graph) + GraphNode in the `main` slot
#             wrapping an inner BaseGraph/AgentBaseGraph (domain workflow)
#   Pipeline: Same fixed 5-node backbone as Cat 1; domain complexity is
#             encapsulated inside DomainWorkflowGraphNode.get_subgraph().
#   Layout  : src/graph/graph.py                 ← outer graph (this file)
#             src/graph/domain_workflow_graph.py ← inner graph
#
# Cat 3 — Autonomous think→act→observe loop (self-directed)
#   Purpose : Runs an LLM-driven loop that decides its own next action,
#             executes tools, observes results, and terminates when the task
#             is complete or a budget/iteration ceiling is hit.
#   Examples: ResearchAgent, AutonomousCodeReviewAgent,
#             DataExplorationAgent, MultiStepPlannerAgent
#   Parent  : AutonomousBaseGraph
#   Pipeline: START → initialize → think ⇄ act → finalize → END
#   Config  : budget_usd and llm are required in config.yaml.
#
# ── How to choose ─────────────────────────────────────────────────────────────
#
#   Ask: "Does this template solve a specific business problem end-to-end?"
#     No  → Cat 1 (generic capability)
#     Yes → "Does it need an autonomous reasoning loop to decide its steps?"
#       No  → Cat 2 (fixed multi-step workflow)
#       Yes → Cat 3 (LLM-driven loop)
#
# framework.* imports are unchanged; agent-local imports use the src. prefix.
# ─────────────────────────────────────────────────────────────────────────────

from typing import Any

from framework.graph.agent_base_graph import AgentBaseGraph
from src.graph.domain_workflow_graph import FMEAWorkflowGraphNode
from src.nodes.input_validate_node import InputValidateNode
from src.nodes.security_gate_output_node import SecurityGateOutputNode
from src.schemas.state import State
from src.services.llm import AzureOpenAIFMEALLM


class EquipmentFMEADraftGeneratorAgentGraph(AgentBaseGraph):
    """Fixed-pipeline graph."""

    @property
    def name(self) -> str:
        return "EquipmentFMEADraftGeneratorAgent"

    @property
    def state_schema(self) -> type:
        return State

    def get_output(self, state: dict[str, Any]) -> dict[str, Any]:
        # Surface the S-4 audit_metadata compliance artifact to the caller alongside the default
        # output. AgentBaseGraph.get_output returns only output/status/trace/correlation/node_history;
        # merge_output projected audit_metadata from the inner workflow into the outer State, so expose
        # it here (hash-only; the FMEA draft itself is under "output").
        out: dict[str, Any] = super().get_output(state)
        out["audit_metadata"] = state.get("audit_metadata")
        # An answered request completes carrying its code instead of a table, so surface the
        # code alongside the notice that post_process wrote to formatted_output.
        code = state.get("diagnostic_code")
        if code:
            out["diagnostic_code"] = code
        return out

    def register_nodes(self) -> None:
        # super() injects "initialize" and "finalize" slots automatically.
        # They are shown here as sample code only — do NOT re-register them
        # unless you are providing a custom subclass.
        #
        # Sample (for reference — already injected by super()):
        #   self._nodes["initialize"] = InitializeNode()   # sets schema_version,
        #                                                    # session_id, trust_level
        #   self._nodes["finalize"]   = FinalizeNode()     # builds response_metadata,
        #                                                    # total_time_ms
        #
        # To customise: subclass InitializeNode / FinalizeNode and override
        # on_initialize() / on_finalize() respectively; then register here:
        #   self._nodes["initialize"] = CustomInitializeNode()
        #   self._nodes["finalize"]   = CustomFinalizeNode()
        super().register_nodes()  # injects InitializeNode + FinalizeNode

        # Domain pipeline slots (required — fill all three). The agent.yaml config
        # (BaseGraph.config) is injected into InputValidateNode so its documented
        # safety-acknowledgement precedence (per-call → config default) is the DEPLOYED
        # behavior. A non-bool config safety_review_acknowledged raises ConfigError here.
        self._nodes["pre_process"] = InputValidateNode(node_config=self.config)
        # main = the inner FMEA workflow (Cat 2 GraphNode). The FMEA LLM adapter flows down via the
        # inner-graph config: use a caller-supplied `llm` (tests inject a fake), else the default
        # keyless AzureOpenAIFMEALLM — it resolves AZURE_OPENAI_* per-request at call time, so a config-less
        # deploy (server.py constructs Graph()) still has a working adapter for the inner LLM nodes
        # (failure_mode_normalize / sod_rating_compute / countermeasure_generate).
        cfg = dict(self.config or {})
        cfg.setdefault("llm", None)
        if cfg["llm"] is None:
            cfg["llm"] = AzureOpenAIFMEALLM()
        self._nodes["main"] = FMEAWorkflowGraphNode(node_config=cfg)
        # S-3 output gate — block proprietary design data (IP); one output_gated event.
        self._nodes["post_process"] = SecurityGateOutputNode(node_config=self.config)
