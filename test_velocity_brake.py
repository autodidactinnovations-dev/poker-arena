"""Regression suite for the velocity brake (evaluate_velocity_brake).

Two halves, deliberately:

  SHOULD TRIGGER - the real bust hand cmtcarsk (2026-08-27), where commitment
  went 0.105 -> 0.516 -> 1.000 across two turn decisions at a flat 0.828 equity
  and both existing safeguards missed it.

  SHOULD NOT TRIGGER - real hands the agent actually WON. These are the whole
  point of the suite: a brake that only passes the losing-hand test is proven to
  fire, not proven to be calibrated. All three below are hands a flat 0.30 cap
  incorrectly fired on (+24, +5, +3 chips), taken from the 37 outcome-matched
  hands in the v8_2 session. Per-street thresholds must leave them alone.

Plus the check-not-fold case: the first draft of this brake folded on a `bet`
(where to_call == 0), throwing away all equity when checking was free.
"""
import sys
sys.path.insert(0, "C:/Users/billy/poker-arena")
import live_agent as LA

failures = []
BB = 2
DEEP = 20 * BB * 10  # comfortably past the short-stack guard


def ev(street, delta, equity, agg=0.0, action="raise", to_call=10,
       total=DEEP, bb=BB, legal=("fold", "call", "check", "bet", "raise"),
       made=None, pot=None):
    """`agg` is cumulative aggression (strategy.cumulative_aggression), which
    replaced the per-street opp_raises counter as the brake's tightening input.
    Reference scores: one preflop raise 1.0; a flop bet 1.0; a flop raise 2.0;
    a turn raise 3.0; the cmtfd8a3 three-step turn war 9.5."""
    return LA.evaluate_velocity_brake(street, delta, equity, agg, action,
                                       to_call, total, bb, lambda n: n in legal,
                                       made_category=made, pot=pot)


# --- SHOULD TRIGGER: the cmtcarsk bust ---
v, d = ev("Turn", 0.411, 0.828, agg=4.0, action="raise", to_call=30)
print(f"cmtcarsk turn raise (jump 0.411, eq 0.828) -> {v} {d}")
if v is None:
    failures.append("cmtcarsk turn raise should trigger the brake")

v, d = ev("Turn", 0.484, 0.828, agg=7.0, action="call", to_call=505)
print(f"cmtcarsk fatal turn call (jump 0.484, eq 0.828) -> {v} {d}")
if v is None:
    failures.append("cmtcarsk fatal turn call should trigger the brake")

# --- SHOULD NOT TRIGGER: real hands the agent won ---
won_cases = [
    # (label, street, jump, equity, action, to_call, chips_won)
    ("flop raise, won +24", "Flop", 0.311, 0.868, "raise", 12, 24),
    ("preflop raise, won +5", "Preflop", 0.308, 0.645, "raise", 5, 5),
    ("preflop raise, won +3", "Preflop", 0.320, 0.760, "raise", 5, 3),
]
for label, street, jump, equity, action, to_call, won in won_cases:
    v, d = ev(street, jump, equity, agg=1.0, action=action, to_call=to_call)
    print(f"WON HAND {label} (jump {jump}, eq {equity}) -> {v} {d}")
    if v is not None:
        failures.append(f"false positive: brake fired on a hand the agent won (+{won}): {label}")

# --- No equity override: a huge jump is braked at ANY equity reading ---
# Reversed from the original version of this test, which asserted that 0.97
# equity should bypass the brake. Hand cmtd2ab0 disproved that: equity read
# 0.954 on trip aces with a five kicker, drawing dead, and the override let
# 0.807 of the bankroll through. High equity is not evidence here.
v, d = ev("River", 0.60, 0.97, action="raise", to_call=50)
print(f"river shove at 'near-nuts' 0.97 (jump 0.60) -> {v} {d}")
if v is None:
    failures.append("brake must fire on a 0.60 jump even at 0.97 equity (no override)")

# The exact cmtd2ab0 shape: flop, 3 opponent raises, equity 0.954, jump 0.807.
v, d = ev("Flop", 0.807, 0.954, agg=7.0, action="call", to_call=793)
print(f"cmtd2ab0 fatal flop call (jump 0.807, eq 0.954, 3 raises) -> {v} {d}")
if v is None:
    failures.append("REGRESSION: cmtd2ab0's fatal call must now be braked")

# --- Short-stack guard: never brake when we are too short to fold profitably ---
v, d = ev("River", 0.90, 0.50, action="call", to_call=2, total=10, bb=BB)
print(f"short stack (total 10 = 5bb, jump 0.90) -> {v} {d}")
if v is not None:
    failures.append("short-stack guard should suppress the brake entirely")

# --- check-not-fold: betting with nobody in front of us must never fold ---
v, d = ev("River", 0.50, 0.60, action="bet", to_call=0,
          legal=("fold", "check", "bet", "raise"))
print(f"river bet with to_call=0 (jump 0.50) -> {v} {d}")
if v == "fold":
    failures.append("BUG: brake folded a bet when checking was free")
if v != "check":
    failures.append(f"expected 'check' when to_call==0 and check is legal, got {v}")

# --- raise-count signal tightens the cap independently of range_strength ---
v_no, d_no = ev("Turn", 0.22, 0.70, agg=0.0)
v_yes, d_yes = ev("Turn", 0.22, 0.70, agg=6.0)
print(f"jump 0.22 turn: agg=0 -> {v_no} (cap {d_no.get('cap')}) | "
      f"agg=6 -> {v_yes} (cap {d_yes.get('cap')})")
if v_no is not None:
    failures.append("0.22 jump under the 0.25 turn cap should pass with no aggression")
if v_yes is None:
    failures.append("sustained aggression should tighten the cap enough to fire at 0.22")

# --- cmtfd8a3: the hand this rescaling exists for. Flat -0.05 tightening let
# a 0.184 jump through against a 0.20 cap while aggression was 9.5. ---
v, d = ev("Turn", 0.184, 0.616, agg=9.5, action="call", to_call=250)
print(f"cmtfd8a3 fatal turn call (jump 0.184, agg 9.5) -> {v} {d}")
if v is None:
    failures.append("REGRESSION: cmtfd8a3's fatal call must trip the rescaled brake")

# --- Monotonicity: more aggression must never LOOSEN the cap ---
caps = [LA.velocity_cap_for("Turn", a) for a in (0, 1, 2, 4, 6, 9.5, 15)]
print("turn cap by aggression 0,1,2,4,6,9.5,15:", [round(c, 3) for c in caps])
if any(caps[i + 1] > caps[i] for i in range(len(caps) - 1)):
    failures.append(f"cap must be non-increasing in aggression: {caps}")


# --- Made-hand exemption (cmtg76xo): a completed flush is a deterministic
# fact, not a modeled estimate, and must never be braked. ---
import strategy as S
_board = [S._parse_card(c) for c in ["7h", "5h", "4s", "4h"]]
_cat = S._best_hand_rank([S._parse_card(c) for c in ["Ah", "2h"]] + _board)[0]
v, d = ev("Turn", 0.803, 0.875, agg=5.0, action="raise", to_call=200, made=_cat)
print(f"cmtg76xo made flush (cat {_cat}, jump 0.803, agg 5.0) -> {v} {d}")
if _cat != 5:
    failures.append(f"test setup: Ah2h on 7h5h4s4h should be a flush (cat 5), got {_cat}")
if v is not None:
    failures.append(f"REGRESSION: a made flush must be exempt from the brake, got {v}")

# cmtd2ab0: trip aces, bad kicker (category 3) - NOT exempt, must still brake.
_b2 = [S._parse_card(c) for c in ["Ad", "Ah", "2c", "Qd", "8s"]]
_cat2 = S._best_hand_rank([S._parse_card(c) for c in ["Ac", "5s"]] + _b2)[0]
v, d = ev("Flop", 0.807, 0.954, agg=7.0, action="call", to_call=793, made=_cat2)
print(f"cmtd2ab0 trips (cat {_cat2}, jump 0.807) -> {v} {d}")
if _cat2 >= LA.STACK_VELOCITY_MADE_HAND_EXEMPT:
    failures.append(f"test setup: trips should be below the exemption bar, got {_cat2}")
if v is None:
    failures.append("cmtd2ab0 trips must STILL be braked - it was a -1000 loser")

# cmtg5d71: pair + open-ended draw (category 1) - unchanged, still folds.
_b3 = [S._parse_card(c) for c in ["Ks", "8s", "7d", "9d"]]
_cat3 = S._best_hand_rank([S._parse_card(c) for c in ["Th", "Kd"]] + _b3)[0]
v, d = ev("Turn", 0.244, 0.664, agg=4.75, action="call", to_call=128, made=_cat3)
print(f"cmtg5d71 pair+draw (cat {_cat3}, jump 0.244) -> {v} {d}")
if v is None:
    failures.append("cmtg5d71 should be UNCHANGED (still braked) - draws are not exempt")

# cmtgk7w6: made QUEEN-HIGH STRAIGHT (8c Jh on 7s Tc Qd 9c), folded on the
# river at 0.962 equity for -352. Category 4 sat one below the original bar of
# 5 - an untested gap, since the validating cases were a flush and trips with
# no straight between them.
_b4 = [S._parse_card(c) for c in ["7s", "Tc", "Qd", "9c"]]
_cat4 = S._best_hand_rank([S._parse_card(c) for c in ["8c", "Jh"]] + _b4)[0]
v, d = ev("Turn", 0.20, 0.903, agg=5.5, action="raise", to_call=150, made=_cat4)
print(f"cmtgk7w6 made straight (cat {_cat4}, turn) -> {v} {d}")
if _cat4 != 4:
    failures.append(f"test setup: 8cJh on 7sTcQd9c should be a straight (cat 4), got {_cat4}")
if v is not None:
    failures.append(f"REGRESSION: a made straight must be exempt from the brake, got {v}")
# and on the river, where it was actually folded
_b5 = [S._parse_card(c) for c in ["7s", "Tc", "Qd", "9c", "2h"]]
_cat5 = S._best_hand_rank([S._parse_card(c) for c in ["8c", "Jh"]] + _b5)[0]
v, d = ev("River", 0.40, 0.962, agg=8.0, action="call", to_call=200, made=_cat5)
print(f"cmtgk7w6 made straight (cat {_cat5}, river) -> {v} {d}")
if v is not None:
    failures.append(f"REGRESSION: made straight on the river must be exempt, got {v}")

# Invariant: no aggression or jump may ever brake a straight-or-better.
for cat in (4, 5, 6, 7, 8):
    for agg in (0.0, 5.0, 20.0):
        for jump in (0.3, 0.9, 1.0):
            vv, _ = ev("River", jump, 0.50, agg=agg, action="call", to_call=500, made=cat)
            if vv is not None:
                failures.append(f"invariant broken: cat {cat} braked at agg {agg} jump {jump}")
print("invariant: straight+ (cat 4-8) never braked at any aggression/jump -> checked")

# --- POT-ODDS EXEMPTION (added 2026-09-03 after v33) ---------------------
# Real decisions, real numbers, taken from the logs. Two of these assert the
# UNCOMFORTABLE result: hands the brake arguably should not have folded, that
# it must nevertheless keep folding, because no threshold separates them from
# the busts the brake exists to catch. A suite that only encoded the cases
# where the fix looks good would be marketing, not verification.

# cmtktqkt (v33): trips eights, Qc8h on 3d4d8s8c. Pot 5992, to_call 308 ->
# pot odds 0.049 (19.5:1). The brake folded this and handed over ~6000 chips.
# Needs 4.9% equity; trips holds far more than that against any range, so the
# equity estimate cannot be wrong enough to matter. MUST be exempt.
v, d = ev("Turn", 0.858, 0.955, agg=6.0, action="call", to_call=308, made=3, pot=5992)
print(f"cmtktqkt trips at 19.5:1 (po 0.049) -> {v} {d}")
if v is not None:
    failures.append(f"REGRESSION: trips at 19.5:1 must be exempt on pot odds, got {v}")
if d.get("skipped") != "pot_odds":
    failures.append(f"cmtktqkt should skip via pot_odds, got skipped={d.get('skipped')}")

# cmtcarsk fatal turn call: pot odds 0.209, equity 0.828, opp_raises 1. This is
# the bust the whole brake was built for. It MUST still fire. Note it is
# CHEAPER than cmtkwn8t below - which is exactly why no loose pot-odds bar can
# be used to exempt cmtkwn8t.
v, d = ev("Turn", 0.484, 0.828, agg=7.0, action="call", to_call=933, made=2,
          pot=3530)  # to_call/(pot+to_call) = 0.209
print(f"cmtcarsk fatal call (po {d.get('pot_odds')}) -> {v} {d}")
if v is None:
    failures.append("REGRESSION: the pot-odds exemption un-braked the cmtcarsk bust")

# cmtd2ab0: trips, equity 0.954, pot odds 0.397, opponent's THIRD raise on the
# street. Cost -1000. Not a pot-odds error, and must keep folding.
v, d = ev("Flop", 0.807, 0.954, agg=9.0, action="call", to_call=793, made=3,
          pot=1204)  # to_call/(pot+to_call) = 0.397
print(f"cmtd2ab0 -1000 trips (po {d.get('pot_odds')}) -> {v} {d}")
if v is None:
    failures.append("REGRESSION: the pot-odds exemption un-braked cmtd2ab0 (-1000)")

# cmtkwn8t (v33): two pair, AdQh on 4s4c6hQc. Pot 239, to_call 94 -> pot odds
# 0.282. The brake FOLDED this, costing -69, and we accepted that as permanent:
# exempting it needs a pot-odds bar >= 0.282, which also exempts cmtcarsk's
# fatal call at 0.209. On the pot-odds axis the two really cannot be separated
# - cmtkwn8t is the MORE expensive call of the pair.
#
# The raise-fallthrough fix separates them anyway, on an axis nobody had
# examined: the ACTION TYPE.
#
#     cmtkwn8t   action="raise", to_call  94   refused RAISE -> call
#     cmtcarsk   action="call",  to_call 933   refused CALL  -> fold
#
# A refused raise falls through to call, because calling is strictly cheaper
# than the raise just refused. A refused call has no cheaper action to fall
# through to, so it still folds. That needs no pot-odds bar and no trust in the
# equity estimate, which is why it does not re-open the bust.
#
# So cmtkwn8t now CALLS and the "accepted cost" is recovered. Worth remembering
# as a case where a sound proof that two hands were inseparable was confined to
# the one property being measured.
v, d = ev("Turn", 0.758, 0.861, agg=5.0, action="raise", to_call=94, made=2, pot=239)
print(f"cmtkwn8t two pair at 2.5:1 (po {d.get('pot_odds')}) -> {v} {d}  <- raise refused, now CALLS not folds")
if v is None:
    failures.append("cmtkwn8t's RAISE must still be refused - downgraded, not "
                    "waved through")
if v != "call":
    failures.append(f"cmtkwn8t should now fall through to call, got {v}")
if d.get("fallthrough") != "call_over_cap":
    failures.append("cmtkwn8t should reach call via the over-cap fallthrough, "
                    f"got {d.get('fallthrough')}")

# Scope check: the exemption is for MADE hands only. Same cheap price, no made
# hand -> the 'any made hand holds 12%' argument does not apply, so no skip.
v, d = ev("Turn", 0.858, 0.955, agg=6.0, action="call", to_call=308, made=0, pot=5992)
print(f"draw/air at 19.5:1 (made=0) -> {v} {d}")
if v is None:
    failures.append("pot-odds exemption must not extend past made hands (made=0)")

# Boundary: exactly at the threshold is exempt, a hair above is not.
v_at, _ = ev("Turn", 0.9, 0.90, agg=6.0, action="call", to_call=12, made=2, pot=88)
v_over, _ = ev("Turn", 0.9, 0.90, agg=6.0, action="call", to_call=13, made=2, pot=87)
print(f"boundary: po=0.120 -> {v_at} | po=0.130 -> {v_over}")
if v_at is not None:
    failures.append("boundary: po exactly at 0.12 should be exempt")
if v_over is None:
    failures.append("boundary: po above 0.12 must not be exempt")

# The ratio guard: cheap price but we think we are behind -> no exemption.
# (equity must be under 3 x 0.049 = 0.147 for this guard to bind; the first
# draft of this case used 0.20, which clears it - the code was right and the
# test was wrong.)
v, d = ev("Turn", 0.858, 0.10, agg=6.0, action="call", to_call=308, made=2, pot=5992)
print(f"cheap price but equity 0.10 (< 3x po) -> {v} {d}")
if v is None:
    failures.append("ratio guard: equity below 3x pot odds must not be exempt")

# Back-compat: callers that pass no pot get the old behaviour exactly.
v_nopot, _ = ev("Turn", 0.858, 0.955, agg=6.0, action="call", to_call=308, made=3)
if v_nopot is None:
    failures.append("without pot, the brake must behave as before (no exemption)")
print(f"no pot passed -> {v_nopot} (unchanged legacy behaviour)")


print()
if failures:
    for f in failures:
        print("FAIL:", f)
    sys.exit(1)
print("PASS: catches the real bust, fires on zero hands the agent actually won, "
      "respects near-nuts and short-stack exemptions, never folds a free check")
