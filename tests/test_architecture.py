"""Prevent future imports from turning the layers inside out."""

import ast
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app"
FORBIDDEN = {
    "domain": ("app.application", "app.infrastructure", "app.presentation", "app.bootstrap", "app.api", "app.worker"),
    "application": ("app.infrastructure", "app.presentation", "app.bootstrap", "app.api", "app.worker"),
    "infrastructure": ("app.presentation", "app.bootstrap", "app.api", "app.worker"),
    "presentation": ("app.infrastructure", "app.bootstrap", "app.api", "app.worker"),
}


def test_imports_follow_layer_direction():
    violations = []
    for layer, forbidden in FORBIDDEN.items():
        for path in (APP / layer).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                elif isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                else:
                    continue
                for module in modules:
                    if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
                        violations.append(f"{path.relative_to(APP)}:{node.lineno} imports {module}")
    assert not violations, "Layer dependency violations:\n" + "\n".join(violations)
