import argparse
import asyncio

from app.config import Settings
from app.seed.config import SeedConfig
from app.seed.generator import generate_dataset
from app.seed.loader import seed_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the synthetic demo companies.")
    parser.add_argument("--seed", type=int, default=SeedConfig.seed)
    args = parser.parse_args()
    dataset = generate_dataset(SeedConfig(seed=args.seed))
    asyncio.run(seed_database(Settings().database_url, dataset))
    for table, rows in dataset.tables():
        print(f"{table.name}: {len(rows)}")


if __name__ == "__main__":
    main()
