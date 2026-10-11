"""
thermal_agent.py: the THERMAL specialist.

WHAT: Judges whether engine/brake temperatures change the pit decision.
WHY:  Gives the scoring engine a second, independent opinion. It owns a different domain from
      the tire agent, so the two don't duplicate each other's reasoning.
HOW:  Code converts raw temperatures into risk scores (deterministic). The LLM then decides the
      action, its own confidence, and a rationale, treating those scores as given facts.
"""
from typing import Any, Dict, Optional

from agents.base_agent import BaseAgent
from config import DOMAIN_PRIORITY, THERMAL_LIMITS as L


def _risk(value: float, warn: float, crit: float) -> float:
    """
    WHAT: Maps a temperature to a 0-1 risk: 0 when comfortably normal, 0.5 at the warning
          limit, 1.0 at the critical limit and beyond.
    WHY:  The same temperature must always produce the same risk. A prompt can't guarantee
          that; arithmetic can.
    HOW:  A piecewise-linear ramp in two segments:
            [92% of warn -> warn]  rises 0.0 -> 0.5
            [warn -> crit]         rises 0.5 -> 1.0
    """
    floor = 0.92 * warn                       # below this, the part is simply "normal"
    if value <= floor:
        return 0.0
    if value <= warn:
        return 0.5 * (value - floor) / (warn - floor)
    if value <= crit:
        return 0.5 + 0.5 * (value - warn) / (crit - warn)
    return 1.0                                # beyond critical is capped, never above 1


def _read(state: Dict[str, Any], key: str) -> Optional[float]:
    """Redis hands values back as strings, and a missing key is None or "". Convert defensively."""
    try:
        return float(state.get(key))
    except (TypeError, ValueError):
        return None                           # missing or unparseable means "no reading"


def compute_thermal_risks(state: Dict[str, Any]):
    """Returns (engine_risk, brake_risk). Either is None if that reading is missing."""
    engine = _read(state, "sim_engine_temp_c")
    brake = _read(state, "sim_brake_temp_c")
    return (
        None if engine is None else round(_risk(engine, L["engine_warn_c"], L["engine_crit_c"]), 3),
        None if brake is None else round(_risk(brake, L["brake_warn_c"], L["brake_crit_c"]), 3),
    )


def _fmt(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x:.2f}"


class ThermalAgent(BaseAgent):
    name = "thermal"
    domain_priority = DOMAIN_PRIORITY["thermal"]     # the weight comes from config, never from the LLM

    def system_prompt(self) -> str:
        # The limits are injected from config, so this prompt and the risk math can never disagree.
        return f"""
You are the THERMAL specialist on an F1 pit-wall strategy team.
SCOPE: power-unit and brake temperatures only. Do NOT judge tyre wear; another specialist owns that.

A tyre-degradation alert is active for this driver. Your question: do thermal constraints
change the pit decision? You are NOT asked to second-guess the tyre analysis.

The risk scores are computed by code. Treat them as facts; do not recompute them.
Reference limits (illustrative project values): engine warn {L['engine_warn_c']} C, critical {L['engine_crit_c']} C;
brake warn {L['brake_warn_c']} C, critical {L['brake_crit_c']} C.

How to decide:
- Both risks low (under about 0.3): thermal is no concern. Choose NO_ACTION with HIGH confidence.
  This is a useful answer: it tells the team thermal does not object.
- Either risk near 0.5 (at a warning limit): choose MONITOR.
- Either risk above about 0.8: a stop to address cooling is justified: PIT_SOON, or PIT_NOW if near 1.0.
- Set your risk field to the higher of the two computed risks.
- If one reading is missing, lower your confidence.
- If the data source starts with SIMULATED, say "simulated" in your rationale.
Actions: PIT_NOW, PIT_SOON, MONITOR, NO_ACTION. Keep rationale under 40 words and cite the numbers.
""".strip()

    def build_user_prompt(self, event: Dict[str, Any], state: Dict[str, Any]) -> str:
        engine_risk, brake_risk = compute_thermal_risks(state)   # cheap deterministic math, safe to repeat
        return (
            f"Driver #{event['driver']}, lap {event['lap']}. A tyre-degradation alert is active.\n"
            f"Track temperature: {state.get('track_temp_c')} C\n"
            f"Engine temp: {state.get('sim_engine_temp_c')} C (computed risk {_fmt(engine_risk)})\n"
            f"Brake temp: {state.get('sim_brake_temp_c')} C (computed risk {_fmt(brake_risk)})\n"
            f"Data source: {state.get('thermal_source', 'UNKNOWN')}\n"
            f"Do thermal constraints change the pit decision?"
        )

    def analyze(self, event: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        engine_risk, brake_risk = compute_thermal_risks(state)

        # Check preconditions BEFORE spending an API call. With no readings there is nothing to
        # judge, and a failure result excludes this agent from the vote instead of letting it guess.
        if engine_risk is None and brake_risk is None:
            return self._failure(ValueError("no thermal readings in driver state"))

        result = super().analyze(event, state)    # the shared Gemini call: retries, validation, clamping
        if result.get("error"):
            return result

        # Overwrite the numbers with the code-computed ones. The LLM supplies judgment
        # (action, confidence, rationale); code is the source of truth for arithmetic.
        known = [r for r in (engine_risk, brake_risk) if r is not None]
        result["engine_temp_risk"] = engine_risk
        result["brake_temp_risk"] = brake_risk
        result["risk"] = max(known)               # the worst component drives the overall risk
        return result