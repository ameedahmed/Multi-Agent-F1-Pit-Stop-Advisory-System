"""
scoring_engine.py: deterministic conflict resolution between the specialist agents.

WHAT: Takes every agent's assessment and returns ONE decision, plus a status saying how far
      to trust it (ALIGNED, RESOLVED, CONFLICT, DEGRADED, CRITICAL_OVERRIDE, LOW_SIGNAL, NO_SIGNAL).
WHY:  It makes no API calls and always gives the same output for the same input, so "who wins
      when agents disagree" is a rule you can test and argue about with numbers, not a prompt.
HOW:  weight = confidence x domain_priority x risk. Agents proposing the same action ADD their
      weights; the heaviest action wins; a narrow win is flagged for the Race Director.
      A decision reached without every specialist is labelled DEGRADED, never a clean consensus.
"""
from collections import defaultdict
from typing import Any, Dict, List

from config import (MIN_ACTIONABLE_SCORE, DISAGREEMENT_MARGIN,
                    CRITICAL_RISK, CRITICAL_MIN_CONFIDENCE)

PIT_ACTIONS = {"PIT_NOW", "PIT_SOON"}


def weight(a: Dict[str, Any]) -> float:
    # Your diagram's formula: Confidence x Domain Priority x Urgency (urgency = the agent's risk).
    # Note: an agent reporting "all clear" (risk ~0) therefore carries almost no weight.
    return a["confidence"] * a["domain_priority"] * a["risk"]


def _resolve_core(assessments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    WHAT: The voting logic itself, working only from the agents that returned valid answers.
    WHY:  Kept separate from resolve() so the voting rules and the "who was missing" bookkeeping
          can each be read and tested on their own.
    """
    # Failed agents (error=True) are excluded: no vote beats a guessed vote.
    valid = [a for a in assessments if not a.get("error")]
    if not valid:
        return {"status": "NO_SIGNAL", "action": "NO_ACTION", "scores": {}, "dissent": [], "margin": 0.0}

    # 1. SAFETY OVERRIDE: one confident, alarmed specialist proposing a pit stop cannot be outvoted.
    # Requiring a pit action prevents a nonsense override like "critical risk -> NO_ACTION".
    for a in valid:
        if (a["risk"] >= CRITICAL_RISK and a["confidence"] >= CRITICAL_MIN_CONFIDENCE
                and a["action"] in PIT_ACTIONS):
            return {"status": "CRITICAL_OVERRIDE", "action": a["action"], "scores": {},
                    "dissent": [], "margin": 1.0, "source": a["agent"]}

    # 2. Add up each action's total weight (agents proposing the same action reinforce each other).
    scores = defaultdict(float)
    for a in valid:
        scores[a["action"]] += weight(a)
    total = sum(scores.values())

    # 3. Too little evidence overall: don't escalate on noise.
    if total < MIN_ACTIONABLE_SCORE:
        return {"status": "LOW_SIGNAL", "action": "NO_ACTION", "scores": dict(scores),
                "dissent": [], "margin": 0.0}

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_action, top_score = ranked[0]
    # Dissent is kept, not discarded, so the Race Director can see who disagreed and why.
    dissent = [{"agent": a["agent"], "action": a["action"], "rationale": a["rationale"]}
               for a in valid if a["action"] != top_action]

    # 4. Everyone who voted proposed the same action: "Aligned -> Auto-merge" from your diagram.
    if len(ranked) == 1:
        return {"status": "ALIGNED", "action": top_action, "scores": dict(scores),
                "dissent": [], "margin": 1.0}

    # 5. Disagreement: a clear winner is RESOLVED; a narrow one is flagged for human review.
    margin = (top_score - ranked[1][1]) / total
    status = "RESOLVED" if margin >= DISAGREEMENT_MARGIN else "CONFLICT"
    return {"status": status, "action": top_action, "scores": dict(scores),
            "dissent": dissent, "margin": round(margin, 3)}


def resolve(assessments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    WHAT: The public entry point. Runs the voting logic, then records which agents were missing.
    WHY:  If thermal fails and only the tire agent votes, _resolve_core reports ALIGNED, because
          one voter always agrees with itself. That label would tell the Race Director two
          specialists agreed when only one spoke. A consensus without the full panel isn't one.
    HOW:  Downgrade ALIGNED and RESOLVED to DEGRADED whenever any agent failed. The other
          statuses are left alone, each for a reason:
            CRITICAL_OVERRIDE: a confident safety alarm shouldn't be softened because some other
                               agent happened to be unavailable.
            CONFLICT / LOW_SIGNAL / NO_SIGNAL: already cautious statuses; DEGRADED would add nothing.
    """
    decision = _resolve_core(assessments)
    failed = [a["agent"] for a in assessments if a.get("error")]
    decision["failed_agents"] = failed          # always present, so consumers never hit a missing key
    if failed and decision["status"] in ("ALIGNED", "RESOLVED"):
        decision["status"] = "DEGRADED"
    return decision