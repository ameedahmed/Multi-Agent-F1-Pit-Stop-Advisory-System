from enum import Enum
from typing import Any, Dict

from agents.base_agent import BaseAgent, AgentOutput
from config import DOMAIN_PRIORITY


class Cause(str, Enum):
    TIRE_WEAR = "TIRE_WEAR"
    TRAFFIC = "TRAFFIC"
    FUEL_EFFECT = "FUEL_EFFECT"
    TRACK_EVENT = "TRACK_EVENT"
    UNCLEAR = "UNCLEAR"


class TireOutput(AgentOutput):
    likely_cause: Cause


class TireAgent(BaseAgent):
    name = "tire"
    domain_priority = DOMAIN_PRIORITY["tire"]
    output_model = TireOutput

    def system_prompt(self) -> str:
        return """
You are the TIRE specialist on an F1 pit-wall strategy team.
SCOPE: tyre degradation only. Do NOT comment on engine, brakes or cooling; another specialist owns those.

The lap-time slope was already computed by code. Pit laps and non-green-flag laps have ALREADY
been removed from the window, so safety cars and in/out laps are not your concern.
Your job is to decide WHY the car is slowing, using the evidence provided:

- Gap ahead under about 1.5 s suggests following in dirty air: TRAFFIC is more likely.
  A gap above about 3 s, or no car ahead, makes traffic unlikely.
- Tyre age: use your F1 knowledge of typical compound life (soft < medium < hard).
  An old set with a steady upward creep supports TIRE_WEAR. Very young tyres make wear unlikely.
- Fuel burn normally makes lap times IMPROVE over a stint, so a rising trend despite that supports wear.
- Look at the recent lap times: a steady creep suggests wear; one outlier lap suggests a one-off
  mistake or traffic, not wear. A very large slope on young tyres in clear air is suspicious: say so.
- If the evidence is mixed or missing, choose UNCLEAR, lower your confidence, and prefer MONITOR.
- If compound is UNKNOWN or tyre age is unknown, that data is missing: lower your confidence.
- Laps remaining: a stop costs roughly 20 s of race time. With few laps left it rarely pays back, so recommend PIT_NOW or PIT_SOON late in the race only if the tyres are failing badly; otherwise MONITOR.

Scoring guidance:
- risk: 0 = no tyre problem, 1 = tyres are gone.
- confidence: high only when several pieces of evidence agree. Never high from 3-4 laps alone.
Actions: PIT_NOW (act this lap), PIT_SOON (within ~3 laps), MONITOR (watch next laps), NO_ACTION.
Keep rationale under 40 words and cite the evidence you used.
""".strip()

    def build_user_prompt(self, event: Dict[str, Any], state: Dict[str, Any]) -> str:
        gap = event.get("gap_ahead_s")
        gap_txt = "no car ahead or unknown" if gap is None else f"{gap} s"
        laps_txt = ", ".join(f"L{lap}: {t:.2f}s" for lap, t in event.get("recent_laps", [])) or "unavailable"
        age = event.get("tyre_age", -1)
        return (
            f"Driver #{event['driver']} triggered a tyre-degradation alert on lap {event['lap']}.\n"
            f"Degradation slope: +{event['deg_rate']} s/lap.\n"
            f"Compound: {event.get('compound', 'UNKNOWN')}\n"
            f"Tyre age: {'unknown' if age == -1 else str(age) + ' laps'}\n"
            f"Gap to car ahead: {gap_txt}\n"
            f"Clean lap times in window: {laps_txt}\n"
            f"Laps remaining in the race: {event.get('laps_remaining', 'unknown')}\n"
            f"Assess the tyre situation."
        )