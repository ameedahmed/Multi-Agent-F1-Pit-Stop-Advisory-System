from agents.tire_agent import TireAgent

agent = TireAgent()
state = {"last_lap": "30", "latest_lap_time": "97.4"}

for deg in (0.02, 0.15, 0.40):
    r = agent.analyze({"driver": "1", "lap": 30, "deg_rate": deg}, state)
    print(f"\ndeg_rate={deg}")
    for k in ("risk", "confidence", "action", "likely_cause", "rationale", "error"):
        print(f"  {k}: {r.get(k)}")