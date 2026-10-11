"""
runner.py: connects alerts to agents to a decision.

WHAT: Reads alert events from the Redis stream, runs the specialist agents on each, scores
      their answers, and writes the assessments and the decision back to the blackboard.
WHY:  This is the loop from your architecture diagram. Writing results back as events keeps
      the append-only log a complete audit trail of who said what, and why.
HOW:  read stream -> cooldown check -> both agents -> resolve() -> publish -> repeat.
"""
import argparse
import time

from blackboard import RaceBlackboard
from agents.base_agent import usage_report, BudgetExceeded
from agents.tire_agent import TireAgent
from agents.thermal_agent import ThermalAgent
from agents.thermal_feed import simulated_thermal_reading
from scoring_engine import resolve
from config import TRIGGER_EVENT, AGENT_COOLDOWN_LAPS, AGENT_CALL_PAUSE_S, TARGET_DRIVER, RACE_LAPS

board = RaceBlackboard()
AGENTS = [TireAgent(), ThermalAgent()]    # adding a specialist later = one more entry here
LAST_ANALYZED = {}                        # driver -> lap of the last analysis (cost control)
LAST_ACTION = {}                          # driver -> last decision, so changes can be flagged

def handle_event(event: dict) -> bool:
    """Returns True if the agents ran (an API spend), False if the cooldown skipped it."""
    
    p = {**event["payload"], "laps_remaining": RACE_LAPS - event["payload"]["lap"]}
    driver, lap = p["driver"], p["lap"]

    if TARGET_DRIVER and driver!= TARGET_DRIVER:
        return False
    
    # Cooldown: a driver in a long slump alerts nearly every lap; analyzing each one would burn
    # API calls to say the same thing.
    if lap - LAST_ANALYZED.get(driver, -999) < AGENT_COOLDOWN_LAPS:
        return False
    LAST_ANALYZED[driver] = lap

    state = board.get_driver_state(driver)
    # Use the track temperature frozen into the EVENT, not the live hash (which may be from a later lap).
    state.update(simulated_thermal_reading(driver, lap, track_temp_c=p.get("track_temp_c")))

    assessments = []
    for agent in AGENTS:
        a = agent.analyze(p, state)
        a.update(driver=driver, lap=lap)
        board.publish_event("AGENT_ASSESSMENT", a)       # blackboard WRITE: the audit trail
        assessments.append(a)
        time.sleep(AGENT_CALL_PAUSE_S)                   # stay under API rate limits

    decision = resolve(assessments)                      # deterministic scoring
    decision.update(driver=driver, lap=lap)
    board.publish_event("SCORED_DECISION", decision)     # Phase 4 (the composer) will consume this
    board.update_driver_state(driver, {"last_decision": decision["action"],
                                       "decision_status": decision["status"]})

    votes = ", ".join(f"{a['agent']}={a['action']}" for a in assessments)
    gap = p.get("gap_ahead_s")
    gap_str = f"{gap:.1f}" if gap is not None else "none"
    print(f"#{driver} lap {lap:>2}: {decision['status']:12s} -> {decision['action']:9s} "
          f"{p.get('compound', '?'):6s} stint={p.get('stint', '?')} age={p.get('tyre_age', '?'):>2} "
          f"slope={p.get('deg_rate', 0):.3f} gap={gap_str:>5} | {votes}")

    # When the decision changes, show the tire agent's reasoning, so the change is explained.
    tire = next((a for a in assessments if a["agent"] == "tire"), None)
    prev = LAST_ACTION.get(driver)
    if tire and prev is not None and prev != decision["action"]:
        print(f"      CHANGED {prev} -> {decision['action']}. Tire agent: {tire['rationale']}")
    LAST_ACTION[driver] = decision["action"]
    return True


def latest_id() -> str:
    # Turns "$" ("only new entries") into a REAL id once. Re-using "$" inside a polling loop
    # would silently drop events that arrive between two polls.
    last = board.r.xrevrange("race_events", count=1)
    return last[0][0] if last else "0-0"


def main(start: str, max_analyses: int, follow: bool) -> None:
    last_id = latest_id() if start == "$" else start
    done = 0
    while True:
        events = board.read_events_after(last_id)
        if not events:
            if follow:
                continue                 # live mode: keep waiting for new alerts
            print(f"Stream exhausted. Analyses run: {done}")
            return                       # test mode: exit once everything has been read
        for event in events:
            last_id = event["event_id"]
            # Must filter: we also WRITE to this stream and must not react to our own output.
            if event["type"] != TRIGGER_EVENT:
                continue
            if handle_event(event):
                done += 1
                if max_analyses and done >= max_analyses:
                    print(f"Reached --max-analyses {max_analyses}. Stopping.")
                    return


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="0-0", help='"0-0" = replay from the beginning, "$" = only new alerts')
    ap.add_argument("--max-analyses", type=int, default=20,
                    help="stop after N real agent runs (default 3 protects your API quota; 0 = unlimited)")
    ap.add_argument("--follow", action="store_true", help="keep waiting for new alerts (live mode)")
    args = ap.parse_args()
    try:
        main(args.start, args.max_analyses, args.follow)
    except BudgetExceeded as e:
        print(f"STOPPED: {e}")
    finally:
        print(usage_report())          # printed on every exit path, so spend is never a surprise