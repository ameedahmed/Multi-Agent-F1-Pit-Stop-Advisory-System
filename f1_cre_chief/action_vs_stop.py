"""action_vs_stop.py: do the agents' actions separate stop laps from other laps? No API calls."""
import os
from collections import defaultdict

import fastf1

from blackboard import RaceBlackboard

WINDOW = 4            # a stop "follows" a lap if the in-lap is 1..WINDOW laps later
LATE_LAPS_LEFT = 6    # "late" means this many laps or fewer remain

fastf1.Cache.enable_cache(os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_cache"))
session = fastf1.get_session(2023, 1, "R")
session.load(telemetry=False, weather=False, messages=False)
RACE_LAPS = int(session.laps["LapNumber"].max())

stops = defaultdict(list)
for _, row in session.laps[session.laps["PitInTime"].notna()].iterrows():
    stops[str(row["DriverNumber"])].append(int(row["LapNumber"]))


def stop_follows(drv, lap):
    return any(1 <= s - lap <= WINDOW for s in stops[drv])


n = hit = 0
for _, row in session.laps.iterrows():
    n += 1
    hit += stop_follows(str(row["DriverNumber"]), int(row["LapNumber"]))
print(f"race length: {RACE_LAPS} laps")
print(f"baseline: a stop follows {hit}/{n} = {hit / n:.0%} of all driver-laps\n")

table = defaultdict(lambda: [0, 0])
for e in RaceBlackboard().read_latest_events(count=5000):
    d = e["payload"]
    if e["type"] != "SCORED_DECISION":
        continue
    phase = "late" if RACE_LAPS - d["lap"] <= LATE_LAPS_LEFT else "early"
    table[(d["action"], phase)][0] += 1
    table[(d["action"], phase)][1] += stop_follows(d["driver"], d["lap"])

print(f"{'action':10s} {'phase':6s} {'n':>4s} {'stop followed':>14s}")
for (action, phase), (cnt, h) in sorted(table.items()):
    print(f"{action:10s} {phase:6s} {cnt:>4d} {h:>6d} ({h / cnt:.0%})")