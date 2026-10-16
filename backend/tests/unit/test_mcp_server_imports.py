import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_MODULES = ("app.config", "app.db.mcp_password")
DECISION_MODULES = ("app.services.correction_decisions", "app.graph.correction_graph", "psycopg")


def _import_check(modules: tuple[str, ...]) -> str:
    return (
        "import sys, mcp_server.server, mcp_server.settings; "
        f"loaded = [m for m in {modules!r} if m in sys.modules]; "
        "assert not loaded, loaded"
    )


def _run_import_check(modules: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _import_check(modules)],
        env={**os.environ, "PYTHONPATH": "backend" + os.pathsep + "."},
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_mcp_server_import_does_not_load_admin_settings() -> None:
    result = _run_import_check(ADMIN_MODULES)

    assert result.returncode == 0, result.stderr


def test_mcp_server_import_does_not_load_decision_modules() -> None:
    result = _run_import_check(DECISION_MODULES)

    assert result.returncode == 0, result.stderr
