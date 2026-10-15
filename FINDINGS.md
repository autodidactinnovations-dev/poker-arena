# Poker agent — reconnection + strategy review (2026-08-26)

> See `PRINCIPLES.md` for the reasoning discipline these findings were produced
> with, and `DESIGN_BACKLOG.md` for issues held at design stage.

## 1. Why it stopped

Nothing was broken. **Playground S15 ended.** Every join returned
`409 competition_not_active` (see `live_round_v7_31.log`).

Reconnected to **[Poker] Playground S16** (`cmt78jcn6c9a1ddft3z2to1y5`), the
free playground — not the tournament. `COMPETITION_ID` in `live_agent.py`
updated. Agent rejoined with a fresh 1000-chip stack and is playing hands.

Final S15 standing: rank 118, 603 chips of the 1000 it started with.

## 2. API contract: unchanged

Pulled the live schema (`GET /api/arena/__introspection`) and diffed it against
the cached `introspection.json` from last season.

- Endpoints: **70 before, 70 after. None added, none removed.**
- 16 endpoints show a textual diff; **all 16 are mojibake repairs**
  (mangled em-dashes and `USD₮0`). No shape changes.
- `/texas/pending-actions` and `/texas/action` — the two the agent actually
  depends on — are **byte-for-byte identical**.

No migration work was needed. One rule *did* change: S16 advertises free
rebuys on bust, which S15 promised but the API never honored (ticket
`cmt6zkvt9fgypxjsh5mas7dvk`). Worth re-verifying before relying on it.

## 3. Bug fixed in the reconnect path

`main()`'s join loop handled `max_concurrent_tables` but not `already_queued`,
so if anything had already put the agent in the lobby, it burned all 5 retries
and aborted without ever polling. Now treated as "we're in, go poll."

## 3b. Bug fixed (then re-fixed) in the rebuy path (2026-08-27)

No code path ever called `POST /texas/rebuy`. Diagnosed after a session ran
the stack down to 2 chips; the fix landed gated behind
`chip_state == "busted" and totalChips <= 0`, on the assumption that "busted"
meant exactly zero chips.

**That assumption was wrong, and the first fix shipped broken.** The very
next 4-hour run busted at `totalChips: 1` (a sub-big-blind remainder, not
zero) — `chipState` correctly read `"busted"`, but the extra `<= 0` guard
never matched, so the agent spent **3h44m of a 4-hour run** hammering plain
`/texas/join` into a permanent `insufficient_chips` 409 instead of ever
reaching the rebuy branch. 16 minutes of actual play, then silence, for the
whole session.

**Fix:** trust `chipState == "busted"` alone, exactly as the schema's own
description says to ("use this instead of inferring from
bankrollChips/totalChips") — no compound condition on top of it. Manually
verified against the live stuck agent before relaunching: `POST
/texas/rebuy` with a bare `competitionId` returned 200, credited a full 1000
chips for free, `rebuyCount` incremented, no payment involved.

Lesson for next time: when a schema field is explicitly documented as
authoritative, don't reinforce it with a second inferred condition — that's
exactly the surface where it can silently never fire.

---

# Where it leaks chips

Ranked by confidence. #1 is confirmed against arena ground truth.

## Leak 1 — it calls with equity BELOW pot odds *(fixed)*

**The single clearest -EV bug in the code.**

`call_margin` is the extra equity demanded on top of raw pot odds. It is built
from signed bumps, and `opponent_read_bump()` alone returns down to **-0.15**.
Nothing floored the total, so against a loose/bluffy opponent the margin went
**negative** — the agent required *less* equity than the pot was offering.

Caught live in S16, table 33225, with hole cards revealed by `/texas/recent-tables`:

| street | action | equity | pot odds | verdict |
|---|---|---|---|---|
| Preflop | call 5 | 0.198 | 0.278 | below pot odds |
| Flop | call 5 | 0.427 | 0.135 | fine |
| Turn | **call 92** | **0.215** | **0.261** | **below pot odds** |
| Turn | fold | 0.219 | 0.255 | escalation_bump finally fired |

Arithmetic: `looseness 0.17` gives `loose_bonus -0.023`;
`call_margin = 0.06 + 0.0115 + 0.0 - 0.15 = -0.0785`;
required equity `0.261 - 0.0785 = 0.1825`; actual `0.215` → **call**.

We held `Jd 3c` (one pair, bad kicker) on `7d Jc Qc Kd 5h`. Lost to a straight.
**102 chips in, 0 back**, on a hand where two of three calls were -EV by the
agent's own numbers.

**Fix:** clamp `call_margin` with `max(0.0, ...)` *after* all bumps, at both
call sites. The negative bump still usefully shrinks the margin toward zero —
zero is just the floor. This strategy models no implied odds and no fold
equity, so it has nothing to justify calling below pot odds with.

Regression test: `test_call_margin_floor.py`. Verified it **fails on the old
code and passes on the new** — a real guard, not a tautology.

## Leak 2 — Monte Carlo noise is as large as the decision margins *(fixed)*

`estimate_equity` ran 250 trials postflop / 150 preflop. Re-running the *same*
real spot 40 times:

| trials | stdev | spread |
|---|---|---|
| 250 (old) | 0.029 | 0.120 |
| 2000 (new) | 0.010 | 0.042 |

`call_margin` is 0.06 and threshold gaps are 0.04–0.10 — **the same scale as
the noise.** A meaningful share of borderline calls and folds were decided by
the RNG, not the hand. Worse, the estimate is re-rolled every poll, so the
agent can contradict itself on identical spots.

2000 trials costs **0.55s against a 30s action deadline**. Precision was
essentially free and was being left on the table. Now 2000 on every street.

## Leak 3 — the opponent model reads everyone as *tighter* than default

`_opp_profile` computes `looseness = min(1.0, s["vpip"] / 6.0)` where `vpip`
is a **raw count of voluntary preflop actions, never divided by hands played.**
It is a count masquerading as a rate.

Because `_OPPONENT_STATS` resets every ~7–20 minute process, the count almost
never gets past 1. Observed across all logged decisions:

```
96x  looseness 0.17   (= 1/6)
26x  looseness 0.40   (unseen-opponent default)
13x  looseness 0.00
 5x  looseness 0.08
 3x  looseness 0.33
```

**It never once exceeded 0.33.** The neutral default is 0.40, so on ~82% of
decisions the model reported opponents as *tighter than neutral* — purely as an
artifact of a short-lived counter. That makes `loose_bonus` persistently
negative, which **raises** `call_margin` and **raises** the open/isolate
thresholds.

Net effect: a systematic drift toward tight play — in direct contradiction to
this project's own documented finding that the top performers in this arena run
80–93% VPIP, and that v4's wide-calling shape beat v5's tight one. The agent
has been quietly re-introducing the v5 regression through a units bug.

*Not yet fixed — see recommendations.*

## Leak 4 — `aggression` is computed and never used

`live_agent.py:250` unpacks `looseness, aggression = S._opp_profile(...)`.
`aggression` appears exactly twice: that line, and the debug dict. **It feeds no
decision.** Half the "opponent-tendency-aware" design isn't wired to anything,
and the half that is wired is Leak 3.

## Leak 5 — both safeguards are street-scoped; the real losses are multi-street

- `escalation_bump` requires 2+ raises **on the same street**. It fired
  **1 time in 104 logged decisions (0.96%)**.
- The stack-risk cap measured `amount / (my_bet + stack)`, where `my_bet` is
  `currentBetChips` — which the schema documents as *per-street*, resetting to
  0 each street.

So a hand that calls a material bet on three consecutive streets, each well
under the 50% cap, could bleed most of a stack with neither safeguard firing.
That is the exact shape of both large recorded losses (v7_9 −193, v7_24 −249):
multi-street call-downs, `opp_raises_this_street` of 0 or 1 throughout.

**Partially fixed:** the cap now measures whole-hand commitment via
`totalCommittedChips`. This roughly doubles what it sees (v7_24 flop
0.163 → 0.304; v7_9 river 0.106 → 0.210), confirming the old denominator
materially under-counted.

**But it is not a fix for those two hands** — neither reaches the 0.5 cap even
corrected. The breaker now measures the right quantity; catching this shape
needs a lower whole-hand threshold (below).

## Leak 6 — equity is computed vs *random* hands, including vs. aggression *(partially fixed)*

`estimate_equity` deals opponents uniformly random cards. But an opponent who
bets, raises, and re-raises does **not** hold a random hand. The agent's equity
is therefore overstated precisely when it is facing strength — exactly when
being wrong is most expensive.

`escalation_bump` and `opp_bump` are hand-tuned patches over this hole. Table
33225 is the illustration: 0.215 equity vs *random* hands, against a field that
had a straight.

A second, distinct instance surfaced in S16 table `cmtb7bys` (2026-08-27),
worth recording separately because it's a **calling** range, not a raising
one — the recommendation below ("N tightening as raises rise") would not
have caught it. We held `Qc8s` (top pair, weak kicker) on `2s Jc Qd 3h 5c`
and barreled flop/turn/river at 76%/87%/84% modeled equity into a single
opponent who had simply **called** both prior streets, no raises anywhere in
the hand. They turned over `Qs Ts` — the same top pair, better kicker — for
**-83 chips**, 42% of that session's entire net loss on its own.

**Fix (`estimate_equity`'s new `range_strength` param, wired via
`live_agent.py`'s `_opponent_continuation_count`):** once an opponent has
voluntarily continued (called *or* raised) on a prior street this hand,
Monte Carlo trials reject-and-redraw away from that opponent whiffing
completely (no pair at all), with probability rising by how many streets
they've stuck around — 0 on a hand's first bet (no history yet), up to 0.7
after two called streets. Verified in `test_range_strength.py`: correctly
0 before any continuation exists, correctly ramps on the live decision path,
and measurably pulls the `cmtb7bys` board's equity down (0.848 → 0.797 at
range_strength 0.7 in that spot).

**Known gap — this does not fully solve the repro hand.** The bar is "at
least a pair," and the villain's actual hand *was* a pair (top pair, just a
better kicker) — same category as ours, so this coarse filter doesn't touch
it. 79.7% would still clear the river value-bet threshold. Closing that
needs a kicker-aware bar (e.g. weighting against opponent hands that beat or
tie our specific hand rank, not just against categorical air), which is a
sharper and riskier change — worth tuning against real hands rather than
shipping blind. Treat this fix as removing the cheapest, safest slice of the
overstatement (pure air), not as resolving thin-value river barrels in
general.

---

# Recommended next changes

Ordered by expected chips per unit of risk.

1. **Make `looseness` a real rate (fixes Leak 3).** Track hands observed per
   opponent and divide, or drop the in-process tracker for preflop-range
   purposes and read `vpip` straight off `/texas/agent-stats`, which is
   server-computed, cross-session, and already cached on disk. Until then
   consider pinning `looseness` to the 0.4 default, since the current signal is
   worse than no signal.

2. **Add a whole-hand commitment brake (completes Leak 5).** The 0.5 cap is
   too loose to ever engage. A second, lower threshold — e.g. require rising
   equity to keep calling once whole-hand commitment passes ~25–30% of stack —
   targets the multi-street call-down that caused both large losses. Tune
   against the two recorded hands, which now have correct fractions.

3. **Condition equity on observed aggression (Leak 6).** *Partially done* —
   `range_strength` now filters out complete air once an opponent has
   continued on a prior street (see Leak 6 above). Still open: a kicker-aware
   version that would have actually changed the `cmtb7bys` outcome, and
   folding `escalation_bump`/`opp_bump` into this mechanism instead of
   keeping them as separate hand-tuned patches over the same underlying gap.

4. **Wire up `aggression`, or delete it (Leak 4).** Live code shouldn't carry an
   unused decision input — it reads as implemented when it isn't.

5. **Widen preflop once Leak 3 is fixed.** With looseness corrected, revisit
   `open_thresh` 0.68 / `isolate_thresh` 0.66. Arena evidence favors wider.
   Change this *after* the model is trustworthy, and one knob at a time.

## A note on sample size

S15 was 41 hands and S16 is a few dozen so far. That is far too small to judge
strategy by results — a 100-chip swing is one hand. Leaks 1–5 are all
identified by **reading the decision logic against the arena's documented
schema and the agent's own logged numbers**, not by fitting to outcomes, which
is why they are actionable at this sample size. Leak 1 additionally has
hole-card ground truth. Treat any bb/100 comparison as noise until several
hundred hands are in.
