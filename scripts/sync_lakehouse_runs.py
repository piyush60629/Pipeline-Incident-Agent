"""Copy the run logs of the Retail Analytics Lakehouse into lakehouse/monitoring/.

    python scripts/sync_lakehouse_runs.py ../Retail-Analytics-Lakehouse

Copies monitoring/index.json and monitoring/runs/ only. The lakehouse's scenarios/results.json
(which failure was injected into which run) is deliberately NOT copied: the agent must find the
cause from the evidence, and the evaluation keeps its expected answers in eval/cases.json.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parent.parent / "lakehouse" / "monitoring"


def sync(lakehouse_repo: Path, target: Path = TARGET) -> int:
    source = lakehouse_repo / "monitoring"
    if not (source / "index.json").exists():
        raise SystemExit(f"No monitoring/index.json in {lakehouse_repo}. Run the lakehouse pipeline first.")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    shutil.copy2(source / "index.json", target / "index.json")
    shutil.copytree(source / "runs", target / "runs", ignore=shutil.ignore_patterns("*.tmp"))
    return sum(1 for _ in (target / "runs").glob("*/run.json"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("lakehouse_repo", type=Path, help="Path to a clone of Retail-Analytics-Lakehouse")
    args = parser.parse_args(argv)
    count = sync(args.lakehouse_repo.resolve())
    print(f"Copied {count} runs to {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
