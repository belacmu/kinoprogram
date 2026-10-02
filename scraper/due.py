#!/usr/bin/env python3
"""Print the regions that should be refreshed in this run (space separated).

Oslo is refreshed on every run. Westman and Costa del Sol change slowly, so each is refreshed twice a day by
its own local clock: the first run after 09:00 local (this is also the run that sends its morning email) and
the first run after its evening hour. Going by the last refresh instead of the clock hour keeps this right when
GitHub starts a scheduled run late, and when daylight saving shifts the UTC times.
Usage: python3 scraper/due.py [--all]
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from build import REGIONS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FMT = "%Y-%m-%dT%H:%M"
MORNING = 9
EVENING = {"westman": 21, "costadelsol": 20}   # local hour of the second daily refresh


def due(rkey, cfg, now_utc):
    now = now_utc.astimezone(ZoneInfo(cfg["tz"])).replace(tzinfo=None)
    data, state = ROOT / "site" / "data" / cfg["data"], ROOT / "state" / cfg["state"]
    if not (data.exists() and state.exists()):
        return True
    last = json.loads(state.read_text()).get("lastRun") or ""
    today = now.strftime("%Y-%m-%d")
    for hour in (MORNING, EVENING[rkey]):
        mark = f"{today}T{hour:02d}:00"
        if now.strftime(FMT) >= mark and last < mark:
            return True
    return False


def main():
    now_utc = datetime.now(ZoneInfo("UTC"))
    out = ["oslo"] + [r for r, cfg in REGIONS.items() if r != "oslo" and ("--all" in sys.argv or due(r, cfg, now_utc))]
    print(" ".join(out))


if __name__ == "__main__":
    main()
