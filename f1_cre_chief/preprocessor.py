"""
preprocessor.py: Phase 2, the deterministic gateway between raw telemetry and the LLM agents.

WHAT: Replays (or, later, streams) lap data, keeps a short rolling window of lap times per
      driver, fits a trend line, and publishes an alert ONLY when a driver's pace is
      degrading faster than a threshold.
WHY:  LLM calls cost money and time. Plain arithmetic is free and instant, so code does the
      filtering and the agents are woken only for the ambiguous cases worth reasoning about.
HOW:  fastf1 replay -> lap_context() gathers evidence -> process_telemetry_window() does the
      math -> Redis hash (live state) + Redis stream (alert events, with the evidence attached).
"""
import argparse
import os
import time

import fastf1
import numpy as np
import pandas as pd

from blackboard import RaceBlackboard

# Connects to Redis at import time. If Redis isn't running, the error appears here.
board = RaceBlackboard()

# ---------------------------------------------------------------------------
# Module-level state (lives only as long as the process; a scratchpad, not race state)
# ---------------------------------------------------------------------------
LAP_BUFFERS = {}   # driver -> list of (lap_number, lap_time) for the rolling window
LAST_STINT = {}    # driver -> stint number last seen (used to detect a tyre change)
STATS = {"processed": 0, "dirty": 0, "alerts": 0, "no_track_temp": 0}   # lets you SEE what a run did

# ---------------------------------------------------------------------------
# Hyper-parameters. Tune one at a time so you know what moved the results.
# ---------------------------------------------------------------------------
DEGRADATION_THRESHOLD = 0.1   # seconds lost per lap before an alert fires
WINDOW_SIZE = 4               # how many recent clean laps the trend line uses
MIN_LAPS_FOR_FIT = 3          # a line needs at least this many points to mean anything
TRIGGER_EVENT = "THRESHOLD_EXCEEDED_TIRE_DEG"   # must match TRIGGER_EVENT in config.py


def _clean(value, default=None):
    """
    WHAT: Returns `value`, or `default` if it is missing (NaN / NaT / None).
    WHY:  fastf1 marks missing data as NaN, and NaN is "truthy" in Python, so `value or default`
          silently passes it through. An explicit check is safe.
    """
    return value if pd.notna(value) else default


def build_gap_lookup(laps) -> dict:
    """
    WHAT: A quick-reference table: (lap_number, race_position) -> the race-clock time (seconds)
          at which that car crossed the line to finish that lap.
    WHY:  To know how far behind the car in front a driver was, we need that car's crossing
          time on the SAME lap. fastf1 groups laps by driver, so that row is elsewhere in the
          data. One pass up front makes every later lookup instant.
    HOW:  Loop over every lap once and store its finish time under (lap, position).
    """
    lookup = {}
    for _, lap in laps.iterlaps():
        lap_no, position, finish = lap.get("LapNumber"), lap.get("Position"), lap.get("Time")
        if pd.notna(lap_no) and pd.notna(position) and pd.notna(finish):
            lookup[(int(lap_no), int(position))] = finish.total_seconds()
    return lookup


def build_weather_lookup(weather) -> pd.DataFrame | None:
    """
    WHAT: A time-sorted table of (Time, TrackTemp) taken from the session's weather data.
    WHY:  Weather is sampled roughly once a minute on the session clock, not once per lap.
          Sorting by time lets us ask "what was the track temperature when this lap ended?"
    HOW:  Keep only the two columns we need. Returns None if the data is missing, so the rest
          of the pipeline degrades gracefully (the thermal feed then labels itself as using
          the default temperature) instead of crashing.
    """
    if weather is None or weather.empty or "TrackTemp" not in weather.columns:
        return None
    return (weather[["Time", "TrackTemp"]].dropna()
            .sort_values("Time").reset_index(drop=True))


def track_temp_at(table, lap_end_time):
    """
    WHAT: The track temperature (C) from the latest weather sample at or before lap_end_time.
    WHY:  The most recent reading is the best estimate of conditions at that moment.
    HOW:  searchsorted finds where lap_end_time would slot into the sorted sample times; one
          step back is the latest sample that is not in the future. A result of -1 means
          "no sample exists yet", so we return None.
    """
    if table is None or pd.isna(lap_end_time):
        return None
    i = table["Time"].searchsorted(lap_end_time, side="right") - 1
    if i < 0:
        return None
    return round(float(table["TrackTemp"].iloc[i]), 1)


def lap_context(lap, gap_lookup: dict, weather_table) -> dict:
    """
    WHAT: Collects the evidence about ONE lap: tyre compound, tyre age, stint, gap to the car
          ahead, track temperature, and whether the lap was "clean".
    WHY:  A slowdown can mean worn tyres, traffic, a safety car, or a pit stop, and the slope
          alone can't separate them. We attach the evidence the agents need. Pit laps and
          non-green laps are flagged so the math can ignore them.
    HOW:  Reads fastf1's lap row. Missing values become explicit "unknown" markers
          ("UNKNOWN" compound, -1 tyre age, None stint/gap/temperature), never silently dropped.
    """
    # Gap to the car ahead: my finish time minus the finish time of the car one place ahead.
    # A snapshot at the end of the lap, a rough proxy for "stuck in traffic".
    gap = None
    position = _clean(lap.get("Position"))
    finish = _clean(lap.get("Time"))
    if position is not None and finish is not None and int(position) > 1:   # P1 has nobody ahead
        ahead_finish = gap_lookup.get((int(lap["LapNumber"]), int(position) - 1))
        if ahead_finish is not None:
            raw_gap = finish.total_seconds() - ahead_finish
            if raw_gap > 0:                  # zero or negative means the data disagreed; ignore it
                gap = round(raw_gap, 3)

    # Is this lap usable for measuring tyre wear?
    is_pit_lap = pd.notna(lap.get("PitInTime")) or pd.notna(lap.get("PitOutTime"))
    is_green = str(_clean(lap.get("TrackStatus"), "")) == "1"   # "1" = all clear for the whole lap

    tyre_age = _clean(lap.get("TyreLife"))
    stint = _clean(lap.get("Stint"))
    return {
        "compound": str(_clean(lap.get("Compound"), "UNKNOWN")),
        "tyre_age": int(tyre_age) if tyre_age is not None else -1,   # -1 = unknown
        "stint": int(stint) if stint is not None else None,          # None = unknown
        "gap_ahead_s": gap,
        "track_temp_c": track_temp_at(weather_table, lap.get("Time")),   # real weather at lap end
        "clean": (not is_pit_lap) and is_green,
    }


def process_telemetry_window(driver: str, lap_number: int, lap_time: float, ctx: dict | None = None):
    """
    WHAT: Takes one finished lap, updates that driver's rolling window, fits a trend line,
          saves the latest state to Redis, and publishes an alert if the pace is degrading
          faster than DEGRADATION_THRESHOLD.
    WHY:  This is the gate that decides "normal lap, ignore" vs "wake the specialists".
          Everything here is plain math, so it costs nothing and is easy to test.
    HOW:  (1) reset the window if the tyres changed, (2) add the lap only if it is clean,
          (3) fit lap_time = slope * lap_number + c, (4) write state, (5) maybe publish an alert.
    """
    ctx = ctx or {}
    STATS["processed"] += 1

    # RULE 1: a new stint means new tyres. Laps from the old set say nothing about the new set,
    # so throw the window away and start fresh.
    stint = ctx.get("stint")
    previous_stint = LAST_STINT.get(driver)
    if stint is not None and previous_stint is not None and stint != previous_stint:
        LAP_BUFFERS[driver] = []
    if stint is not None:
        LAST_STINT[driver] = stint

    LAP_BUFFERS.setdefault(driver, [])       # create this driver's window the first time we see them

    # RULE 2: only clean laps (no pit in/out, green flag all the way) may enter the window.
    # Defaults to True so the function still works if called without context.
    is_clean = ctx.get("clean", True)
    if is_clean:
        LAP_BUFFERS[driver].append((lap_number, lap_time))
        if len(LAP_BUFFERS[driver]) > WINDOW_SIZE:
            LAP_BUFFERS[driver].pop(0)       # drop the oldest lap (sliding window)
    else:
        STATS["dirty"] += 1

    # Fit a straight line through (lap number, lap time). The slope is seconds lost per lap:
    # positive = getting slower. Needs MIN_LAPS_FOR_FIT points; before that slope stays None.
    slope = None
    window = LAP_BUFFERS[driver]
    if len(window) >= MIN_LAPS_FOR_FIT:
        laps = [lap for lap, _ in window]
        times = [t for _, t in window]
        slope, _intercept = np.polyfit(laps, times, 1)
        slope = round(float(slope), 3)       # plain float: numpy types don't serialize to JSON

    gap = ctx.get("gap_ahead_s")
    track_temp = ctx.get("track_temp_c")
    if track_temp is None:
        STATS["no_track_temp"] += 1          # shows up in the summary so missing weather is visible

    # Save the live snapshot. Redis hashes accept only str/int/float/bytes, so None becomes ""
    # and bool becomes 0/1 (redis-py raises DataError on None and bool).
    board.update_driver_state(driver, {
        "last_lap": lap_number,
        "latest_lap_time": lap_time,
        "dag_slope": slope if slope is not None else "",   # key spelling kept for compatibility
        "compound": ctx.get("compound", "UNKNOWN"),
        "tyre_age": ctx.get("tyre_age", -1),
        "stint": stint if stint is not None else "",
        "gap_ahead_s": gap if gap is not None else "",
        "track_temp_c": track_temp if track_temp is not None else "",
        "lap_clean": int(is_clean),
    })

    # Publish an alert only for a clean lap, so a pit lap can't repeat a stale alert.
    # While the slope stays above the threshold this fires on every clean lap;
    # the runner's cooldown keeps that from becoming an API-call storm.
    if is_clean and slope is not None and slope >= DEGRADATION_THRESHOLD:
        STATS["alerts"] += 1
        board.publish_event(
            event_type=TRIGGER_EVENT,
            payload={
                "driver": driver,
                "lap": lap_number,
                "deg_rate": slope,
                "urgency_score": 1.5,    # not used by the agents or scoring engine; kept for the dashboard
                "reason": f"Pace dropped by {slope} s/lap over the window",
                # The evidence goes INTO the event, not just the Redis hash. The hash keeps changing,
                # but an event is a frozen snapshot of the moment the alert fired, which keeps the
                # append-only log self-contained and replayable. The runner reads track_temp_c from here.
                "compound": ctx.get("compound", "UNKNOWN"),
                "tyre_age": ctx.get("tyre_age", -1),
                "stint": stint,
                "gap_ahead_s": gap,
                "track_temp_c": track_temp,
                "recent_laps": [[lap, round(t, 3)] for lap, t in window],
            },
        )


def start_ingestion(live_mode: bool = False, season: int = 2023, round_num: int = 1,
                    session: str = "R", delay: float = 0.0):
    """
    WHAT: Entry point that feeds laps into process_telemetry_window(), from a historical race
          replay (working) or the live stream (not wired up yet).
    WHY:  Historical replay lets you develop and test without waiting for a real race.
    HOW:  Load the session with fastf1, build the gap and weather tables, then replay every lap
          in order. `delay` sleeps between laps so you can watch events arrive in Redis.
    """
    if live_mode:
        # A hard stop instead of a silent no-op: an earlier version connected and did nothing,
        # which looks like success. Wire up the livef1 callback here later.
        raise SystemExit("Live mode is not implemented yet. Run without --live for historical replay.")

    print(f"Starting historical replay: season {season}, round {round_num}, session {session}")

    # fastf1 caches downloads so reruns are fast. The path is anchored to this file (not your
    # terminal's current folder) and created if missing; enable_cache() requires it to exist.
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_cache")
    os.makedirs(cache_dir, exist_ok=True)
    fastf1.Cache.enable_cache(cache_dir)

    f1_session = fastf1.get_session(season, round_num, session)
    # Lap-level timing plus weather; skips the heavy car telemetry. weather=True is explicit
    # so we never depend on a default.
    f1_session.load(telemetry=False, laps=True, weather=True)

    gap_lookup = build_gap_lookup(f1_session.laps)             # one pass up front, instant lookups later
    weather_table = build_weather_lookup(f1_session.weather_data)
    if weather_table is None:
        print("No weather data found; thermal readings will fall back to the default track temperature")
    else:
        # Tells you at a glance whether the weather input will actually matter for this race.
        print(f"Track temperature range this session: "
              f"{weather_table['TrackTemp'].min()} to {weather_table['TrackTemp'].max()} C")

    for _, lap in f1_session.laps.iterlaps():
        driver = str(lap.get("DriverNumber", ""))
        lap_num = lap.get("LapNumber")
        lap_time = lap.get("LapTime")

        # pd.notna also catches NaT (a missing lap time), which "is not None" would let through.
        if driver and pd.notna(lap_num) and pd.notna(lap_time):
            try:
                ctx = lap_context(lap, gap_lookup, weather_table)
                process_telemetry_window(driver, int(lap_num), float(lap_time.total_seconds()), ctx)
                if delay:
                    time.sleep(delay)
            except (ValueError, TypeError, AttributeError):
                continue   # skip a malformed lap rather than crash the whole replay

    print(f"Replay finished. Laps processed: {STATS['processed']}, excluded as dirty: {STATS['dirty']}, "
          f"alerts published: {STATS['alerts']}, laps without track temp: {STATS['no_track_temp']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="F1 Telemetry Streaming Pre-Processor")
    parser.add_argument("--live", action="store_true", help="Connect to the live stream (not implemented yet)")
    parser.add_argument("--season", type=int, default=int(os.getenv("F1_SEASON", 2023)))
    parser.add_argument("--round", type=int, default=int(os.getenv("F1_ROUND", 1)))
    parser.add_argument("--session", type=str, default=os.getenv("F1_SESSION", "R"))
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Seconds to pause between laps (e.g. 0.2) so you can watch events arrive")

    args = parser.parse_args()
    start_ingestion(live_mode=args.live, season=args.season, round_num=args.round,
                    session=args.session, delay=args.delay)