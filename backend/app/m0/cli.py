from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import get_settings
from app.m0.online import OnlineContractProbe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run sanitized M0 cloud provider probes")
    parser.add_argument("--output", type=Path, help="Optional sanitized JSON evidence path")
    parser.add_argument(
        "--allow-blocked",
        action="store_true",
        help="Return success when credentials are missing; report remains blocked",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    report = await OnlineContractProbe(get_settings()).run()
    serialized = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    if report.status == "passed" or (report.status == "blocked" and args.allow_blocked):
        return 0
    return 2 if report.status == "blocked" else 1


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
