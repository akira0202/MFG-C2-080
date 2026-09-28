"""Manifest / runtime-config test — prove the DEPLOYED configuration provides the
documented InputValidate default (safety_review_acknowledged), so future docs↔config
drift fails CI (requires the framework — runs in CI).

``config/agent.yaml`` is the static discovery manifest (identity + a single dotted class:
path); ``config/config.yaml`` carries the runtime parameters AgentRegistry passes as
``Graph(config=...)`` and ``src/api/server.py`` loads on the standalone path.
"""

import json
from pathlib import Path

import yaml

from framework.schemas.agent_status import AgentStatus

from src.graph.graph import EquipmentFMEADraftGeneratorAgentGraph as Graph

_MANIFEST = Path(__file__).resolve().parents[2] / "config" / "agent.yaml"
_CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _manifest_config() -> dict:
    # Runtime parameters live in config/config.yaml, never in the static manifest.
    return yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))


def _req(**extra):
    d = {
        "equipment_name": "CNC lathe",
        "equipment_category": "general_equipment",
        "process_description": "turning",
        "failure_events": ["spindle overheating"],
    }
    d.update(extra)
    return json.dumps(d)


class TestManifestConfig:
    def test_manifest_declares_safety_ack_default_as_bool(self):
        cfg = _manifest_config()
        assert isinstance(cfg.get("safety_review_acknowledged"), bool)

    def test_manifest_config_builds_and_runs_through_the_graph(self):
        cfg = _manifest_config()
        g = Graph(config=cfg)
        g.register_nodes()  # a non-bool safety_review_acknowledged would raise ConfigError here
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _req(), "correlation_id": "m"})  # non-critical equipment
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["validated_input"]["safety_review_acknowledged"] == cfg["safety_review_acknowledged"]

    def test_safety_gate_fires_through_the_graph_from_the_manifest(self):
        # config.yaml ships NO safety list, so the node's secure code-side default must keep
        # the gate active: a safety-critical equipment with no ack → rejected via the DEPLOYED
        # manifest path (proves the gate is not fail-open in a zero-config deployment)
        cfg = _manifest_config()
        assert "safety_critical_equipment_categories" not in cfg  # gate is code-side, not operator-configurable
        g = Graph(config=cfg)
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute({"user_input": _req(equipment_category="hydraulic_press"), "correlation_id": "m"})
        # The gate holds: the request is NOT admitted (no validated_input). It is reported as a
        # withheld draft rather than a malfunction, which is what the caller is told.
        assert r["status"] == AgentStatus.SUCCESS.value
        assert r["diagnostic_code"] == "safety_ack_required"
        assert "validated_input" not in r

    def test_manifest_safety_critical_accepted_with_per_call_ack(self):
        cfg = _manifest_config()
        g = Graph(config=cfg)
        g.register_nodes()
        node = g._nodes["pre_process"]
        r = node.execute(
            {
                "user_input": _req(equipment_category="hydraulic_press", safety_review_acknowledged=True),
                "correlation_id": "m",
            }
        )
        assert r["status"] == AgentStatus.SUCCESS.value


class TestManifestIdentity:
    def test_manifest_declares_importable_class(self):
        import importlib
        from pathlib import Path
        import yaml

        root = Path(__file__).resolve().parents[2]
        # Root-level AgentRegistry schema (no `agent:` wrapper).
        m = yaml.safe_load((root / "config" / "agent.yaml").read_text(encoding="utf-8"))
        assert m["base_type"] == "Other"
        # Single dotted class path (module.Class) — no legacy module:/class: split.
        assert "module" not in m, "manifest must use a single dotted class:, not module:/class:"
        module_path, class_name = m["class"].rsplit(".", 1)
        mod = importlib.import_module(module_path)
        assert hasattr(mod, class_name), f"manifest class {m['class']} not importable"
        declared = m.get("requires", {}).get("secrets", [])
        for key in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"):
            assert key in declared, f"{key} is resolved in src/ but not declared in requires.secrets"

    def test_no_unresolved_placeholders(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        token = re.compile(r"\{\{[A-Z0-9_]+\}\}")
        targets = list((root / "config").rglob("*.yaml")) + list((root / "src").rglob("*.py")) + [root / "README.md"]
        offenders = [
            str(p.relative_to(root))
            for p in targets
            if "__pycache__" not in str(p) and token.search(p.read_text(encoding="utf-8"))
        ]
        assert offenders == [], f"unresolved placeholders: {offenders}"
