#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import validate_people


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a private people-watch JSON file.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    with args.path.open("r", encoding="utf-8") as handle:
        registry = json.load(handle)
    errors = validate_people(registry)
    if errors:
        raise SystemExit("\n".join(f"- {error}" for error in errors))
    print(f"Valid watchlist: {len(registry['people'])} entries.")


if __name__ == "__main__":
    main()

