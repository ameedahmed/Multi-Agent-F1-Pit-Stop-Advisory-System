"""score_vs_pits.py: compares the system's PIT calls with the real pit stops. No API calls."""
import os
from collections import defaultdict

import fastf1

from blackboard import RaceBlackboard
from config import TRIGGER_EVENT

WINDOW = 4                     # a call is "in time" if the real in-lap comes 1..WINDOW laps later
PIT = {"PIT_NOW", "PIT_SOON"}

fastf1.Cache.enable_cache(os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_cache"))
session = fastf1.get_session(2023, 1, "R")
session.load(telemetry=False, weather=False, messages=False)

stops = defaultdict(list)                     # driver -> in-laps of real stops
for _, row in session.laps[session.laps["PitInTime"].notna()].iterrows():
    stops[str(row["DriverNumber"])].append(int(row["LapNumber"]))

events = RaceBlackboard().read_latest_events(count=5000)
alert_laps, decided = defaultdict(set), defaultdict(dict)
for e in events:
    p = e["payload"]
    if e["type"] == TRIGGER_EVENT:
        alert_laps[p["driver"]].add(p["lap"])
    elif e["type"] == "SCORED_DECISION":
        decided[p["driver"]][p["lap"]] = p["action"]

print("--- each PIT call: how far ahead of the next real stop? ---")
good = total = 0
for drv in sorted(decided, key=int):
    for lap, action in sorted(decided[drv].items()):
        if action not in PIT:
            continue
        total += 1
        nxt = [s for s in stops[drv] if s >= lap]
        gap = (nxt[0] - lap) if nxt else None
        verdict = ("no stop followed" if gap is None else "too late (same lap)" if gap == 0
                   else "in time" if gap <= WINDOW else f"early by {gap} laps")
        good += verdict == "in time"
        print(f"  #{drv} lap {lap}: {action:9s} next stop in {gap} laps -> {verdict}")
print(f"  {good}/{total} PIT calls were followed by a stop within {WINDOW} laps\n")

print("--- each real stop: did we call it? ---")
called = seen = 0
for drv in sorted(decided, key=int):
    for stop in stops[drv]:
        window = range(stop - WINDOW, stop)
        acted = [l for l in window if decided[drv].get(l) in PIT]
        if acted:
            what, called = f"CALLED at lap {acted[0]}", called + 1
        elif any(l in decided[drv] for l in window):
            what = "analysed, but the agents said " + str({decided[drv][l] for l in window if l in decided[drv]})
        elif any(l in alert_laps[drv] for l in window):
            what = "alert fired but skipped by the cooldown"
        else:
            what = "no alert fired (the preprocessor was silent)"
        seen += 1
        print(f"  #{drv} stop at lap {stop}: {what}")
print(f"  {called}/{seen} real stops were called in time")