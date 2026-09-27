import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

FUNCTION_LEVEL_IMPORT = """def load() -> str:
    import json

    return json.dumps({})
"""

TOP_LEVEL_IMPORT = """import json


def load() -> str:
    return json.dumps({})
"""


def run_ruff_check(source: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    target = tmp_path / "sample.py"
    target.write_text(source)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(REPO_ROOT / "pyproject.toml"),
            "--no-cache",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_ruff_when_import_inside_function_reports_plc0415(tmp_path: Path) -> None:
    result = run_ruff_check(FUNCTION_LEVEL_IMPORT, tmp_path)

    assert result.returncode == 1
    assert "PLC0415" in result.stdout


def test_ruff_when_import_at_top_passes(tmp_path: Path) -> None:
    result = run_ruff_check(TOP_LEVEL_IMPORT, tmp_path)

    assert result.returncode == 0, result.stdout
