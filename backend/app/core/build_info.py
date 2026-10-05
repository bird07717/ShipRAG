"""Immutable image metadata, never deployment-time environment overrides."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import TypedDict


class BuildInfo(TypedDict):
    commit: str | None
    built_at: str | None
    dirty: bool | None


def read_build_info(path: Path | None = None) -> BuildInfo:
    unknown: BuildInfo = {"commit": None, "built_at": None, "dirty": None}
    try:
        payload = json.loads((path or Path(__file__).with_name("build-info.json")).read_text())
        if not isinstance(payload, dict):
            return unknown
        commit = payload.get("commit")
        if not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40,64}", commit) is None:
            return unknown
        built_at = payload.get("built_at")
        if not isinstance(built_at, str) or datetime.fromisoformat(built_at).tzinfo is None:
            return unknown
        dirty = payload.get("dirty")
        if not isinstance(dirty, bool):
            return unknown
        return {"commit": commit, "built_at": built_at, "dirty": dirty}
    except (OSError, ValueError, TypeError):
        return unknown


def main() -> None:
    parser = argparse.ArgumentParser(description="Show or stamp image build metadata")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--commit", default="unknown")
    parser.add_argument("--built-at", default="unknown")
    parser.add_argument("--dirty", default="unknown")
    args = parser.parse_args()
    if args.write:
        Path(__file__).with_name("build-info.json").write_text(
            json.dumps(
                {
                    "commit": args.commit,
                    "built_at": args.built_at,
                    "dirty": {"true": True, "false": False}.get(args.dirty),
                }
            )
        )
    print(json.dumps(read_build_info()))


if __name__ == "__main__":
    main()
