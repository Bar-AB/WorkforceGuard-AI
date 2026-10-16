import ast
from pathlib import Path
from typing import Final

import pytest

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
SCANNED_ROOTS: Final = (REPO_ROOT / "backend" / "app", REPO_ROOT / "mcp_server")
DECISION_SERVICES: Final = frozenset({"approve_correction", "reject_correction"})
ALLOWED_CALLER: Final = REPO_ROOT / "backend" / "app" / "graph" / "correction_graph.py"


def _names_decision_service(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id in DECISION_SERVICES
    if isinstance(node, ast.Attribute):
        return node.attr in DECISION_SERVICES
    if isinstance(node, ast.alias):
        return node.name.rpartition(".")[2] in DECISION_SERVICES
    if isinstance(node, ast.Constant):
        return node.value in DECISION_SERVICES
    return False


def _references_decision_service(source: str) -> bool:
    return any(_names_decision_service(node) for node in ast.walk(ast.parse(source)))


@pytest.mark.parametrize(
    "source",
    [
        "approve_correction(conn)",
        "decisions.reject_correction(conn)",
        "from app.services.correction_decisions import approve_correction as go",
        "import app.services.correction_decisions.reject_correction",
        "handler = decisions.approve_correction",
        "getattr(decisions, 'reject_correction')(conn)",
    ],
)
def test_decision_service_reference_is_detected(source: str) -> None:
    assert _references_decision_service(source)


def test_unrelated_names_are_not_decision_references() -> None:
    assert not _references_decision_service("approve = reject = 'correction'")


def test_only_correction_graph_calls_decision_services() -> None:
    sources = [path for root in SCANNED_ROOTS for path in root.rglob("*.py")]

    callers = [
        path for path in sources if _references_decision_service(path.read_text(encoding="utf-8"))
    ]

    assert callers == [ALLOWED_CALLER]
