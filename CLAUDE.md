# poker-arena

Live Texas Hold'em agent for the dev.fun Poker Arena (Monad). `live_agent.py`
polls the arena API and plays hands; `strategy.py` holds the equity engine and
decision helpers.

## Read these before reviewing a run or proposing a strategy change

| File | What it is |
|---|---|
| `PRINCIPLES.md` | **Reasoning discipline.** How to reason about runs, evidence, and fixes. Check proposals against it the same way FINDINGS.md is checked for prior leaks. |
| `FINDINGS.md` | Leaks already discovered, with current fixed/unfixed status. |
| `DESIGN_BACKLOG.md` | Known issues held at design stage, deliberately unfixed. |

`PRINCIPLES.md` is the one that is easy to skip and shouldn't be — every entry
in it exists because skipping that check already cost chips at least once.

## Operational notes

- **Competition IDs expire.** `COMPETITION_ID` in `live_agent.py` is hardcoded
  and seasons turn over every few days. When joins return
  `competition_not_active`, get the current one from
  `GET /competition/list-active`. Chips left in an ended season are
  **permanently forfeit** — there is no settle or withdraw endpoint.
- **Run it with output redirected**, from bash rather than PowerShell
  (PowerShell's `>` writes UTF-16, which mangles the log):
  `py live_agent.py <seconds> > live_round_vN.log 2>&1`
  The argument is a wall-clock budget in seconds; there is no hand-count limit.
- **Only one instance at a time.** The arena enforces a 1-table concurrency
  cap, so two processes on the same API key fight over the same seat.
- **Hand outcomes are not in the log.** `live_agent.py` records only our own
  decisions. Join against `GET /agent/{id}/replays` (max 50, keyed by
  `tableId`) for chip deltas, or `GET /texas/recent-tables` for full hole cards
  and board runouts.

## Tests

```
py test_cumulative_aggression.py   # aggression accumulates, bets count, no reset
py test_velocity_brake.py          # brake fires on real busts, not on real winners
py test_range_strength.py          # equity discount direction + live wiring
py test_call_margin_floor.py       # never call below pot odds
py test_opponent_model.py          # opponent tracking
```

`test_strategy_v5.py` is slow (~20k trials, several minutes) and covers the
retired v5 strategy — not part of the normal loop.
