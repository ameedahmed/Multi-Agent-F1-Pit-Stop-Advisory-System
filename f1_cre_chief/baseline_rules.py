"""baseline_rules.py: do the agents' actions add information beyond tyre age? No API calls."""
import os
from collections import defaultdict

import fastf1

from blackboard import RaceBlackboard
from config import TRIGGER_EVENT

WINDOW = 4            # a stop "follows" a lap if the in-lap is 1..WINDOW laps later
LATE_LAPS_LEFT = 6    # skip the final laps, where nothing can follow

fastf1.Cache.enable_cache(os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_cache"))
session = fastf1.get_session(2023, 1, "R")
session.load(telemetry=False, weather=False, messages=False)
RACE_LAPS = int(session.laps["LapNumber"].max())

stops = defaultdict(list)
for _, row in session.laps[session.laps["PitInTime"].notna()].iterrows():
    stops[str(row["DriverNumber"])].append(int(row["LapNumber"]))


def stop_follows(drv, lap):
    return any(1 <= s - lap <= WINDOW for s in stops[drv])


def age_bucket(age):
    return "1: age<=6" if age <= 6 else "2: age 7-12" if age <= 12 else "3: age 13+"


def early(lap):
    return RACE_LAPS - lap > LATE_LAPS_LEFT


events = RaceBlackboard().read_latest_events(count=5000)
alerts = {(e["payload"]["driver"], e["payload"]["lap"]): e["payload"]
          for e in events if e["type"] == TRIGGER_EVENT}
decisions = [e["payload"] for e in events if e["type"] == "SCORED_DECISION"]

by_age = defaultdict(lambda: [0, 0])
for (drv, lap), a in alerts.items():
    if early(lap):
        b = age_bucket(a["tyre_age"])
        by_age[b][0] += 1
        by_age[b][1] += stop_follows(drv, lap)
print("ALL early alerts by tyre age (no agents involved):")
for b, (n, h) in sorted(by_age.items()):
    print(f"  {b:12s} n={n:>3d}  stop followed {h:>3d} ({h / n:.0%})")

by_both = defaultdict(lambda: [0, 0])
for d in decisions:
    a = alerts.get((d["driver"], d["lap"]))
    if a is None or not early(d["lap"]):
        continue
    key = (age_bucket(a["tyre_age"]), d["action"])
    by_both[key][0] += 1
    by_both[key][1] += stop_follows(d["driver"], d["lap"])
print("\nANALYSED early alerts by tyre age and the system's action:")
for (b, action), (n, h) in sorted(by_both.items()):
    print(f"  {b:12s} {action:10s} n={n:>3d}  stop followed {h:>3d} ({h / n:.0%})")