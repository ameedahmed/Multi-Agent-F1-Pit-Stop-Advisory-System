from blackboard import RaceBlackboard
from agents.tire_agent import TireAgent
from config import TRIGGER_EVENT

alerts = [e["payload"] for e in RaceBlackboard().read_latest_events(count=1000) 
          if e["type"] == TRIGGER_EVENT and e["payload"]["driver"] == "11" and e["payload"]["lap"] == 31]
if alerts:
    event = alerts[0]
    print(f"Slope: {event['deg_rate']} s/lap")
    print(f"Compound: {event['compound']}, age: {event['tyre_age']}")
    print(f"Gap: {event['gap_ahead_s']} s")
    print(f"Recent laps: {event['recent_laps']}")
    print(f"Track temp: {event['track_temp_c']} C")
    print("\nPrompt sent to the model:")
    print(TireAgent().build_user_prompt(event, {}))