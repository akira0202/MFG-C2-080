"""AgentCore Platform v1.0 — deployment startup contract for MFG-C2-080.

Single place that turns a deployment into a running agent: load the runtime config
(`config/config.yaml`), select the deployment profile, and compile. `src/api/server.py` calls it
and holds no logic.

The graph and its nodes read only `self.config` and what is injected through it. Profile
selection — the one thing that legitimately depends on where the process is running — lives here,
at the startup boundary, so the agent's business logic never reads the process environment.

No import-time side effects: nothing is constructed on import, so tests can exercise every branch
without triggering the deployment construction in `server.py`.
"""

import os
import pathlib
from typing import Any

from framework.utils.config_loader import load_config

from src.graph.graph import EquipmentFMEADraftGeneratorAgentGraph

_CONFIG_YAML = pathlib.Path(__file__).resolve().parents[2] / "config" / "config.yaml"

# The provisional Stage 5 CI profile. Set only by the rehearsal job and never
# on a real environment.
_STAGE5_BOUNDED_FAKE_ENV = "AGENT_STAGE5_BOUNDED_FAKE"


def load_runtime_config() -> dict[str, Any]:
    """Load `config/config.yaml` — the runtime parameters passed as Graph(config=...).

    The static AgentRegistry manifest (`config/agent.yaml`) holds only discovery/registry keys.
    An absent file yields {}, which leaves every runtime parameter at its code default.
    """
    if not _CONFIG_YAML.exists():
        return {}
    return dict(load_config(str(_CONFIG_YAML)))


def bounded_fake_profile_enabled() -> bool:
    """True only under the explicit CI opt-in. Nothing in `config/config.yaml` can turn the fake
    on: the shipped config carries no `llm` key at all, so a deployment always gets the real
    provider adapter the graph injects by default."""
    return os.environ.get(_STAGE5_BOUNDED_FAKE_ENV) == "1"


def bounded_fake_profile() -> dict[str, Any]:
    """Return the config overlay for the provisional Stage 5 profile.

    The fake travels under `llm`, the same config key a caller would use to inject any other
    adapter — `graph.py` already prefers a supplied `llm` over its default. Imported lazily so
    the CI-only fake is not loaded on a production start.
    """
    from src.services.stage5_fakes import FakeFMEALLM

    return {"llm": FakeFMEALLM()}


def build_agent(config_overrides: dict[str, Any] | None = None) -> EquipmentFMEADraftGeneratorAgentGraph:
    """Build and compile the agent from the runtime config plus any injected objects.

    With no bounded-fake profile and no overrides, the graph injects its default keyless provider
    adapter, which resolves `AZURE_OPENAI_*` from the request-bound SecretProvider at call time —
    the shipped behaviour. Unit tests pass their doubles through `config_overrides`.
    """
    cfg = load_runtime_config()
    if bounded_fake_profile_enabled():
        cfg.update(bounded_fake_profile())
    if config_overrides:
        cfg.update(config_overrides)

    agent = EquipmentFMEADraftGeneratorAgentGraph(config=cfg)
    agent.compile()
    return agent
