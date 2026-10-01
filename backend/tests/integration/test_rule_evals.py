from sqlalchemy.ext.asyncio import AsyncConnection

from app.seed.config import SeedConfig
from app.seed.generator import generate_dataset
from app.seed.loader import load_dataset
from evals.run_rules import evaluate

# Enough days for night shifts, near misses and week boundaries; small enough to stay fast.
SEEDED = SeedConfig(
    employees_per_company=40,
    days=21,
    overtime_breaches_per_company=6,
    buddy_pairs_per_company=1,
    buddy_days_per_pair=2,
    off_shift_accesses_per_company=2,
    injected_notes_per_company=2,
)


async def test_evaluate_overtime_rule_on_seed_finds_every_labelled_breach(
    rollback_conn: AsyncConnection,
) -> None:
    dataset = generate_dataset(SEEDED)
    await load_dataset(rollback_conn, dataset)

    scores = await evaluate(rollback_conn, dataset.company_ids())

    overtime = scores["overtime_breach"]
    assert overtime.true_positives == 12
    assert overtime.recall >= 0.95
    assert overtime.f1 == 1.0
