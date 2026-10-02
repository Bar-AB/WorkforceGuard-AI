"""Scores every rule against the seeded anomaly labels and gates on evals/baselines.json.

Run after `make seed`: `make eval`. Exits 1 when a rule scores below its baseline.
"""

import asyncio
import json
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.db.models import AnomalyLabel, Company
from app.services.scans import RULES

EVALS_DIR = Path(__file__).resolve().parent
BASELINES_PATH = EVALS_DIR / "baselines.json"
REPORT_PATH = EVALS_DIR / "report.md"

# (company, employee, day): a rule hit counts when it names the labelled employee and day.
Key = tuple[uuid.UUID, uuid.UUID, date]
# rule id -> metric name -> minimum allowed score
Baselines = dict[str, dict[str, float]]


@dataclass(frozen=True)
class Score:
    true_positives: int
    false_positives: int
    false_negatives: int

    # An empty denominator means nothing could go wrong on that side, so it scores 1.0.
    @property
    def precision(self) -> float:
        found = self.true_positives + self.false_positives
        return self.true_positives / found if found else 1.0

    @property
    def recall(self) -> float:
        labelled = self.true_positives + self.false_negatives
        return self.true_positives / labelled if labelled else 1.0

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0

    def metric(self, name: str) -> float:
        metrics = {"precision": self.precision, "recall": self.recall, "f1": self.f1}
        return metrics[name]


def score(predicted: set[Key], expected: set[Key]) -> Score:
    return Score(
        true_positives=len(predicted & expected),
        false_positives=len(predicted - expected),
        false_negatives=len(expected - predicted),
    )


async def _labels(conn: AsyncConnection, company_id: uuid.UUID, rule_id: str) -> set[Key]:
    result = await conn.execute(
        select(AnomalyLabel.employee_id, AnomalyLabel.occurred_on).where(
            AnomalyLabel.company_id == company_id, AnomalyLabel.anomaly_type == rule_id
        )
    )
    return {(company_id, employee_id, day) for employee_id, day in result}


async def evaluate(conn: AsyncConnection, company_ids: Sequence[uuid.UUID]) -> dict[str, Score]:
    scores: dict[str, Score] = {}
    for rule_id, rule in RULES.items():
        predicted: set[Key] = set()
        expected: set[Key] = set()
        for company_id in company_ids:
            findings = await rule.run(conn, company_id)
            predicted |= {(company_id, f.employee_id, f.occurred_on) for f in findings}
            expected |= await _labels(conn, company_id, rule_id)
        scores[rule_id] = score(predicted, expected)
    return scores


def shortfalls(scores: dict[str, Score], baselines: Baselines) -> list[str]:
    problems = [
        f"{rule_id}: has a baseline but was not scored"
        for rule_id in baselines
        if rule_id not in scores
    ]
    for rule_id, result in scores.items():
        if rule_id not in baselines:
            problems.append(f"{rule_id}: no baseline in evals/baselines.json")
            continue
        if result.true_positives + result.false_negatives == 0:
            problems.append(f"{rule_id}: no labelled anomalies (run make seed first)")
            continue
        for metric, floor in baselines[rule_id].items():
            value = result.metric(metric)
            if value < floor:
                problems.append(f"{rule_id}: {metric} {value:.3f} is below baseline {floor:.3f}")
    return problems


def render_report(scores: dict[str, Score], problems: list[str]) -> str:
    lines = [
        "# Rule eval report",
        "",
        "| Rule | Precision | Recall | F1 | TP | FP | FN |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {rule_id} | {s.precision:.3f} | {s.recall:.3f} | {s.f1:.3f} "
        f"| {s.true_positives} | {s.false_positives} | {s.false_negatives} |"
        for rule_id, s in scores.items()
    ]
    lines.append("")
    if problems:
        lines += ["## Below baseline", "", *(f"- {problem}" for problem in problems)]
    else:
        lines.append("All rules meet their baselines.")
    return "\n".join(lines) + "\n"


def load_baselines() -> Baselines:
    rules: Baselines = json.loads(BASELINES_PATH.read_text(encoding="utf-8"))["rules"]
    return rules


async def _evaluate_database(database_url: str) -> dict[str, Score]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            company_ids = list((await conn.execute(select(Company.id))).scalars())
            return await evaluate(conn, company_ids)
    finally:
        await engine.dispose()


def main() -> int:
    scores = asyncio.run(_evaluate_database(Settings().database_url))
    problems = shortfalls(scores, load_baselines())
    report = render_report(scores, problems)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(report)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
