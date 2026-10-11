import os
import fastf1

fastf1.Cache.enable_cache(os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_cache"))
s = fastf1.get_session(2023, 1, "R")
s.load(telemetry=False, weather=False, messages=False)
laps = s.laps.pick_drivers("1")
print(laps[laps["PitInTime"].notna()][["LapNumber", "Stint", "Compound", "TyreLife"]])