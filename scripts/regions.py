#!/usr/bin/env python3
"""The regions, from scraper/build.py's REGIONS (the one list), for the workflows and scripts.

    python3 scripts/regions.py keys    # oslo westman winnipeg costadelsol
    python3 scripts/regions.py files   # films.json westman.json winnipeg.json costadelsol.json
    python3 scripts/regions.py pairs   # one "key file" pair per line
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
from build import REGIONS  # noqa: E402


def main(what):
    if what == "keys":
        print(" ".join(REGIONS))
    elif what == "files":
        print(" ".join(cfg["data"] for cfg in REGIONS.values()))
    elif what == "pairs":
        print("\n".join(f"{k} {cfg['data']}" for k, cfg in REGIONS.items()))
    else:
        sys.exit("usage: regions.py keys|files|pairs")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "keys")
