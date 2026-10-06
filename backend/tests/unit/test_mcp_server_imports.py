import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ADMIN_MODULES = ("app.config", "app.db.mcp_password")
IMPORT_CHECK = (
    "import sys, mcp_server.server, mcp_server.settings; "
    f"loaded = [m for m in {ADMIN_MODULES!r} if m in sys.modules]; "
    "assert not loaded, loaded"
)


def test_mcp_server_import_does_not_load_admin_settings() -> None:
    result = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK],
        env={**os.environ, "PYTHONPATH": "backend" + os.pathsep + "."},
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
