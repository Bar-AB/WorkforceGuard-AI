import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict

from app.config import LlmSettings
from app.llm.explain import FindingFacts
from app.llm.proposal_judge import (
    PROPOSAL_JUDGE_PROMPT,
    FixUnderReview,
    ProposalVerdict,
    judge_messages,
)
from app.llm.propose import CandidateRow
from app.llm.provider import LLMProvider, open_ollama
from app.llm.structured import FallbackReason, LLMOutputError, complete_structured
from app.services.corrections import CorrectionTarget
from evals.judge import (
    BASELINES_PATH,
    EVALS_DIR,
    JudgeBaseline,
    table_cell,
)

DATASET_PATH: Final = EVALS_DIR / "datasets" / "proposal_judge_cases.jsonl"
REPORT_PATH: Final = EVALS_DIR / "proposal_judge_report.md"
CI_SAMPLE_SIZE: Final = 4

type Label = Literal["supported", "unsupported"]
LABELS: Final[tuple[Label, Label]] = ("supported", "unsupported")


class CaseRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str
    table: CorrectionTarget
    fields: dict[str, str]

    def candidate(self) -> CandidateRow:
        return CandidateRow(alias=self.alias, target_table=self.table, fields=self.fields)


class ProposalJudgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    rule_id: str
    severity: str
    occurred_on: date
    summary: str
    evidence: dict[str, str]
    rows: list[CaseRow]
    fix: FixUnderReview
    label: Label

    def facts(self) -> FindingFacts:
        return FindingFacts(
            rule_id=self.rule_id,
            severity=self.severity,
            occurred_on=self.occurred_on,
            summary=self.summary,
            evidence=self.evidence,
        )


class _ProposalBaselines(BaseModel):
    proposal_judge: JudgeBaseline


@dataclass(frozen=True)
class ProposalCaseResult:
    case_id: str
    label: Label
    supported: bool | None
    reason: str
    failure: FallbackReason | None = None

    @property
    def correct(self) -> bool:
        return self.supported is not None and self.supported == (self.label == "supported")


def load_proposal_cases(full: bool, dataset: Path = DATASET_PATH) -> list[ProposalJudgeCase]:
    lines = dataset.read_text(encoding="utf-8").splitlines()
    cases = [ProposalJudgeCase.model_validate_json(line) for line in lines if line.strip()]
    return cases if full else cases[:CI_SAMPLE_SIZE]


def load_proposal_baseline(baselines: Path) -> float:
    raw = json.loads(baselines.read_text(encoding="utf-8"))
    return _ProposalBaselines.model_validate(raw).proposal_judge.min_score


async def judge_proposal_case(provider: LLMProvider, case: ProposalJudgeCase) -> ProposalCaseResult:
    try:
        messages = judge_messages(case.facts(), [row.candidate() for row in case.rows], case.fix)
        reply = await complete_structured(provider, messages, ProposalVerdict)
    except LLMOutputError as error:
        reason = f"judge call failed ({error.reason})"
        return ProposalCaseResult(case.id, case.label, None, reason, failure=error.reason)
    verdict = reply.value
    return ProposalCaseResult(case.id, case.label, verdict.supported, verdict.reason)


def _recall(results: Sequence[ProposalCaseResult], label: Label) -> float:
    labeled = [result for result in results if result.label == label]
    return sum(result.correct for result in labeled) / len(labeled) if labeled else 0.0


def balanced_accuracy(results: Sequence[ProposalCaseResult]) -> float:
    return sum(_recall(results, label) for label in LABELS) / len(LABELS)


def proposal_shortfalls(
    results: Sequence[ProposalCaseResult], score: float, baseline: float
) -> list[str]:
    if not results:
        return ["no proposal judge cases ran"]
    present = {result.label for result in results}
    missing = [f"no '{label}' cases ran" for label in LABELS if label not in present]
    if missing:
        return missing
    if score >= baseline:
        return []
    below = f"proposal judge score {score:.3f} is below baseline {baseline:.3f}"
    failures = _failure_summary(results)
    return [f"{below}; {failures}" if failures else below]


def _failure_summary(results: Sequence[ProposalCaseResult]) -> str | None:
    failures = [result.failure for result in results if result.failure is not None]
    if not failures:
        return None
    calls = "1 judge call" if len(failures) == 1 else f"{len(failures)} judge calls"
    return f"{calls} failed (reasons: {', '.join(sorted(set(failures)))})"


def _verdict(result: ProposalCaseResult) -> str:
    if result.supported is None:
        return "failed"
    return "supported" if result.supported else "unsupported"


def _report_row(result: ProposalCaseResult) -> str:
    correct = "yes" if result.correct else "no"
    return (
        f"| {table_cell(result.case_id)} | {result.label} | {_verdict(result)} | {correct} "
        f"| {table_cell(result.reason)} |"
    )


def render_proposal_report(
    results: Sequence[ProposalCaseResult], score: float, problems: list[str]
) -> str:
    lines = [
        "# Proposal judge eval report",
        "",
        f"Prompt: {PROPOSAL_JUDGE_PROMPT.label}. Metric: balanced accuracy. Score: {score:.3f}",
        "",
        "| Case | Label | Verdict | Correct | Reason |",
        "| --- | --- | --- | --- | --- |",
        *map(_report_row, results),
        "",
    ]
    if problems:
        lines += ["## Below baseline", "", *(f"- {problem}" for problem in problems)]
    else:
        lines.append("Proposal judge score meets its baseline.")
        failures = _failure_summary(results)
        if failures:
            lines.append(f"Note: {failures}.")
    return "\n".join(lines) + "\n"


async def _judge_all(
    settings: LlmSettings, cases: Sequence[ProposalJudgeCase]
) -> list[ProposalCaseResult]:
    async with open_ollama(settings) as provider:
        return [await judge_proposal_case(provider, case) for case in cases]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM-as-Judge eval for suggested data fixes.")
    parser.add_argument("--full", action="store_true", help="judge every case, not the CI sample")
    args = parser.parse_args(argv)
    baseline = load_proposal_baseline(BASELINES_PATH)
    cases = load_proposal_cases(full=args.full, dataset=DATASET_PATH)
    results = asyncio.run(_judge_all(LlmSettings(), cases))
    score = balanced_accuracy(results)
    problems = proposal_shortfalls(results, score, baseline)
    report = render_proposal_report(results, score, problems)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
