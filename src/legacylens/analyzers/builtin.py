"""Built-in analyzer loader.

Registration happens as a module-import side effect (the @registry.register
decorator), so making it explicit and idempotent here keeps startup
deterministic: the API service and CLI call load_builtin_analyzers() once;
tests import individual analyzer modules directly.
"""

import importlib

_BUILTIN_MODULES = (
    "legacylens.analyzers.manifest_deps",
    "legacylens.analyzers.module_graph",
    "legacylens.analyzers.tech_detection",
    "legacylens.analyzers.tech_eol",
    "legacylens.analyzers.dep_vulns",
    "legacylens.analyzers.architecture",
    "legacylens.analyzers.config_analysis",
    "legacylens.analyzers.db_analysis",
    "legacylens.analyzers.tech_debt",
)


def load_builtin_analyzers() -> None:
    for module in _BUILTIN_MODULES:
        importlib.import_module(module)
