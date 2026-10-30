"""Entry point for the `painpoint-radar` command: pipeline commands live in pulse.py, single steps in their own modules."""
from __future__ import annotations

import importlib
import sys

STEPS = ("extract", "cluster", "sentiment", "recommend", "overlay", "delta", "digest", "seed", "llm")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in STEPS:
        module = importlib.import_module(f"{__package__}.{argv[0]}")
        return module.main(argv[1:]) or 0
    from . import pulse
    return pulse.main(argv)


if __name__ == "__main__":
    sys.exit(main())
