"""Guard: every name the HA integration imports from ``pyvaonis`` must actually be exported.

CI's pyright only type-checks the bundled ``pyvaonis`` library + tests (not the HA platform files,
since ``homeassistant`` isn't installed), and nothing imports the platforms at test time — so a
missing export (e.g. ``from .pyvaonis import visible_tonight``) only blows up at HA runtime. This
statically parses the HA files and checks each ``.pyvaonis[...]`` import resolves against the real
(installed) package.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

HA_DIR = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "vaonis"


def _pyvaonis_imports() -> list[tuple[str, str, str]]:
    """(file, module, name) for every ``from .pyvaonis[...] import name`` in the HA integration."""
    found: list[tuple[str, str, str]] = []
    for py in sorted(HA_DIR.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 1
                and (node.module or "").split(".")[0] == "pyvaonis"
            ):
                for alias in node.names:
                    found.append((py.name, node.module or "pyvaonis", alias.name))
    return found


def test_ha_pyvaonis_imports_resolve() -> None:
    imports = _pyvaonis_imports()
    assert imports, "expected the HA files to import from pyvaonis"
    missing = [
        f"{file}: from .{module} import {name}"
        for file, module, name in imports
        if not hasattr(importlib.import_module(module), name)
    ]
    assert not missing, "HA imports not exported by pyvaonis:\n" + "\n".join(missing)
