"""Guard: every name the HA integration imports from ``pyvaonis`` must actually be exported.

pyright type-checks the whole integration, but as belt-and-suspenders this also *imports* the real
(installed) package and checks each ``from .pyvaonis[...] import name`` resolves at runtime — the
exact failure mode (a missing export like ``visible_tonight``) that takes the integration down on
load. Cheap, and independent of the type checker.
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
