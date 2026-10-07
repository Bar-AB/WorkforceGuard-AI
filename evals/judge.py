import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.config import LlmSettings
from app.llm.explain import ExplanationSource, FindingFacts, explain_finding, finding_payload
from app.llm.prompt_loader import Prompt, load_prompt
from app.llm.provider import ChatMessage, LLMProvider, open_ollama
from app.llm.structured import LLMOutputError, complete_structured
from app.security.injection import wrap_untrusted

EVALS_DIR: Final = Path(__file__).resolve().parent
BASELINES_PATH: Final = EVALS_DIR / "baselines.json"
DATASET_PATH: Final = EVALS_DIR / "datasets" / "judge_cases.jsonl"
JUDGE_REPORT_PATH: Final = EVALS_DIR / "judge_report.md"
JUDGE_PROMPT: Final[Prompt] = load_prompt("judge_explanation", 1, EVALS_DIR / "prompts")
CI_SAMPLE_SIZE: Final = 3
MAX_VERDICT_REASON_CHARS: Final = 600


class JudgeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    rule_id: str
    severity: str
    occurred_on: date
    summary: str
    evidence: dict[str, str]

    def facts(self) -> FindingFacts:
        return FindingFacts(
            rule_id=self.rule_id,
            severity=self.severity,
            occurred_on=self.occurred_on,
            summary=self.summary,
            evidence=self.evidence,
        )


class JudgeBaseline(BaseModel):
    min_score: float = Field(strict=True, gt=0, le=1, allow_inf_nan=False)


class Baselines(BaseModel):
    judge: JudgeBaseline


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    faithful: bool
    invented_facts: bool
    reason: str = Field(max_length=MAX_VERDICT_REASON_CHARS)


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    explanation: str
    source: ExplanationSource
    score: float
    reason: str


def case_score(verdict: Verdict) -> float:
    return (int(verdict.faithful) + int(not verdict.invented_facts)) / 2


def load_judge_cases(full: bool, dataset: Path = DATASET_PATH) -> list[JudgeCase]:
    lines = dataset.read_text(encoding="utf-8").splitlines()
    cases = [JudgeCase.model_validate_json(line) for line in lines if line.strip()]
    return cases if full else cases[:CI_SAMPLE_SIZE]


def load_judge_baseline(baselines: Path) -> float:
    raw = json.loads(baselines.read_text(encoding="utf-8"))
    return Baselines.model_validate(raw).judge.min_score


def _judge_messages(case: JudgeCase, explanation: str) -> list[ChatMessage]:
    data = {"finding": finding_payload(case.facts()), "explanation": explanation}
    return [
        ChatMessage(role="system", content=JUDGE_PROMPT.text),
        ChatMessage(role="user", content=wrap_untrusted(json.dumps(data, sort_keys=True))),
    ]


async def judge_case(provider: LLMProvider, case: JudgeCase) -> CaseResult:
    explained = await explain_finding(provider, case.facts(), {"eval": "judge", "case_id": case.id})
    if explained.source == "fallback":
        reason = f"explanation fell back ({explained.fallback_reason})"
        return CaseResult(case.id, explained.text, explained.source, 0.0, reason)
    try:
        reply = await complete_structured(provider, _judge_messages(case, explained.text), Verdict)
    except LLMOutputError as error:
        reason = f"judge call failed ({error.reason})"
        return CaseResult(case.id, explained.text, explained.source, 0.0, reason)
    verdict = reply.value
    return CaseResult(
        case.id, explained.text, explained.source, case_score(verdict), verdict.reason
    )


def judge_score(results: Sequence[CaseResult]) -> float:
    return sum(result.score for result in results) / len(results) if results else 0.0


def judge_shortfalls(score: float, baseline: float) -> list[str]:
    if score < baseline:
        return [f"judge score {score:.3f} is below baseline {baseline:.3f}"]
    return []


def _table_cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_judge_report(results: Sequence[CaseResult], score: float, problems: list[str]) -> str:
    lines = [
        "# Judge eval report",
        "",
        f"Prompt: {JUDGE_PROMPT.label}. Score: {score:.3f}",
        "",
        "| Case | Source | Score | Reason |",
        "| --- | --- | --- | --- |",
    ]
    lines += [
        f"| {_table_cell(r.case_id)} | {r.source} | {r.score:.2f} | {_table_cell(r.reason)} |"
        for r in results
    ]
    lines.append("")
    if problems:
        lines += ["## Below baseline", "", *(f"- {problem}" for problem in problems)]
    else:
        lines.append("Judge score meets its baseline.")
    return "\n".join(lines) + "\n"


async def _judge_all(settings: LlmSettings, cases: Sequence[JudgeCase]) -> list[CaseResult]:
    async with open_ollama(settings) as provider:
        return [await judge_case(provider, case) for case in cases]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LLM-as-Judge eval for finding explanations.")
    parser.add_argument("--full", action="store_true", help="judge every case, not the CI sample")
    args = parser.parse_args(argv)
    baseline = load_judge_baseline(BASELINES_PATH)
    cases = load_judge_cases(full=args.full, dataset=DATASET_PATH)
    results = asyncio.run(_judge_all(LlmSettings(), cases))
    score = judge_score(results)
    problems = judge_shortfalls(score, baseline) if results else ["no judge cases ran"]
    report = render_judge_report(results, score, problems)
    JUDGE_REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
