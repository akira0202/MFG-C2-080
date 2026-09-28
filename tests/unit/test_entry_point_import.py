"""Deployment entry-point smoke test.

`src/api/server.py` is what `uvicorn src.api.server:app` loads. Importing it runs every
module-level statement: the runtime config is read, the graph is built and compiled, and the
secret provider is provisioned. A module-level failure there — an import that does not resolve
against the installed framework, a renamed symbol — means the deployment does not start, while
every unit test that exercises nodes and services in isolation still passes.

No other test in this suite imports that module, so this file is the only thing standing between
such a failure and a deployment. The import is the assertion.
"""

import importlib


def test_entry_point_module_imports():
    module = importlib.import_module("src.api.server")

    assert module.app is not None
    assert module.agent is not None


def test_entry_point_loads_the_runtime_config():
    """The graph receives the parsed runtime config, not the empty default a config-less
    construction would leave behind."""
    module = importlib.import_module("src.api.server")

    assert isinstance(module.agent.config, dict)
