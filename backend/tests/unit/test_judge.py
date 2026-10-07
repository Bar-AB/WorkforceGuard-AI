import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import LlmSettings
from app.llm.explain import finding_payload
from app.llm.provider import (
    ChatMessage,
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    ResponseSchema,
)
from evals import judge
from evals.judge import (
    BASELINES_PATH,
    CI_SAMPLE_SIZE,
    CaseResult,
    JudgeCase,
    Verdict,
    case_score,
    judge_case,
    judge_score,
    judge_shortfalls,
    load_judge_baseline,
    load_judge_cases,
    main,
    render_judge_report,
)
from tests.fake_llm import FAKE_EXPLANATION, FakeLLM

OVERTIME_EVIDENCE_KEYS = {"limit", "hours", "max_hours", "source_table", "source_id"}
SOURCE_ID = "0b6f3c1a-7d2e-4f5a-9c8b-1e2d3f4a5b6c"
CASE = JudgeCase(
    id="daily-13h",
    rule_id="overtime_breach",
    severity="high",
    occurred_on=date(2026, 3, 2),
    summary="Worked 13.00 h, daily limit 12 h.",
    evidence={
        "limit": "daily",
        "hours": "13.00",
        "max_hours": "12",
        "source_table": "attendance_events",
        "source_id": SOURCE_ID,
    },
)
FAITHFUL = json.dumps({"faithful": True, "invented_facts": False, "reason": "Matches."})
EXPLANATION = json.dumps({"text": FAKE_EXPLANATION})


class DownLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[ChatMessage], schema: ResponseSchema
    ) -> LLMResponse:
        self.calls += 1
        raise LLMUnavailableError("down")


def _result(case_id: str, score: float) -> CaseResult:
    return CaseResult(
        case_id=case_id, explanation="Some text.", source="llm", score=score, reason="ok"
    )


def _data_json(message: ChatMessage) -> dict[str, object]:
    body = message.content.removeprefix("<data>\n").removesuffix("\n</data>")
    parsed: dict[str, object] = json.loads(body)
    return parsed


def test_load_judge_cases_sample_takes_first_three() -> None:
    sample = load_judge_cases(full=False)

    assert [case.id for case in sample] == [case.id for case in load_judge_cases(full=True)][:3]
    assert len(sample) == CI_SAMPLE_SIZE == 3


def test_load_judge_cases_full_takes_all() -> None:
    cases = load_judge_cases(full=True)

    assert len(cases) == 10
    assert len({case.id for case in cases}) == 10


def test_load_judge_cases_rejects_malformed_line(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id": "broken", "rule_id": "overtime_breach"}\n', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_judge_cases(full=True, dataset=dataset)


def test_judge_cases_mirror_real_overtime_evidence_keys() -> None:
    for case in load_judge_cases(full=True):
        assert set(case.evidence) == OVERTIME_EVIDENCE_KEYS
        assert case.rule_id == "overtime_breach"
        assert case.evidence["source_table"] == "attendance_events"


@pytest.mark.parametrize(
    ("faithful", "invented_facts", "expected"),
    [(True, False, 1.0), (True, True, 0.5), (False, True, 0.0)],
    ids=["faithful_no_invented", "faithful_invented", "unfaithful_invented"],
)
def test_case_score(faithful: bool, invented_facts: bool, expected: float) -> None:
    verdict = Verdict(faithful=faithful, invented_facts=invented_facts, reason="r")

    assert case_score(verdict) == expected


async def test_judge_case_valid_verdict_scores_one() -> None:
    fake = FakeLLM(EXPLANATION, FAITHFUL)

    result = await judge_case(fake, CASE)

    assert result == CaseResult(
        case_id="daily-13h",
        explanation=FAKE_EXPLANATION,
        source="llm",
        score=1.0,
        reason="Matches.",
    )
    assert len(fake.calls) == 2


async def test_judge_case_constrains_verdict_to_verdict_schema() -> None:
    fake = FakeLLM(EXPLANATION, FAITHFUL)

    await judge_case(fake, CASE)

    verdict_schema = fake.schemas[1].schema
    assert verdict_schema["required"] == ["faithful", "invented_facts", "reason"]
    assert verdict_schema["additionalProperties"] is False


async def test_judge_case_verdict_wire_schema_carries_no_length_keyword() -> None:
    fake = FakeLLM(EXPLANATION, FAITHFUL)

    await judge_case(fake, CASE)

    assert fake.schemas[1].schema == {
        "additionalProperties": False,
        "properties": {
            "faithful": {"title": "Faithful", "type": "boolean"},
            "invented_facts": {"title": "Invented Facts", "type": "boolean"},
            "reason": {"title": "Reason", "type": "string"},
        },
        "required": ["faithful", "invented_facts", "reason"],
        "title": "Verdict",
        "type": "object",
    }


def test_verdict_reason_is_capped() -> None:
    Verdict(faithful=True, invented_facts=False, reason="r" * 600)

    with pytest.raises(ValidationError):
        Verdict(faithful=True, invented_facts=False, reason="r" * 601)


async def test_judge_case_overlong_verdict_reason_is_invalid_output() -> None:
    long_verdict = json.dumps({"faithful": True, "invented_facts": False, "reason": "r" * 601})
    fake = FakeLLM(EXPLANATION, long_verdict, long_verdict)

    result = await judge_case(fake, CASE)

    assert (result.score, result.reason) == (0.0, "judge call failed (invalid_output)")


async def test_judge_case_fallback_scores_zero() -> None:
    fake = FakeLLM("not json", "not json")

    result = await judge_case(fake, CASE)

    assert (result.source, result.score) == ("fallback", 0.0)
    assert len(fake.calls) == 2


async def test_judge_case_invalid_verdict_scores_zero() -> None:
    fake = FakeLLM(EXPLANATION, '{"faithful": "maybe"}', "still not a verdict")

    result = await judge_case(fake, CASE)

    assert (result.source, result.score) == ("llm", 0.0)
    assert result.reason == "judge call failed (invalid_output)"
    assert len(fake.calls) == 3


async def test_judge_case_judge_outage_reports_reason() -> None:
    down = LLMUnavailableError("down")
    fake = FakeLLM(EXPLANATION, down, down, down)

    result = await judge_case(fake, CASE)

    assert (result.source, result.score) == ("llm", 0.0)
    assert result.reason == "judge call failed (llm_unavailable)"


async def test_judge_message_uses_explainer_payload() -> None:
    fake = FakeLLM(EXPLANATION, FAITHFUL)

    await judge_case(fake, CASE)

    judge_data = _data_json(fake.calls[1][1])
    assert judge_data == {"finding": finding_payload(CASE.facts()), "explanation": FAKE_EXPLANATION}
    assert judge_data["finding"] == _data_json(fake.calls[0][1])
    assert isinstance(finding_payload(CASE.facts())["occurred_on"], str)
    contents = [message.content for call in fake.calls for message in call]
    assert all(SOURCE_ID not in content for content in contents)


def test_judge_score_is_mean_of_case_scores() -> None:
    results = [_result("a", 1.0), _result("b", 0.5), _result("c", 0.0)]

    assert judge_score(results) == 0.5


def test_judge_score_of_no_cases_is_zero() -> None:
    assert judge_score([]) == 0.0


def test_judge_shortfall_below_baseline() -> None:
    assert judge_shortfalls(0.65, 0.7) == ["judge score 0.650 is below baseline 0.700"]


def test_judge_shortfall_none_at_baseline() -> None:
    assert judge_shortfalls(0.7, 0.7) == []


def test_render_judge_report_lists_cases_and_shortfall() -> None:
    results = [_result("daily-13h", 1.0), _result("weekly-61h", 0.0)]

    report = render_judge_report(results, 0.5, ["judge score 0.500 is below baseline 0.700"])

    assert report.startswith("# Judge eval report\n")
    assert "| daily-13h | llm | 1.00 | ok |" in report
    assert "| weekly-61h | llm | 0.00 | ok |" in report
    assert "Score: 0.500" in report
    assert "- judge score 0.500 is below baseline 0.700" in report


def test_render_judge_report_keeps_table_rows_on_one_line() -> None:
    result = CaseResult(case_id="x", explanation="e", source="llm", score=1.0, reason="a | b\nc")

    report = render_judge_report([result], 1.0, [])

    assert "| x | llm | 1.00 | a \\| b c |" in report
    assert "Judge score meets its baseline." in report


def test_load_judge_baseline_reads_min_score() -> None:
    assert load_judge_baseline(BASELINES_PATH) == 0.7


@pytest.mark.parametrize(
    "judge_section",
    [
        '{"min_score": NaN}',
        '{"min_score": 0}',
        '{"min_score": false}',
        '{"min_score": true}',
        '{"min_score": -0.5}',
        '{"min_score": 1.5}',
        '{"min_score": "0.7"}',
        "{}",
    ],
    ids=["nan", "zero", "false", "true", "negative", "above_one", "string", "missing"],
)
def test_load_judge_baseline_rejects_bad_min_score(tmp_path: Path, judge_section: str) -> None:
    baselines = tmp_path / "baselines.json"
    baselines.write_text(f'{{"rules": {{}}, "judge": {judge_section}}}', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_judge_baseline(baselines)


type OpenProvider = Callable[[LlmSettings], AbstractAsyncContextManager[LLMProvider]]


def _fake_ollama(provider: LLMProvider, opened: list[LlmSettings]) -> OpenProvider:
    @asynccontextmanager
    async def open_fake(settings: LlmSettings) -> AsyncIterator[LLMProvider]:
        opened.append(settings)
        yield provider

    return open_fake


def _isolate_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: LLMProvider
) -> list[LlmSettings]:
    opened: list[LlmSettings] = []
    monkeypatch.setattr(judge, "open_ollama", _fake_ollama(provider, opened))
    monkeypatch.setattr(judge, "JUDGE_REPORT_PATH", tmp_path / "judge_report.md")
    return opened


def test_main_bad_baseline_fails_before_any_llm_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeLLM()
    opened = _isolate_main(monkeypatch, tmp_path, fake)
    baselines = tmp_path / "baselines.json"
    baselines.write_text('{"judge": {"min_score": 0}}', encoding="utf-8")
    monkeypatch.setattr(judge, "BASELINES_PATH", baselines)

    with pytest.raises(ValidationError):
        main([])

    assert (opened, fake.calls) == ([], [])


def test_main_with_no_cases_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate_main(monkeypatch, tmp_path, FakeLLM())
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text("", encoding="utf-8")
    monkeypatch.setattr(judge, "DATASET_PATH", dataset)

    assert main(["--full"]) == 1
    assert "- no judge cases ran" in (tmp_path / "judge_report.md").read_text(encoding="utf-8")


def test_main_passing_sample_returns_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeLLM(*[reply for _ in range(CI_SAMPLE_SIZE) for reply in (EXPLANATION, FAITHFUL)])
    _isolate_main(monkeypatch, tmp_path, fake)

    assert main([]) == 0
    assert len(fake.calls) == 2 * CI_SAMPLE_SIZE


async def test_judge_all_cases_fallback_fails_gate() -> None:
    down = DownLLM()

    results = [await judge_case(down, case) for case in load_judge_cases(full=False)]

    assert [result.score for result in results] == [0.0, 0.0, 0.0]
    assert judge_score(results) == 0.0
    assert judge_shortfalls(judge_score(results), load_judge_baseline(BASELINES_PATH)) != []
