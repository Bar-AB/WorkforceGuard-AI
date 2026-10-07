import os

import pytest
from langsmith import utils as langsmith_utils

TRACING_VARS = (
    "LANGSMITH_TRACING_V2",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
)


def test_tracing_off_fixture_sets_every_tracing_var_false() -> None:
    assert {name: os.environ.get(name) for name in TRACING_VARS} == dict.fromkeys(
        TRACING_VARS, "false"
    )


def test_tracing_stays_off_when_langchain_tracing_v2_is_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")

    assert langsmith_utils.tracing_is_enabled() is False
