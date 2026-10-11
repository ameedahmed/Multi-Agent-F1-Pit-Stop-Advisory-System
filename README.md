# Multi-Agent-F1-Pit-Stop-Advisory-System
<img width="2809" height="1144" alt="ArchitectureDiagram" src="https://github.com/user-attachments/assets/130849b4-8f6b-4acd-a0bc-1b7c4ccfa3a1" />

This is a multi-agent pit stop advisor for Formula 1 races. 

Somewhere in a Redis database, two small robots are arguing whether a pit-stop needs to be made or not.

The first is the Tyre Agent. It studies a car's lap times and asks: is this rubber truly worn out, or is the driver merely stuck behind? The second is the Thermal Agent, who frets about hot brakes. (Its temperatures are invented, due to non-availability of public data on a car's engine temp.)

When they disagree, a referee made entirely of arithmetic settles the matter and every recommendation is saved in Redis for future reference. 

## How it works ##
1. Plain maths watches every lap time and rings a bell when a car starts slowing beyond a certain threshold based on a 4 lap rolling window. 
2. This triggers an alert that is written to Redis along with the tyre age, the gap to the car ahead & the last few laps.
3. The tire & thermal agents read this data. Each agent gives an action, the reason behind it & a risk + confidence value associated with the recommendation.
4. The scoring engine weighs the recommendation of both agents using a mathematical formula & gives the final answer, which could be one of the following:

| Status | Meaning |
|---|---|
| ALIGNED | Everyone agreed |
| RESOLVED | They disagreed, but one side clearly won |
| CONFLICT | Too close to call, so flag for a human |
| DEGRADED | An agent failed, so it's not a full consensus |
| CRITICAL_OVERRIDE | One agent is very alarmed and proposes a pit stop, so it can't be outvoted |
| LOW_SIGNAL | Not enough evidence to bother anyone |

5. Everything is written back to redis, so any call can be replayed and decision judged against some baseline.

## Keeping inference costs down
The AI only wakes up when the car slows beyond a certain degree & a per driver cool-down avoids repeated alerts which might lead to fatigue.

## Limitations
- The thermal agent uses simulated data, since F1 doesn't publish engine temperatures
- Low recall, as many real stops are tactical or planned
- Historical replay only: no live mode or dashboard yet

## Still To Do:
1. Integrating a feedback loop so the agents can improve with every turn
2. A dashboard for user-friendly visibility

Here is how to run it:

```brew install redis && brew services start redis
python3 -m venv venv && source venv/bin/activate
pip install anthropic redis numpy pandas fastf1 pydantic

export ANTHROPIC_API_KEY="..."          # Insert your Agent API Key

redis-cli FLUSHDB
python3 preprocessor.py --season 2023 --round 1
python3 runner.py --max-analyses 3      # three analyses will cost u a few cents. Set max analyses to 0 for a full review.
```
Then compare the results with what really happened: 

```
python3 score_vs_pits.py
python3 baseline_rules.py
```

