import json
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from app.config import LlmSettings
from app.llm.proposal_judge import PROPOSAL_JUDGE_PROMPT, FixUnderReview
from app.llm.propose import PROPOSER_COLUMNS, CandidateRow, proposal_payload
from app.llm.provider import ChatMessage, LLMProvider, LLMUnavailableError
from app.llm.structured import FallbackReason
from app.services.corrections import validate_patch
from evals import proposal_judge, run_rules
from evals.judge import BASELINES_PATH, JUDGE_REPORT_PATH
from evals.proposal_judge import (
    CI_SAMPLE_SIZE,
    CaseRow,
    ProposalCaseResult,
    ProposalJudgeCase,
    balanced_accuracy,
    judge_proposal_case,
    load_proposal_baseline,
    load_proposal_cases,
    main,
    proposal_shortfalls,
    render_proposal_report,
)
from tests.fake_llm import FakeLLM

type Label = Literal["supported", "unsupported"]

SOURCE_ID = "0b6f3c1a-7d2e-4f5a-9c8b-1e2d3f4a5b6c"
CASE = ProposalJudgeCase(
    id="clock-out-typo",
    rule_id="overtime_breach",
    severity="high",
    occurred_on=date(2026, 3, 2),
    summary="Worked 17.00 h, daily limit 12 h.",
    evidence={
        "limit": "daily",
        "hours": "17.00",
        "max_hours": "12",
        "source_table": "attendance_events",
        "source_id": SOURCE_ID,
    },
    rows=[
        CaseRow(
            alias="row_1",
            table="shifts",
            fields={
                "starts_at": "2026-03-02T08:00:00+00:00",
                "ends_at": "2026-03-02T17:00:00+00:00",
            },
        ),
        CaseRow(
            alias="row_2",
            table="attendance_events",
            fields={
                "event_type": "clock_out",
                "occurred_at": "2026-03-03T01:00:00+00:00",
                "source": "terminal",
            },
        ),
    ],
    fix=FixUnderReview(
        target="row_2",
        column="occurred_at",
        value="2026-03-02T17:00:00+00:00",
        reason="The clock-out is 8 hours after the shift end.",
    ),
    label="supported",
)
SUPPORTED = json.dumps({"supported": True, "reason": "Value is the shift end."})


def _result(label: Label, supported: bool | None, case_id: str = "c") -> ProposalCaseResult:
    return ProposalCaseResult(case_id=case_id, label=label, supported=supported, reason="r")


def _data_json(message: ChatMessage) -> dict[str, object]:
    body = message.content.removeprefix("<data>\n").removesuffix("\n</data>")
    parsed: dict[str, object] = json.loads(body)
    return parsed


def test_load_proposal_cases_full_set_has_both_labels() -> None:
    cases = load_proposal_cases(full=True)

    labels = [case.label for case in cases]
    assert (labels.count("supported"), labels.count("unsupported")) == (7, 7)
    assert len({case.id for case in cases}) == 14
    assert labels == ["supported", "unsupported"] * 7


def test_load_proposal_cases_sample_is_first_four_with_both_labels() -> None:
    sample = load_proposal_cases(full=False)

    assert [case.id for case in sample] == [case.id for case in load_proposal_cases(full=True)][:4]
    assert [case.label for case in sample] == [
        "supported",
        "unsupported",
        "supported",
        "unsupported",
    ]
    assert CI_SAMPLE_SIZE == 4


def test_load_proposal_cases_rejects_malformed_line(tmp_path: Path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text('{"id": "broken", "label": "supported"}\n', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_proposal_cases(full=True, dataset=dataset)


def test_every_case_target_is_a_row_alias() -> None:
    for case in load_proposal_cases(full=True):
        aliases = [row.alias for row in case.rows]
        assert aliases == [f"row_{index}" for index in range(1, len(case.rows) + 1)]
        assert case.fix.target in aliases


def test_every_case_patch_passes_validate_patch() -> None:
    for case in load_proposal_cases(full=True):
        row = next(row for row in case.rows if row.alias == case.fix.target)
        validate_patch(row.table, {case.fix.column: case.fix.value})


def test_every_case_column_is_proposer_editable() -> None:
    for case in load_proposal_cases(full=True):
        row = next(row for row in case.rows if row.alias == case.fix.target)
        assert case.fix.column in PROPOSER_COLUMNS[row.table]


def _confident_fix_reason(case: ProposalJudgeCase) -> bool:
    return "set it to the shift" in case.fix.reason


def test_unsupported_cases_carry_confident_fix_reasons() -> None:
    unsupported = [case for case in load_proposal_cases(full=True) if case.label == "unsupported"]
    sample = [case for case in load_proposal_cases(full=False) if case.label == "unsupported"]

    assert sum(map(_confident_fix_reason, unsupported)) * 2 >= len(unsupported)
    assert any(map(_confident_fix_reason, sample))


def test_some_supported_cases_have_terse_reasons() -> None:
    supported = [case for case in load_proposal_cases(full=True) if case.label == "supported"]

    assert sum(not _confident_fix_reason(case) for case in supported) >= 3


def test_eval_reports_are_git_ignored() -> None:
    reports = [run_rules.REPORT_PATH, JUDGE_REPORT_PATH, proposal_judge.REPORT_PATH]

    checked = subprocess.run(
        ["git", "check-ignore", "--no-index", *map(str, reports)],
        cwd=JUDGE_REPORT_PATH.parent,
        capture_output=True,
        text=True,
        check=False,
    )

    assert len(checked.stdout.splitlines()) == len(reports)


def test_balanced_accuracy_all_correct_is_one() -> None:
    results = [_result("supported", True), _result("unsupported", False)]

    assert balanced_accuracy(results) == 1.0


def test_balanced_accuracy_always_supported_is_half() -> None:
    results = [
        _result("supported", True),
        _result("supported", True),
        _result("supported", True),
        _result("unsupported", True),
    ]

    assert balanced_accuracy(results) == 0.5


def test_balanced_accuracy_counts_failed_judge_as_miss() -> None:
    results = [
        _result("supported", True),
        _result("supported", None),
        _result("unsupported", False),
        _result("unsupported", None),
    ]

    assert balanced_accuracy(results) == 0.5


def test_balanced_accuracy_of_no_cases_is_zero() -> None:
    assert balanced_accuracy([]) == 0.0


def test_proposal_shortfalls_below_baseline_reports_problem() -> None:
    results = [_result("supported", True), _result("unsupported", True)]

    assert proposal_shortfalls(results, 0.5, 0.75) == [
        "proposal judge score 0.500 is below baseline 0.750"
    ]


def test_proposal_shortfalls_none_at_baseline() -> None:
    results = [_result("supported", True), _result("unsupported", False)]

    assert proposal_shortfalls(results, 0.75, 0.75) == []


def _failed(label: Label, failure: FallbackReason) -> ProposalCaseResult:
    return ProposalCaseResult("f", label, None, "x", failure=failure)


def test_proposal_shortfalls_below_baseline_names_judge_failures() -> None:
    results = [
        _failed("supported", "llm_unavailable"),
        _failed("unsupported", "invalid_output"),
        _failed("unsupported", "llm_unavailable"),
        _result("supported", True),
    ]

    assert proposal_shortfalls(results, 0.25, 0.75) == [
        "proposal judge score 0.250 is below baseline 0.750; "
        "3 judge calls failed (reasons: invalid_output, llm_unavailable)"
    ]


def test_proposal_shortfalls_below_baseline_names_single_failure() -> None:
    results = [_failed("supported", "llm_unavailable"), _result("unsupported", False)]

    assert proposal_shortfalls(results, 0.5, 0.75) == [
        "proposal judge score 0.500 is below baseline 0.750; "
        "1 judge call failed (reasons: llm_unavailable)"
    ]


def test_proposal_shortfalls_ignores_failure_when_score_meets_baseline() -> None:
    results = [
        _failed("supported", "llm_unavailable"),
        _result("supported", True),
        _result("unsupported", False),
    ]

    assert proposal_shortfalls(results, 0.75, 0.75) == []


def test_proposal_shortfalls_no_cases_fails() -> None:
    assert proposal_shortfalls([], 0.0, 0.75) == ["no proposal judge cases ran"]


def test_proposal_shortfalls_sample_missing_label_fails() -> None:
    results = [_result("supported", True), _result("supported", True)]

    assert proposal_shortfalls(results, 1.0, 0.75) == ["no 'unsupported' cases ran"]


def test_load_proposal_baseline_reads_min_score() -> None:
    assert load_proposal_baseline(BASELINES_PATH) == 0.75


@pytest.mark.parametrize(
    "section",
    ['{"min_score": NaN}', '{"min_score": 0}', '{"min_score": 1.5}', '{"min_score": "0.75"}'],
    ids=["nan", "zero", "above_one", "string"],
)
def test_load_proposal_baseline_rejects_nan_zero_and_above_one(
    tmp_path: Path, section: str
) -> None:
    baselines = tmp_path / "baselines.json"
    baselines.write_text(f'{{"judge": {{"min_score": 0.7}}, "proposal_judge": {section}}}')

    with pytest.raises(ValidationError):
        load_proposal_baseline(baselines)


def test_load_proposal_baseline_missing_key_fails(tmp_path: Path) -> None:
    baselines = tmp_path / "baselines.json"
    baselines.write_text('{"judge": {"min_score": 0.7}}', encoding="utf-8")

    with pytest.raises(ValidationError):
        load_proposal_baseline(baselines)


async def test_judge_proposal_case_uses_proposal_payload_in_data_block() -> None:
    fake = FakeLLM(SUPPORTED)

    result = await judge_proposal_case(fake, CASE)

    assert result == ProposalCaseResult(
        case_id="clock-out-typo",
        label="supported",
        supported=True,
        reason="Value is the shift end.",
    )
    system, user = fake.calls[0]
    assert system.content == PROPOSAL_JUDGE_PROMPT.text
    rows = [
        CandidateRow(alias=row.alias, target_table=row.table, fields=row.fields)
        for row in CASE.rows
    ]
    assert _data_json(user) == {
        **proposal_payload(CASE.facts(), rows),
        "fix": {
            "target": "row_2",
            "column": "occurred_at",
            "value": "2026-03-02T17:00:00+00:00",
            "reason": "The clock-out is 8 hours after the shift end.",
        },
    }
    assert SOURCE_ID not in user.content
    assert fake.schemas[0].schema["required"] == ["reason", "supported"]


async def test_judge_proposal_case_invalid_verdict_counts_as_miss() -> None:
    fake = FakeLLM('{"supported": "maybe"}', "not json")

    result = await judge_proposal_case(fake, CASE)

    assert (result.supported, result.reason) == (None, "judge call failed (invalid_output)")
    assert result.failure == "invalid_output"
    assert balanced_accuracy([result]) == 0.0


async def test_judge_proposal_case_outage_reports_reason() -> None:
    down = LLMUnavailableError("down")
    fake = FakeLLM(down, down, down)

    result = await judge_proposal_case(fake, CASE)

    assert (result.supported, result.reason) == (None, "judge call failed (llm_unavailable)")


def test_render_proposal_report_lists_label_and_verdict() -> None:
    results = [
        _result("supported", True, "a"),
        _result("unsupported", True, "b"),
        ProposalCaseResult(case_id="c", label="unsupported", supported=None, reason="x | y\nz"),
    ]

    report = render_proposal_report(results, 0.5, ["proposal judge score 0.500 is below"])

    assert report.startswith("# Proposal judge eval report\n")
    assert "Score: 0.500" in report
    assert "| a | supported | supported | yes | r |" in report
    assert "| b | unsupported | supported | no | r |" in report
    assert "| c | unsupported | failed | no | x \\| y z |" in report
    assert "- proposal judge score 0.500 is below" in report


def test_render_proposal_report_meets_baseline_line() -> None:
    report = render_proposal_report([_result("supported", True)], 1.0, [])

    assert "Proposal judge score meets its baseline." in report
    assert "judge call" not in report


def test_render_proposal_report_notes_failures_when_meeting_baseline() -> None:
    results = [_failed("supported", "llm_unavailable"), _result("unsupported", False)]

    report = render_proposal_report(results, 0.75, [])

    assert "Proposal judge score meets its baseline." in report
    assert "Note: 1 judge call failed (reasons: llm_unavailable)." in report
    assert "## Below baseline" not in report


def _isolate_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, provider: LLMProvider
) -> list[LlmSettings]:
    opened: list[LlmSettings] = []

    @asynccontextmanager
    async def open_fake(settings: LlmSettings) -> AsyncIterator[LLMProvider]:
        opened.append(settings)
        yield provider

    monkeypatch.setattr(proposal_judge, "open_ollama", open_fake)
    monkeypatch.setattr(proposal_judge, "REPORT_PATH", tmp_path / "report.md")
    return opened


def test_main_passing_sample_returns_zero(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    verdicts = [
        json.dumps({"supported": case.label == "supported", "reason": "ok"})
        for case in load_proposal_cases(full=False)
    ]
    fake = FakeLLM(*verdicts)
    _isolate_main(monkeypatch, tmp_path, fake)

    assert main([]) == 0
    assert len(fake.calls) == CI_SAMPLE_SIZE
    assert "Score: 1.000" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_main_always_supported_sample_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_main(monkeypatch, tmp_path, FakeLLM(*[SUPPORTED] * CI_SAMPLE_SIZE))

    assert main([]) == 1
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "- proposal judge score 0.500 is below baseline 0.750" in report


def test_main_with_no_cases_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate_main(monkeypatch, tmp_path, FakeLLM())
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text("", encoding="utf-8")
    monkeypatch.setattr(proposal_judge, "DATASET_PATH", dataset)

    assert main(["--full"]) == 1
    assert "- no proposal judge cases ran" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_main_bad_baseline_fails_before_any_llm_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeLLM()
    opened = _isolate_main(monkeypatch, tmp_path, fake)
    baselines = tmp_path / "baselines.json"
    baselines.write_text('{"proposal_judge": {"min_score": 0}}', encoding="utf-8")
    monkeypatch.setattr(proposal_judge, "BASELINES_PATH", baselines)

    with pytest.raises(ValidationError):
        main([])

    assert (opened, fake.calls) == ([], [])
