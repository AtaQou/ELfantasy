#!/usr/bin/env python3
"""Generate the four measured Phase 5B reports."""

from __future__ import annotations

from src.modeling.phase5b_reports import generate_phase5b_reports


def main() -> int:
    for name, path in generate_phase5b_reports().items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
