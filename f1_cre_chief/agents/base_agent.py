"""
base_agent.py: the shared contract for every specialist agent.

WHAT: Owns everything agents have in common: calling an LLM, forcing structured output,
      validating and clamping the result, retrying transient errors, and tracking spend.
WHY:  Subclasses (tire, thermal) supply only prompts. This is the ONE file that touches an LLM
      SDK, so switching providers means editing this file and config.py, nothing else.
HOW:  analyze() -> provider-specific call -> validate -> clamp -> one uniform result dict.
"""
import os
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel

from config import (LLM_PROVIDER, GEMINI_MODEL, CLAUDE_MODEL, LLM_MAX_ATTEMPTS, LLM_MAX_OUTPUT_TOKENS,
                    CLAUDE_PRICE_IN_PER_MTOK, CLAUDE_PRICE_OUT_PER_MTOK, LLM_BUDGET_USD)


class Action(str, Enum):
    PIT_NOW = "PIT_NOW"
    PIT_SOON = "PIT_SOON"
    MONITOR = "MONITOR"
    NO_ACTION = "NO_ACTION"


class AgentOutput(BaseModel):
    # Fields EVERY agent must return. Subclasses add domain-specific fields.
    risk: float
    confidence: float
    action: Action
    rationale: str


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _is_transient(e: Exception) -> bool:
    """
    WHAT: True if the error is worth retrying (overload, rate limit, network blip).
    WHY:  Retrying a permanent error (bad key, wrong model name) only wastes time.
    HOW:  Works for both SDKs by reading whichever status attribute the error has.
          429 and 5xx (which includes 503 and 529 'overloaded') are transient.
    """
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    if isinstance(code, int) and (code == 429 or code >= 500):
        return True
    return type(e).__name__ in ("APIConnectionError", "APITimeoutError")


# ---- Spend tracking: process-wide, so every agent adds to the same total ----
USAGE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}


class BudgetExceeded(RuntimeError):
    pass


def usage_report() -> str:
    return (f"LLM usage: {USAGE['calls']} calls, {USAGE['input_tokens']} in / "
            f"{USAGE['output_tokens']} out tokens, est. cost ${USAGE['cost_usd']:.4f} "
            f"(budget ${LLM_BUDGET_USD:.2f})")


def _record_usage(in_tokens: int, out_tokens: int) -> None:
    USAGE["calls"] += 1
    USAGE["input_tokens"] += in_tokens
    USAGE["output_tokens"] += out_tokens
    if LLM_PROVIDER == "anthropic":     # only Claude is priced here; the Gemini path is not costed
        USAGE["cost_usd"] += (in_tokens * CLAUDE_PRICE_IN_PER_MTOK
                              + out_tokens * CLAUDE_PRICE_OUT_PER_MTOK) / 1_000_000


class BaseAgent(ABC):
    name: str = "base"
    domain_priority: float = 1.0               # set from config by each subclass, never by the LLM
    output_model: type[AgentOutput] = AgentOutput

    def __init__(self):
        if LLM_PROVIDER == "anthropic":
            import anthropic
            # max_retries=0: our own loop in analyze() does the retrying, so behaviour is the same
            # for both providers and retries don't multiply. Reads ANTHROPIC_API_KEY from the
            # environment and fails loudly at startup if it is missing.
            self.client = anthropic.Anthropic(max_retries=0)
        elif LLM_PROVIDER == "gemini":
            from google import genai
            self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        else:
            raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")

    @abstractmethod
    def system_prompt(self) -> str: ...

    @abstractmethod
    def build_user_prompt(self, event: Dict[str, Any], state: Dict[str, Any]) -> str: ...

    def _complete_claude(self, prompt: str):
        # .parse() turns our Pydantic class into a JSON schema, constrains the model to it,
        # then validates the reply and hands back a ready-made object.
        response = self.client.messages.parse(
            model=CLAUDE_MODEL,
            max_tokens=LLM_MAX_OUTPUT_TOKENS,
            system=self.system_prompt(),
            messages=[{"role": "user", "content": prompt}],
            output_format=self.output_model,
        )
        out = response.parsed_output
        if out is None:                        # e.g. a refusal, or output cut off at max_tokens
            raise ValueError(f"no structured output (stop_reason={response.stop_reason})")
        return out, response.usage.input_tokens, response.usage.output_tokens

    def _complete_gemini(self, prompt: str):
        from google.genai import types
        response = self.client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt(),
                response_mime_type="application/json",
                response_schema=self.output_model,
                temperature=0.2,
            ),
        )
        out = self.output_model.model_validate_json(response.text)
        usage = getattr(response, "usage_metadata", None)
        return (out, getattr(usage, "prompt_token_count", 0) or 0,
                getattr(usage, "candidates_token_count", 0) or 0)

    def analyze(self, event: Dict[str, Any], state: Dict[str, Any]) -> Dict[str, Any]:
        # Budget guard sits OUTSIDE the try block on purpose, so the broad except below can't
        # swallow it. Hitting the budget should stop the run, not become another quiet failure.
        if USAGE["cost_usd"] >= LLM_BUDGET_USD:
            raise BudgetExceeded(f"estimated spend ${USAGE['cost_usd']:.4f} reached the "
                                 f"${LLM_BUDGET_USD:.2f} budget in config.py")

        prompt = self.build_user_prompt(event, state)
        complete = self._complete_claude if LLM_PROVIDER == "anthropic" else self._complete_gemini

        for attempt in range(1, LLM_MAX_ATTEMPTS + 1):
            try:
                out, in_tokens, out_tokens = complete(prompt)
                _record_usage(in_tokens, out_tokens)
                break                                       # success: leave the retry loop
            except Exception as e:
                if _is_transient(e) and attempt < LLM_MAX_ATTEMPTS:
                    time.sleep(2 ** attempt)                # back off: 2 s, then 4 s
                    continue
                return self._failure(e)                     # permanent error, or out of attempts

        result = out.model_dump(mode="json")
        result["risk"] = _clamp(result["risk"])              # never trust LLM numbers
        result["confidence"] = _clamp(result["confidence"])
        result.update(agent=self.name, domain_priority=self.domain_priority, error=False)
        return result

    def _failure(self, err: Exception) -> Dict[str, Any]:
        # Same keys as a success, so downstream code never hits a missing key.
        # error=True is what lets resolve() exclude this agent from the vote.
        return {"agent": self.name, "domain_priority": self.domain_priority,
                "risk": 0.0, "confidence": 0.0, "action": "NO_ACTION",
                "rationale": f"Agent failed: {type(err).__name__}: {err}", "error": True}