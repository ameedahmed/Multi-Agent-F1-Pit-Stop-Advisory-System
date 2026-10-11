"""
thermal_feed.py: modelled engine and brake temperatures, indexed by track temperature.

WHAT: Produces plausible temperatures for a driver on a given lap.
WHY:  The public F1 feed has no engine/brake temperatures. We model them from the one real
      input: track temperature, so readings respond to actual race conditions.
HOW:  baseline + a signed heat effect + noise + an optional stress dial for testing.
      These are modelled values.
"""
import os
import random

# Export F1_THERMAL_STRESS=0.8 in your terminal to force a "hot car" without editing code.
DEFAULT_STRESS = float(os.getenv("F1_THERMAL_STRESS", "0"))
REFERENCE_TRACK_C = 30.0    # the track temperature at which our baselines apply
FALLBACK_TRACK_C = 35.0     # used only when the real value is missing


def simulated_thermal_reading(driver: str, lap: int, track_temp_c: float | None = None,
                              stress: float | None = None) -> dict:
    stress = DEFAULT_STRESS if stress is None else stress
    stress = max(0.0, min(1.0, stress))                 # keep the dial inside 0..1

    # Seeded by (driver, lap): replaying a lap gives identical noise, so tests are reproducible.
    # (A string seed is deterministic in Python; the built-in hash() is not.)
    rng = random.Random(f"{driver}-{lap}")

    # Record whether the weather input was real or a fallback. The label travels into the
    # prompt and the logs so an estimate is never mistaken for a measurement.
    if track_temp_c is None:
        used_temp, source = FALLBACK_TRACK_C, "SIMULATED_DEFAULT_WEATHER"
    else:
        used_temp, source = float(track_temp_c), "SIMULATED_WEATHER_DRIVEN"

    # Signed on purpose: clamping at zero would erase the effect of any track cooler than 30 C.
    heat = used_temp - REFERENCE_TRACK_C

    engine = 94 + 0.3 * heat + 20 * stress + rng.uniform(-4, 4)       # degrees C
    brake = 650 + 4 * heat + 300 * stress + rng.uniform(-120, 120)    # degrees C

    return {
        "sim_engine_temp_c": round(engine, 1),
        "sim_brake_temp_c": round(brake, 1),
        "track_temp_c": round(used_temp, 1),            # included so the agent can cite it
        "thermal_source": source,
    }