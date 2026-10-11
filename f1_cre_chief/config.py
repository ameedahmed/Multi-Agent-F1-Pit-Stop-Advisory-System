TARGET_DRIVER  = None

LLM_PROVIDER = "anthropic"

CLAUDE_MODEL = "claude-sonnet-5-5"
GEMINI_MODEL = "gemini-3.8-flash"

LLM_MAX_OUTPUT_TOKENS = 400
LLM_MAX_ATTEMPTS = 3

# Claude Sonnet 5.5
# Prices are USD per million tokens (MTok).
CLAUDE_PRICE_IN_PER_MTOK = 2.00
CLAUDE_PRICE_OUT_PER_MTOK = 10.00

LLM_BUDGET_USD = 2.0
#Calibrate the following against real pit decision from historical data
DOMAIN_PRIORITY = {"tire":1.0,"thermal":0.0}
#SCORING ENGINE
#Below this total weight, evidence is 2 weak to act on
MIN_ACTIONABLE_SCORE = 0.1
#Decision will be flagged for human review if 2 agents disagree by more than 25%
DISAGREEMENT_MARGIN = 0.25
#If critical risk assumed by one agent exceeds 80% (given it is 50% confident), the decision by this agent overrides decision by other agents
CRITICAL_RISK = 0.9
CRITICAL_MIN_CONFIDENCE = 0.5
#define trigger
TRIGGER_EVENT = "THRESHOLD_EXCEEDED_TIRE_DEG"
AGENT_COOLDOWN_LAPS = 3 #do not reanalyse the same driver every lap
AGENT_CALL_PAUSE_S =1.0 #stay under API limit
# Illustrative thermal limits (project reference values, NOT official F1 figures)
THERMAL_LIMITS = {"engine_warn_c": 105, "engine_crit_c": 115,"brake_warn_c": 850, "brake_crit_c": 950}
RACE_LAPS = 57